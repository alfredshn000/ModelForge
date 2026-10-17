"""贴图查找与解码，服务端用。

三级查找（和离线导出脚本一致的顺序，但更宽松）：

1. **精确相对路径**：模型目录 / 上级目录 / 整合包根
2. **同名文件**：按 basename 在搜索根里找（整合包常把贴图平铺在一个目录）
3. **游戏 CASC**：原路径查，`.tif` 自动回退 `.dds` / `.blp`

解出 DDS / BLP1 / PNG 后统一编码成 PNG 返回前端。同一张只解一次，
缓存在内存里（默认最多 300 张，够一屏用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading

from mdxcore import blp, dds
from mdxcore import paths as P
from mdxcore.casc import CascError

try:
    import brand
except Exception:                                   # noqa: BLE001
    brand = None

# 同一张贴图在不同用途下要不同尺寸：3D 先用 512 再升级到 1024，缩略图用 96
DEFAULT_SIZE = 512
MAX_CACHE = 300
RGBA_BUDGET = 192 * 1024 * 1024      # 解码后的像素很占内存：1024² RGBA 一张就 4 MB
PNG_BUDGET = 128 * 1024 * 1024

# 磁盘缓存：按"源文件哈希 + 尺寸"存 PNG。重开同一个模型不用再解一次 DXT
# （2048² 的 DXT5 解一次要 2 秒，12 张就是半分钟，第二次打开等不起）。
DISK_FILES_MAX = 1200
DISK_PRUNE = 300
_DISK_WRITES = 0


class TextureStore:
    def __init__(self, roots: list[pathlib.Path], casc_factory=None):
        self.roots = [pathlib.Path(r) for r in roots if r]
        self.casc_factory = casc_factory        # callable → Casc | None，延迟打开
        self._casc = None
        self._casc_tried = False
        self._by_name: dict[str, pathlib.Path] | list[pathlib.Path] | None = None
        self._index_lock = threading.Lock()
        self._casc_lock = threading.Lock()      # CASC 不能并发读，见 _find
        self._raw: dict[str, tuple] = {}        # 原始字节缓存：CASC 读一次要 0.3 s
        self._rgba: dict[str, tuple] = {}       # 解码结果缓存
        self._png: dict[tuple, bytes] = {}
        self._bytes = {"rgba": 0, "png": 0}     # 上面两个缓存的实际占用
        self._info: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 查找

    def _casc_obj(self):
        # 只在成功时记"试过了"：第一次因为游戏目录没挂上等原因失败，
        # 之后不该永久放弃——那样整个会话的贴图都会显示"找不到"。
        if self._casc is None and not self._casc_tried:
            try:
                self._casc = self.casc_factory() if self.casc_factory else None
            except Exception:                              # noqa: BLE001
                self._casc = None
            self._casc_tried = self._casc is not None
        return self._casc

    def _name_index(self) -> dict[str, list[pathlib.Path]]:
        """basename → 文件列表。懒建，只扫一次（加锁：多个请求会同时进来）。"""
        if self._by_name is not None:
            return self._by_name
        with self._index_lock:
            if self._by_name is not None:
                return self._by_name
            idx: dict[str, list[pathlib.Path]] = {}
            exts = {".dds", ".blp", ".png", ".tga", ".bmp"}
            for root in self.roots:
                if not root.is_dir():
                    continue
                for p in root.rglob("*"):
                    if p.suffix.lower() in exts and p.is_file():
                        idx.setdefault(p.name.lower(), []).append(p)
            self._by_name = idx
            return idx

    def find(self, tex_path: str, model_dir: pathlib.Path | None = None):
        """返回 (bytes, 描述) 或 (None, 原因)。结果按路径缓存。"""
        cache_key = P.norm(tex_path).lower()
        hit = self._raw.get(cache_key)
        if hit is not None:
            return hit

        rel = P.norm(tex_path).lstrip("\\")
        if not rel:
            return None, "路径为空"

        search = ([model_dir] if model_dir else []) + self.roots
        name = P.basename(rel).lower()
        stem = name.rsplit(".", 1)[0]

        # 1) 精确相对路径（含扩展名回退）
        for root in search:
            if not root or not root.is_dir():
                continue
            direct = root / rel.replace("\\", os.sep)
            for cand in [direct, direct.with_suffix(".dds"), direct.with_suffix(".tif"),
                         direct.with_suffix(".blp"), direct.with_suffix(".png")]:
                if cand.is_file():
                    data = cand.read_bytes()
                    self._cache_raw(cache_key, data, f"local:{cand.name}")
                    return data, f"local:{cand.name}"

        # 2) 同名文件（任意扩展名，先同扩展名）
        idx = self._name_index()
        for key, files in ((name, idx.get(name)),):
            if files:
                data = files[0].read_bytes()
                self._cache_raw(cache_key, data, f"local:{files[0].name}")
                return data, f"local:{files[0].name}"
        for alt in (".dds", ".blp", ".png", ".tif"):
            files = idx.get(stem + alt)
            if files:
                data = files[0].read_bytes()
                self._cache_raw(cache_key, data, f"local:{files[0].name}")
                return data, f"local:{files[0].name}"

        # 3) 游戏数据
        casc = self._casc_obj()
        if casc is not None:
            # CASC 内部有惰性加载的索引与 TVFS 表，多线程同时读会把状态打乱，
            # 表现是"明明有这张图却读不出来"。贴图请求是浏览器并发打进来的，
            # 所以这里必须串行。单张读取在毫秒级，串行不影响观感。
            broken = False
            with self._casc_lock:
                for cand in (tex_path, rel, stem + ".dds", stem + ".blp"):
                    try:
                        raw = casc.read(cand)
                    except CascError as exc:
                        if "找不到" in str(exc):
                            continue                    # 正常的"没有这个文件"
                        broken = True                   # 索引/数据块损坏
                        continue
                    except Exception:                   # noqa: BLE001
                        broken = True                   # 读取出错 ≠ 文件不存在
                        continue
                    if raw:
                        self._cache_raw(cache_key, raw, "casc")
                        return raw, "casc"

            if broken:
                # 不要把"读取出错"当成"没有"缓存下来——否则这张图整个会话都废了
                return None, "游戏数据读取异常（可重试）"
        else:
            # CASC 没连上时更不能缓存失败，否则接上游戏目录也不会恢复
            return None, "游戏数据未连接"

        self._cache_raw(cache_key, None, "本地与游戏数据都没有")
        return None, "本地与游戏数据都没有"

    def _cache_raw(self, key: str, raw: bytes | None, origin: str) -> None:
        with self._lock:
            if len(self._raw) >= 300:
                self._raw.pop(next(iter(self._raw)))
            self._raw[key] = (raw, origin)

    # ---------------------------------------------------------------- 解码

    def _decode(self, raw: bytes, size: int = 0):
        """DDS 走跳块解码，直接产出小图；BLP1 只能全解后再缩。"""
        magic = raw[:4]
        if magic == b"DDS ":
            return dds.decode(raw, size)
        if magic == b"BLP1":
            return blp.decode(raw)
        if magic == b"BLP2":
            raise ValueError("BLP2 暂不支持")
        raise ValueError(f"未知格式 {magic!r}")

    def png(self, tex_path: str, size: int = DEFAULT_SIZE,
            model_dir: pathlib.Path | None = None) -> tuple[bytes | None, dict]:
        """返回 (PNG 字节, 信息)。信息里有来源、原始尺寸、平均色。"""
        key = (P.norm(tex_path).lower(), size)
        with self._lock:
            if key in self._png:
                return self._png[key], self._info.get(key[0], {})

        raw, origin = self.find(tex_path, model_dir)
        if raw is None:
            info = {"origin": origin, "w": 0, "h": 0, "avg": None}
            with self._lock:
                self._info[P.norm(tex_path).lower()] = info
            return None, info

        # 磁盘缓存命中就直接发，省掉解码与 PNG 编码（2048² 要 2.4 秒）
        disk = self._disk_get(raw, size)
        if disk is not None:
            payload, info = disk
            info = dict(info, origin=origin)
            with self._lock:
                if len(self._png) >= MAX_CACHE:
                    self._png.pop(next(iter(self._png)))
                self._png[key] = payload
                self._info[P.norm(tex_path).lower()] = info
            return payload, info

        # 同一张贴图会被请求多次（缩略图 96 / 3D 512 / 升级 1024），按尺寸分别缓存
        key_rgba = (P.norm(tex_path).lower(), size)
        cached = self._rgba.get(key_rgba)
        if cached is not None:
            w, h, rgba = cached
        else:
            try:
                w, h, rgba = self._decode(raw, size)
            except Exception as exc:                      # noqa: BLE001
                info = {"origin": f"{origin} · {exc}", "w": 0, "h": 0, "avg": None}
                return None, info
            self._put_rgba(key_rgba, (w, h, rgba))

        # DDS 跳块后已经接近目标尺寸，只有还超标时才真正缩放（BLP1 走这里）
        if max(w, h) > size * 1.05:
            nw, nh, small = dds.downscale(w, h, rgba, size)
        else:
            nw, nh, small = w, h, rgba
        payload = dds.encode_png(nw, nh, small)
        info = {"origin": origin, "w": w, "h": h,
                "avg": list(dds.average_color(nw, nh, small)), "png": [nw, nh]}

        self._put_png(key, payload)
        with self._lock:
            self._info[P.norm(tex_path).lower()] = info
        self._disk_put(raw, size, payload, info)
        return payload, info

    # ---------------------------------------------------------------- 磁盘缓存

    @staticmethod
    def _disk_root() -> pathlib.Path | None:
        if brand is None:
            return None
        try:
            d = brand.cache_dir() / "tex"
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:                              # noqa: BLE001
            return None

    def _disk_path(self, raw: bytes, size: int) -> pathlib.Path | None:
        root = self._disk_root()
        if root is None:
            return None
        digest = hashlib.sha1(raw).hexdigest()[:20]
        return root / f"{digest}-{size}.png"

    def _disk_get(self, raw: bytes, size: int):
        p = self._disk_path(raw, size)
        if p is None or not p.is_file():
            return None
        try:
            payload = p.read_bytes()
            meta = p.with_suffix(".json")
            info = json.loads(meta.read_text("utf-8")) if meta.is_file() else {}
            if not info:
                return None
            return payload, info
        except Exception:                              # noqa: BLE001
            return None

    def _disk_put(self, raw: bytes, size: int, payload: bytes, info: dict) -> None:
        global _DISK_WRITES
        p = self._disk_path(raw, size)
        if p is None:
            return
        try:
            p.write_bytes(payload)
            p.with_suffix(".json").write_text(json.dumps(info), "utf-8")
        except Exception:                              # noqa: BLE001
            return
        _DISK_WRITES += 1
        if _DISK_WRITES % 200 == 0:
            self._disk_prune()

    def _disk_prune(self) -> None:
        root = self._disk_root()
        if root is None:
            return
        try:
            files = sorted(root.glob("*.png"), key=lambda f: f.stat().st_mtime)
            if len(files) <= DISK_FILES_MAX:
                return
            for f in files[:DISK_PRUNE]:
                f.unlink(missing_ok=True)
                f.with_suffix(".json").unlink(missing_ok=True)
        except Exception:                              # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 缓存
    #
    # 按字节数淘汰而不是按条数：一张 96 的缩略图和一张 2048 的贴图差三个数量级，
    # 用条数限制会出现"缓存 200 张却吃掉 800 MB"的情况。

    def _put_rgba(self, key, value) -> None:
        with self._lock:
            old = self._rgba.get(key)
            if old is not None:
                self._bytes["rgba"] -= len(old[2])
            self._bytes["rgba"] += len(value[2])
            self._rgba[key] = value
            while self._bytes["rgba"] > RGBA_BUDGET and len(self._rgba) > 1:
                k = next(iter(self._rgba))
                if k == key:                # 刚放进去的就是最老的，说明它自己就超预算
                    break
                self._bytes["rgba"] -= len(self._rgba.pop(k)[2])

    def _put_png(self, key, payload: bytes) -> None:
        old = self._png.get(key)
        if old is not None:
            self._bytes["png"] -= len(old)
        self._bytes["png"] += len(payload)
        self._png[key] = payload
        while (self._bytes["png"] > PNG_BUDGET or len(self._png) > MAX_CACHE) \
                and len(self._png) > 1:
            k = next(iter(self._png))
            if k == key:
                break
            self._bytes["png"] -= len(self._png.pop(k))

    def probe(self, tex_path: str, model_dir=None) -> dict:
        """只看找不找得到，不解码（列表里批量用，快）。"""
        key = P.norm(tex_path).lower()
        with self._lock:
            hit = self._info.get(key)
        if hit is not None:
            return hit
        raw, origin = self.find(tex_path, model_dir)
        info = {"origin": origin, "w": 0, "h": 0, "avg": None} if raw is None else \
               {"origin": origin, "w": 0, "h": 0, "avg": None, "found": True}
        with self._lock:
            self._info[key] = info
        return info
