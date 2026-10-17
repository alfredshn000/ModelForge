"""CASC 只读入口。

用一条路径换出文件字节，或者列出全部文件路径。

打开顺序：
    1. .build.info          → Build Key（config 文件名）
    2. Data/config/xx/yy/<buildKey>  → root CKey、encoding EKey
    3. Data/data/*.idx       → ekey → (data.NNN 编号, 偏移, 长度)
    4. Data/data/data.NNN    → BLTE 解码
    5. encoding 的 EKey → TVFS → 路径表

注意：Reforged 的贴图路径有别名，见 filealiases.json；再叠加 6 个虚拟前缀目录，
这就是为什么模型里写的 "Units\\Human\\Footman\\Footman.blp" 能查到实际文件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import blte, indexfile, tvfs


class CascError(RuntimeError):
    """CASC 打不开或读不到。"""


@dataclass
class BuildInfo:
    branch: str
    active: int
    build_key: str
    cdn_key: str
    tags: str
    version: str
    product: str


def _parse_build_info(path: Path) -> BuildInfo:
    raw = path.read_bytes().decode("utf-8", "replace").replace("\x00", "")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise CascError(".build.info 内容为空")

    names = [c.split("!")[0].strip() for c in lines[0].split("|")]
    rows = [ln.split("|") for ln in lines[1:]]

    # 多行时取 Active!DEC == 1 的那行
    chosen = rows[0]
    if "Active" in names:
        idx = names.index("Active")
        for row in rows:
            try:
                if int(row[idx]) == 1:
                    chosen = row
                    break
            except (ValueError, IndexError):
                continue

    def get(name: str, default: str = "") -> str:
        if name not in names:
            return default
        i = names.index(name)
        return chosen[i].strip() if i < len(chosen) else default

    return BuildInfo(
        branch=get("Branch"),
        active=int(get("Active", "1") or 1),
        build_key=get("Build Key"),
        cdn_key=get("CDN Key"),
        tags=get("Tags"),
        version=get("Version"),
        product=get("Product"),
    )


def _parse_config(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.split()
    return out


def _bucket_of(ekey: bytes) -> int:
    acc = 0
    for b in ekey:
        acc ^= b
    return (acc & 0xF) ^ ((acc >> 4) & 0xF)


class Casc:
    """一个只读的 WC3 Reforged 数据源。"""

    # 虚拟目录前缀，顺序取自 Retera 的 Cascket
    PREFIXES = ("war3.mpq", "deprecated.mpq", "war3.w3mod", "deprecated.w3mod")

    def __init__(self, install_dir: str | Path, locale: str = "enUS"):
        self.root = Path(install_dir)
        info_path = self.root / ".build.info"
        if not info_path.is_file():
            raise CascError(f"找不到 .build.info：{info_path}")

        self.build = _parse_build_info(info_path)
        self.data_dir = self.root / "Data"
        if not self.data_dir.is_dir():
            raise CascError(f"找不到 Data 目录：{self.data_dir}")

        config_path = self._config_path(self.build.build_key)
        if config_path is None:
            raise CascError(f"找不到 build 配置 {self.build.build_key}")
        self.config = _parse_config(config_path)

        self.locale = locale
        # 顺序按 Retera 的 Cascket：先经典版容器，再 Reforged 的 w3mod
        # HD 覆盖层排在经典层之前：Reforged 里 _hd.w3mod 是后挂载的覆盖层，
        # 同名文件以 HD 为准。人类/兽人/精灵单位与建筑几乎全在 _hd 里，
        # 经典层只有 creeps 等少量资产——漏掉这一层会把大量内置贴图判成"找不到"。
        self.prefixes = [
            "war3.mpq",
            "deprecated.mpq",
            f"{locale}-war3local.mpq",
            "war3.w3mod\\_hd.w3mod",
            f"war3.w3mod\\_locales\\{locale}.w3mod",
            "war3.w3mod",
            "deprecated.w3mod",
        ]

        self._index: dict[int, indexfile.IndexFile] = {}
        self._channels: dict[int, object] = {}
        self._tvfs: tvfs.Tvfs | None = None
        self._nested: dict[bytes, tvfs.Tvfs] = {}
        self._loose: dict[str, str] | None = None
        self._load_indexes()
        self._aliases = self._load_aliases()

    # ---------- 打开 ----------

    def _config_path(self, build_key: str) -> Path | None:
        if len(build_key) < 4:
            return None
        folder = self.data_dir / "config" / build_key[0:2] / build_key[2:4]
        candidate = folder / build_key
        return candidate if candidate.is_file() else None

    def _load_indexes(self) -> None:
        idx_files = sorted((self.data_dir / "data").glob("*.idx"))
        if not idx_files:
            raise CascError("Data/data 下没有 .idx 索引")

        for path in idx_files:
            try:
                parsed = indexfile.parse(path)
            except Exception:                      # noqa: BLE001
                continue
            self._index[parsed.bucket] = parsed

        if not self._index:
            raise CascError("所有 .idx 都无法解析")

    def _loose_index(self) -> dict[str, str]:
        """去掉全部反斜杠后建索引，用于 path 查找的兜底。

        TVFS 的前缀树会把多级目录折叠进相邻片段：实测
        `ReplaceableTextures\\CommandButtons\\BTNXxx.blp` 在树里长成
        `replaceabletextures\\commandbuttonsbtnxxx.blp`，中间少一个分隔符。
        按原样拼出来的路径永远查不到标准写法，于是大量内置贴图被判成"缺失"。

        这里用「忽略分隔符」的归一化兜底，**只在精确查找失败后**才用，
        不影响本来就能命中的路径。歧义（两种不同路径归一化后相同）一律丢弃，
        宁可报缺失也不配错贴图。
        """
        if self._loose is not None:
            return self._loose
        idx: dict[str, str] = {}
        amb: set[str] = set()
        for name in self.prefixes:                 # HD 覆盖层在前，天然优先
            try:
                paths = self.list_all(name)
            except Exception:                      # noqa: BLE001
                continue
            for path in paths:
                key = path.replace("\\", "")
                full = f"{name.lower()}\\{path}"
                prev = idx.get(key)
                if prev is None:
                    idx[key] = full
                elif prev != full:
                    amb.add(key)
        for key in amb:
            idx.pop(key, None)
        self._loose = idx
        return idx

    def _load_aliases(self) -> dict[str, str]:
        """filealiases.json：把模型里写的 .tif 之类的虚拟名映射到真实文件。

        注意这里不能用 self.read()——它依赖别名表本身，会递归。
        """
        mapping: dict[str, str] = {}
        try:
            root = self._ensure_tvfs()
        except CascError:
            return mapping

        entry = root.files.get("filealiases.json")
        if entry is None:
            return mapping

        buf = bytearray()
        for ref in entry.refs:
            raw = self._read_by_ekey(ref.ekey)
            if raw is None:
                return mapping
            buf += blte.decode(raw)

        try:
            entries = json.loads(bytes(buf).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return mapping
        for item in entries:
            src = str(item.get("src", "")).lower().replace("/", "\\")
            dest = str(item.get("dest", "")).lower().replace("/", "\\")
            if src and dest:
                mapping[src] = dest
        return mapping

    def _ensure_tvfs(self) -> tvfs.Tvfs:
        if self._tvfs is not None:
            return self._tvfs

        # TVFS 根是 config 里的 vfs-root（不是 encoding —— encoding 是
        # 「EKey → CKey」的映射表，内容不是 TVFS）
        keys = self.config.get("vfs-root") or []
        if len(keys) < 2:
            raise CascError("build 配置里没有 vfs-root")
        ekey = bytes.fromhex(keys[1])

        raw = self._read_by_ekey(ekey)
        if raw is None:
            raise CascError(f"vfs-root 的 ekey {ekey.hex()[:16]} 不在本地索引里")
        try:
            self._tvfs = tvfs.Tvfs(blte.decode(raw))
        except Exception as exc:                   # noqa: BLE001
            raise CascError(f"TVFS 解析失败：{exc}") from None
        return self._tvfs

    # ---------- 底层读取 ----------

    def _read_by_ekey(self, ekey: bytes) -> bytes | None:
        # bucket 要用「索引自己的 key 长度」算，不是 ekey 的长度：
        # WC3 的索引 key 只有 9 字节，而 config 里的 ekey 是 16 字节，
        # 直接拿 16 字节异或会落到错误的桶里。
        hit: tuple[indexfile.IndexFile, indexfile.IndexEntry] | None = None
        for idx in self._index.values():
            prefix = ekey[:idx.key_length]
            if _bucket_of(prefix) != idx.bucket:
                continue
            entry = idx.get(prefix)
            if entry is not None:
                hit = (idx, entry)
                break

        if hit is None:
            return None
        idx, entry = hit

        store = entry.store_index(idx.data_file_size_bits)
        offset = entry.store_offset(idx.data_file_size_bits)
        data_path = self.data_dir / "data" / f"data.{store:03d}"
        if not data_path.is_file():
            return None

        channel = self._channels.get(store)
        if channel is None:
            channel = data_path.open("rb")
            self._channels[store] = channel

        # WC3 3.0 的 data.NNN 里，每个块前有 30 字节块头
        # （7 字节 + 9 字节 key 逆序 + 4 字节 size + 10 字节），
        # 索引里的 offset 指向块头开头，BLTE 从它后面 30 字节才开始。
        #
        # 注意：索引里的 file_size **也包含**这 30 字节块头。
        # 多读 30 字节时，只有文件末尾的最后一块会暴露（读到不足就失败），
        # 中间块因为后面还有下一块的块头，zlib 会忽略多余字节 —— 所以这个坑很隐蔽。
        skip = self._locate_blte(channel, offset, entry.file_size)
        if skip is None:
            return None

        payload = entry.file_size - skip
        if payload <= 0:
            return None
        channel.seek(offset + skip)
        chunk = channel.read(payload)
        if len(chunk) != payload:
            return None
        return chunk

    @staticmethod
    def _locate_blte(channel, offset: int, length: int, window: int = 64) -> int | None:
        """从 offset 起在有限窗口内找到 BLTE 起点，返回相对偏移。"""
        channel.seek(offset)
        probe = channel.read(min(window, length))
        if probe[:4] == b"BLTE":
            return 0
        pos = probe.find(b"BLTE")
        return pos if pos > 0 else None

    # ---------- 对外 ----------

    def _sub_tvfs(self, entry: "tvfs.FileEntry") -> "tvfs.Tvfs | None":
        """把根 TVFS 里的一个条目当作嵌套文件系统解开。"""
        if not entry.refs:
            return None
        ekey = entry.refs[0].ekey
        cached = self._nested.get(ekey)
        if cached is not None:
            return cached
        raw = self._read_by_ekey(ekey)
        if raw is None:
            return None
        try:
            sub = tvfs.Tvfs(blte.decode(raw))
        except Exception:                          # noqa: BLE001
            return None
        self._nested[ekey] = sub
        return sub

    def _split(self, real: str) -> tuple["tvfs.Tvfs", str] | None:
        """把路径拆成 (所属 TVFS, TVFS 内路径)。支持多级嵌套。

        war3.w3mod 里还套着 _hd.w3mod，所以这里要逐级往下钻，
        不能只判断第一段。
        """
        table = self._ensure_tvfs()
        rest = real.lower()
        while True:
            if rest in table.files:
                return table, rest
            first, _, tail = rest.partition("\\")
            if not tail:
                return None
            entry = table.files.get(first)
            if entry is None:
                return None
            sub = self._sub_tvfs(entry)
            if sub is None:
                return None
            table, rest = sub, tail

    # Reforged 把大量经典版 .blp 换成了 .dds，但模型里仍写着旧路径
    EXT_FALLBACK = {
        ".blp": (".dds", ".tif", ".tiff", ".png"),
        ".tif": (".dds", ".blp"),
        ".tga": (".dds", ".blp"),
        ".dds": (".blp", ".tif"),
    }

    def resolve(self, path: str) -> str | None:
        """把模型里写的路径解析成 CASC 里的真实路径（含别名与前缀规则）。"""
        key = path.lower().replace("/", "\\")
        if not key:
            return None
        key = self._aliases.get(key, key)

        candidates = [key]
        # 扩展名回退：.blp → .dds 是最常见的（Reforged 的重制贴图）
        stem, dot, ext = key.rpartition(".")
        if dot:
            for alt in self.EXT_FALLBACK.get("." + ext, ()):
                candidates.append(f"{stem}{alt}")

        for candidate in candidates:
            # 虚拟目录前缀（war3.w3mod\ 等）；前缀本身也是嵌套容器
            for name in self.prefixes:
                full = f"{name.lower()}\\{candidate}"
                if self._split(full) is not None:
                    return full
            if self._split(candidate) is not None:
                return candidate
            # 精确查不到时才用宽松索引（容忍 TVFS 折叠掉的分隔符）
            loose = self._loose_index().get(candidate.replace("\\", ""))
            if loose is not None:
                return loose
        return None

    def read(self, path: str) -> bytes:
        """按路径读文件，自动套用别名与前缀。"""
        real = self.resolve(path)
        if real is None:
            raise CascError(f"CASC 里找不到：{path}")
        found = self._split(real.lower())
        if found is None:
            raise CascError(f"读取失败：{real}")
        table, inner = found
        entry = table.files[inner]

        out = bytearray()
        for ref in entry.refs:
            raw = self._read_by_ekey(ref.ekey)
            if raw is None:
                raise CascError(f"数据块缺失：{real}")
            out += blte.decode(raw)
        return bytes(out)

    def exists(self, path: str) -> bool:
        return self.resolve(path) is not None

    def list_files(self) -> list[str]:
        """根 TVFS 的条目（不含嵌套）。"""
        return sorted(self._ensure_tvfs().files)

    def containers(self) -> list[str]:
        """根 TVFS 里的嵌套容器（war3.w3mod 等）。"""
        root = self._ensure_tvfs()
        return [name for name in sorted(root.files) if "\\" not in name]

    def list_all(self, prefix: str = "war3.w3mod") -> list[str]:
        """列出某个容器内的全部文件（默认 war3.w3mod，即游戏资产）。

        支持多级容器（如 war3.w3mod\\_hd.w3mod）：逐段下钻，
        不能只取第一段——_hd.w3mod 是 war3.w3mod 内的嵌套容器。
        """
        table = self._ensure_tvfs()
        rest = prefix.lower()
        while True:
            entry = table.files.get(rest)
            if entry is not None:
                sub = self._sub_tvfs(entry)
                return sorted(sub.files) if sub is not None else []
            first, _, tail = rest.partition("\\")
            if not tail:
                return []
            entry = table.files.get(first)
            if entry is None:
                return []
            sub = self._sub_tvfs(entry)
            if sub is None:
                return []
            table, rest = sub, tail

    def find(self, pattern: str, limit: int = 200, prefix: str = "war3.w3mod") -> list[str]:
        """在容器内按正则找路径，用于挑模型。"""
        regex = re.compile(pattern, re.IGNORECASE)
        out = []
        for path in self.list_all(prefix):
            if regex.search(path):
                out.append(path)
                if len(out) >= limit:
                    break
        return out

    def close(self) -> None:
        for channel in self._channels.values():
            try:
                channel.close()                    # type: ignore[attr-defined]
            except Exception:                      # noqa: BLE001
                pass
        self._channels.clear()

    def __enter__(self) -> "Casc":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_casc(install_dir: str | Path, locale: str = "enUS") -> Casc:
    return Casc(install_dir, locale=locale)


# 常见安装位置，用于自动探测
_CANDIDATES = (
    r"C:/Program Files/Warcraft III",
    r"C:/Program Files (x86)/Warcraft III",
    r"D:/Games/Warcraft III",
    r"D:/Games/War3 2.0",
    r"D:/Program Files/Warcraft III",
)


def find_install(extra: list[str] | None = None) -> Path | None:
    """找一个可用的 WC3 Reforged 安装目录。"""
    import os

    for raw in (extra or []):
        found = deepest_install(Path(raw))
        if (found / ".build.info").is_file():
            return found

    env = os.environ.get("WC3_INSTALL") or os.environ.get("WC3_DIR")
    if env:
        found = deepest_install(Path(env))
        if (found / ".build.info").is_file():
            return found

    for raw in _CANDIDATES:
        found = deepest_install(Path(raw))
        if (found / ".build.info").is_file():
            return found
    return None


def _looks_like_install(path: Path) -> bool:
    """真正的安装目录要有 .build.info 和 Data 目录；也可能多套几层。"""
    return deepest_install(path) is not path or (
        (path / ".build.info").is_file() and (path / "Data").is_dir())


def deepest_install(path: Path, depth: int = 4) -> Path:
    """把「父目录」递归下钻到真正的安装目录（版本号常套好几层）。"""
    if (path / ".build.info").is_file() and (path / "Data").is_dir():
        return path
    if depth <= 0 or not path.is_dir():
        return path
    try:
        children = sorted(c for c in path.iterdir() if c.is_dir())
    except OSError:
        return path
    for child in children:
        found = deepest_install(child, depth - 1)
        if (found / ".build.info").is_file():
            return found
    return path
