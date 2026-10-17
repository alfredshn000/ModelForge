"""一次「打开 → 改 → 存」会话。

这一层是方案1 的地基：界面和以后的 MCP 都调它，不依赖任何 UI。

## 为什么改动要先攒着

改一条贴图路径，界面上要立刻看到新值，但文件不能动——用户可能改十条再一起存，
也可能中途放弃。所以：

- `self.pending` 记住所有未保存改动，`meta()` 把改动叠加到原始值上再返回
- `save()` 才真正生成新字节：`ChunkEditor` 定长覆写 → `check_delta` 断言
  "只有该改的字节变了" → 备份 → 写盘

定长覆写的意义：文件长度永不变，所以校验比结构变更严得多，
一条路径改错位置会被立刻拦下，不会写出一个"能打开但内容错"的文件。

## 保存后为什么重新打开

保存会重写磁盘上的文件。重新 `parse` 一遍能保证：
1. 校验真的通过（不是"我以为写对了"）
2. 后续操作的偏移基于新字节
代价是一次解析（2.5 MB 约 100 ms），可以接受。
"""

from __future__ import annotations

import copy
import dataclasses
import pathlib
import shutil
import time

from mdxcore import ops, rebuild
from mdxcore import paths as P
from mdxcore.chunks import atch as atchmod
from mdxcore.chunks import mtls as mtlsmod
from mdxcore.chunks import seqs as seqsmod
from mdxcore.chunks import texs as texsmod
from mdxcore.container import parse
from mdxcore.verify import check_delta
from mdxcore.writers import ChunkEditor, pack_path_field

import extract

FILTERS = ["None", "Transparent", "Blend", "Additive", "AddAlpha",
           "Modulate", "Modulate2x"]

# WC3 路径里不允许出现的字符（会破坏引擎解析或文件名）
BAD_PATH_CHARS = set('<>:"|?*')


class SessionError(Exception):
    """操作被拒绝。message 直接给用户看。"""


@dataclasses.dataclass
class Change:
    kind: str          # tex / layer / seq / atch
    target: str        # 人读描述
    before: object
    after: object

    def as_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target,
                "before": self.before, "after": self.after}


def _pack_str(text: str, width: int) -> bytes:
    raw = (text or "").encode("latin-1", "replace")
    if len(raw) > width - 1:
        raise SessionError(f"字符串过长：{len(raw)} 字节，最多 {width - 1}")
    return raw.ljust(width, b"\x00")


class ModelSession:
    def __init__(self, path: pathlib.Path, raw: bytes, store=None):
        self.path = pathlib.Path(path)
        self.raw = raw
        self.mdx = parse(raw)
        self.store = store
        self.pending: dict[str, dict] = {"tex": {}, "layer": {}, "seq": {}, "atch": {}}
        self._base_meta: dict | None = None
        self._render: dict | None = None

    # ---------------------------------------------------------------- 打开

    @classmethod
    def open(cls, path, store=None) -> "ModelSession":
        path = pathlib.Path(path)
        if not path.is_file():
            raise SessionError(f"文件不存在：{path}")
        raw = path.read_bytes()
        if raw[:4] != b"MDLX":
            raise SessionError(f"不是 MDX 文件（头 4 字节是 {raw[:4]!r}）")
        try:
            return cls(path, raw, store)
        except Exception as exc:                          # noqa: BLE001
            raise SessionError(f"解析失败：{exc}") from exc

    @property
    def model_dir(self) -> pathlib.Path:
        return self.path.parent

    # ---------------------------------------------------------------- 读

    def meta(self) -> dict:
        if self._base_meta is None:
            self._base_meta = extract.meta(self.mdx, self.path.name, str(self.path))
        data = copy.deepcopy(self._base_meta)
        self._apply_tex(data)
        self._apply_layers(data)
        self._apply_seqs(data)
        self._apply_atch(data)
        data["changes"] = [c.as_dict() for c in self.changes()]
        data["dirty"] = self.is_dirty()
        return data

    def _apply_tex(self, data: dict) -> None:
        for idx, new in self.pending["tex"].items():
            if not 0 <= idx < len(data["textures"]):
                continue
            t = data["textures"][idx]
            t["path"] = new
            t["cls"] = P.classify(new, t["replaceableId"])
            t["reason"] = P.reason(new, t["replaceableId"])
            t["file"] = P.basename(new)

    def _apply_layers(self, data: dict) -> None:
        for key, spec in self.pending["layer"].items():
            mi, li = key
            try:
                lay = data["materials"][mi]["layers"][li]
            except (IndexError, KeyError):
                continue
            if "filter" in spec:
                lay["filter"] = FILTERS[spec["filter"]] if 0 <= spec["filter"] < 7 else lay["filter"]
            if "alpha" in spec:
                lay["alpha"] = round(float(spec["alpha"]), 3)
            if "textureId" in spec:
                lay["textureId"] = spec["textureId"]
            if "hd" in spec:
                lay["hd"] = dict(spec["hd"])

    def _apply_seqs(self, data: dict) -> None:
        for idx, spec in self.pending["seq"].items():
            if not 0 <= idx < len(data["sequences"]):
                continue
            s = data["sequences"][idx]
            if "start" in spec:
                s["start"] = spec["start"]
            if "end" in spec:
                s["end"] = spec["end"]
            if "loop" in spec:
                s["loop"] = spec["loop"]

    def _apply_atch(self, data: dict) -> None:
        for idx, spec in self.pending["atch"].items():
            if not 0 <= idx < len(data["attachments"]):
                continue
            a = data["attachments"][idx]
            if "name" in spec:
                a["name"] = spec["name"]
            if "path" in spec:
                a["mount"] = spec["path"]
            if "attachmentId" in spec:
                a["attachmentId"] = spec["attachmentId"]

    def render(self) -> dict:
        """几何/骨骼/动画。定长字段的改动不影响它，所以只算一次。"""
        if self._render is None:
            self._render = extract.render(self.mdx)
        return self._render

    def texture(self, index: int, size: int = 512, team: int = 0):
        meta = self._base_meta or extract.meta(self.mdx, self.path.name, str(self.path))
        if not 0 <= index < len(meta["textures"]):
            raise SessionError(f"贴图索引 {index} 超出范围（共 {len(meta['textures'])} 条）")
        t = meta["textures"][index]
        path = self.pending["tex"].get(index, t["path"])
        rid = t["replaceableId"]

        if rid:
            # 可替换贴图在模型里没有文件，是运行时按玩家颜色代入的。
            # 游戏里就是 ReplaceableTextures\TeamColor\TeamColorNN.blp 这几张纯色图，
            # 直接按当前选的队伍色去取，缩略图和 3D 就都有颜色了。
            sub = {1: "TeamColor", 2: "TeamGlow"}.get(rid)
            if sub and self.store is not None:
                p = f"ReplaceableTextures\\{sub}\\{sub}{max(0, min(23, team)):02d}.blp"
                payload, info = self.store.png(p, size, self.model_dir)
                if payload:
                    return payload, dict(info, origin=f"队伍颜色 {team:02d}",
                                         team=True)
            name = "队伍颜色" if rid == 1 else ("队伍光晕" if rid == 2 else f"替换 {rid}")
            return None, {"origin": f"运行时生成（{name}）", "w": 0, "h": 0,
                          "avg": [200, 200, 200], "generated": True}
        if not path:
            return None, {"origin": "路径为空", "w": 0, "h": 0, "avg": None}
        if self.store is None:
            return None, {"origin": "未配置贴图搜索路径", "w": 0, "h": 0, "avg": None}
        return self.store.png(path, size, self.model_dir)

    # ---------------------------------------------------------------- 改

    def edit(self, kind: str, payload: dict) -> dict:
        kind = (kind or "").lower()
        if kind == "tex":
            return self._edit_tex(payload)
        if kind == "layer":
            return self._edit_layer(payload)
        if kind == "seq":
            return self._edit_seq(payload)
        if kind == "atch":
            return self._edit_atch(payload)
        raise SessionError(f"未知操作 {kind}")

    def _edit_tex(self, p: dict) -> dict:
        idx = int(p["index"])
        new = str(p.get("path", ""))
        texs = ops.list_textures(self.mdx)
        if not 0 <= idx < len(texs):
            raise SessionError(f"贴图索引 {idx} 超出范围")
        if texs[idx].replaceable_id:
            raise SessionError("这是运行时生成的贴图（replaceableId≠0），没有路径可改")
        if not new.strip():
            raise SessionError("路径不能为空")
        bad = sorted({ch for ch in new if ch in BAD_PATH_CHARS or ord(ch) < 32})
        if bad:
            raise SessionError(f"路径含非法字符 {''.join(bad)}")
        if len(new.encode("latin-1", "replace")) > 255:
            raise SessionError("路径超过 255 字节，写不进 256 字节字段")
        self.pending["tex"][idx] = new
        return {"ok": True, "index": idx, "path": new}

    def _edit_layer(self, p: dict) -> dict:
        mi, li = int(p["mi"]), int(p["li"])
        mats = ops.list_materials(self.mdx)
        if not 0 <= mi < len(mats) or not 0 <= li < len(mats[mi].layers):
            raise SessionError("材质或图层不存在")
        lay = mats[mi].layers[li]
        spec = self.pending["layer"].setdefault((mi, li), {})

        if "filter" in p:
            f = int(p["filter"])
            if not 0 <= f < len(FILTERS):
                raise SessionError(f"FilterMode 只能是 0–6，收到 {f}")
            spec["filter"] = f
        if "alpha" in p:
            a = float(p["alpha"])
            if not 0.0 <= a <= 1.0:
                raise SessionError(f"alpha 必须在 0–1，收到 {a}")
            spec["alpha"] = a
        if "textureId" in p:
            tid = int(p["textureId"])
            n = len(ops.list_textures(self.mdx))
            if not 0 <= tid < n:
                raise SessionError(f"贴图索引 {tid} 超出范围（共 {n} 条）")
            if not lay.hd_slots:
                # 老模型（<1100 或没有合并槽）直接改历史字段
                spec["textureId"] = tid
            else:
                spec.setdefault("hd", {})["Diffuse"] = tid
        if "hd" in p and isinstance(p["hd"], dict):
            for name, tid in p["hd"].items():
                if not any(s.type_name == name for s in (lay.hd_slots or [])):
                    raise SessionError(f"文件里没有 {name} 槽")
                spec.setdefault("hd", {})[name] = int(tid)
        if not spec:
            raise SessionError("没有要改的字段")
        return {"ok": True, "mi": mi, "li": li, "spec": spec}

    def _edit_seq(self, p: dict) -> dict:
        idx = int(p["index"])
        seqs = ops.list_sequences(self.mdx)
        if not 0 <= idx < len(seqs):
            raise SessionError("序列不存在")
        cur = seqs[idx]
        spec = self.pending["seq"].setdefault(idx, {})
        start = int(p.get("start", spec.get("start", cur.start)))
        end = int(p.get("end", spec.get("end", cur.end)))
        if start < 0 or end <= start:
            raise SessionError(f"区间非法：{start}–{end}（结束必须大于开始）")
        if end > 0x7FFFFFFF:
            raise SessionError("时间超出范围")
        spec["start"], spec["end"] = start, end
        if "loop" in p:
            spec["loop"] = bool(p["loop"])
        return {"ok": True, "index": idx, **spec}

    def _edit_atch(self, p: dict) -> dict:
        idx = int(p["index"])
        items = atchmod.parse(self.mdx.data(self.mdx.find("ATCH"))) \
            if any(c.tag == "ATCH" for c in self.mdx.chunks) else []
        if not 0 <= idx < len(items):
            raise SessionError("附着点不存在")
        spec = self.pending["atch"].setdefault(idx, {})
        if "name" in p:
            name = str(p["name"])
            if len(name.encode("latin-1", "replace")) > 79:
                raise SessionError("名字超过 79 字节")
            spec["name"] = name
        if "path" in p:
            path = str(p["path"])
            bad = sorted({ch for ch in path if ch in BAD_PATH_CHARS or ord(ch) < 32})
            if bad:
                raise SessionError(f"路径含非法字符 {''.join(bad)}")
            if len(path.encode("latin-1", "replace")) > 255:
                raise SessionError("路径超过 255 字节")
            spec["path"] = path
        if "attachmentId" in p:
            spec["attachmentId"] = int(p["attachmentId"])
        if not spec:
            raise SessionError("没有要改的字段")
        return {"ok": True, "index": idx, "spec": spec}

    # ---------------------------------------------------------------- 改动清单

    def is_dirty(self) -> bool:
        return any(self.pending[k] for k in self.pending)

    def changes(self) -> list[Change]:
        out: list[Change] = []
        texs = ops.list_textures(self.mdx)
        for idx, new in sorted(self.pending["tex"].items()):
            out.append(Change("tex", f"贴图 #{idx} {P.basename(texs[idx].path)}",
                              texs[idx].path, new))

        mats = ops.list_materials(self.mdx)
        for (mi, li), spec in sorted(self.pending["layer"].items()):
            lay = mats[mi].layers[li]
            if "filter" in spec:
                out.append(Change("layer", f"材质 {mi} 层 {li} · FilterMode",
                                  lay.filter_name, FILTERS[spec["filter"]]))
            if "alpha" in spec:
                out.append(Change("layer", f"材质 {mi} 层 {li} · alpha",
                                  round(float(lay.alpha), 3), round(spec["alpha"], 3)))
            if "textureId" in spec:
                out.append(Change("layer", f"材质 {mi} 层 {li} · 贴图",
                                  lay.texture_id, spec["textureId"]))
            for name, tid in (spec.get("hd") or {}).items():
                old = next((s.texture_id for s in (lay.hd_slots or [])
                            if s.type_name == name), None)
                out.append(Change("layer", f"材质 {mi} 层 {li} · {name}", old, tid))

        seqs = ops.list_sequences(self.mdx)
        for idx, spec in sorted(self.pending["seq"].items()):
            s = seqs[idx]
            if "start" in spec or "end" in spec:
                out.append(Change("seq", f"序列 #{idx} {s.name} 区间",
                                  f"{s.start}–{s.end}",
                                  f"{spec.get('start', s.start)}–{spec.get('end', s.end)}"))
            if "loop" in spec:
                out.append(Change("seq", f"序列 #{idx} {s.name} 循环",
                                  not s.non_looping, spec["loop"]))

        if self.pending["atch"]:
            items = atchmod.parse(self.mdx.data(self.mdx.find("ATCH")))
            for idx, spec in sorted(self.pending["atch"].items()):
                a = items[idx]
                if "name" in spec:
                    out.append(Change("atch", f"附着点 #{idx} · 名字", a.name, spec["name"]))
                if "path" in spec:
                    out.append(Change("atch", f"附着点 #{idx} {a.name} · 挂载",
                                      a.path, spec["path"]))
                if "attachmentId" in spec:
                    out.append(Change("atch", f"附着点 #{idx} {a.name} · 编号",
                                      a.attachment_id, spec["attachmentId"]))
        return out

    def revert(self) -> None:
        self.pending = {"tex": {}, "layer": {}, "seq": {}, "atch": {}}

    # ---------------------------------------------------------------- 存

    def build_bytes(self) -> tuple[bytes, list[str]]:
        """把 pending 应用成新字节，并做 `check_delta` 断言。"""
        if not self.is_dirty():
            raise SessionError("没有改动可保存")
        ed = ChunkEditor(self.mdx)
        notes: list[str] = []

        for idx, new in self.pending["tex"].items():
            lo, hi = texsmod.path_field_span(idx)
            ed.write_raw("TEXS", lo, pack_path_field(new, hi - lo))
            notes.append(f"贴图 #{idx} 路径")

        mats = ops.list_materials(self.mdx)
        for (mi, li), spec in self.pending["layer"].items():
            lay = mats[mi].layers[li]
            if "filter" in spec:
                ci, span = ops.layer_filter_span(self.mdx, mi, li)
                ed.write("MTLS", span[0], "<I", spec["filter"])
                notes.append(f"材质 {mi}/{li} FilterMode")
            if "alpha" in spec:
                ci, span = ops.layer_alpha_span(self.mdx, mi, li)
                ed.write("MTLS", span[0], "<f", float(spec["alpha"]))
                notes.append(f"材质 {mi}/{li} alpha")
            if "textureId" in spec:
                if not lay.hd_slots:
                    ci, span = ops.layer_texture_span(self.mdx, mi, li)
                    ed.write("MTLS", span[0], "<i", spec["textureId"])
                    notes.append(f"材质 {mi}/{li} 贴图")
            for name, tid in (spec.get("hd") or {}).items():
                slot_type = mtlsmod.resolve_hd_type(name)
                ci, span = ops.layer_hd_texture_span(self.mdx, mi, li, slot_type)
                ed.write("MTLS", span[0], "<i", tid)
                notes.append(f"材质 {mi}/{li} {name} 槽")

        seqs = ops.list_sequences(self.mdx)
        for idx, spec in self.pending["seq"].items():
            s = seqs[idx]
            if "start" in spec or "end" in spec:
                ci, span = ops.sequence_interval_span(self.mdx, idx)
                ed.write("SEQS", span[0], "<II",
                         spec.get("start", s.start), spec.get("end", s.end))
                notes.append(f"序列 #{idx} 区间")
            if "loop" in spec:
                ci, span = ops.sequence_looping_span(self.mdx, idx)
                ed.write("SEQS", span[0], "<I", 1 if not spec["loop"] else 0)
                notes.append(f"序列 #{idx} 循环")

        if self.pending["atch"]:
            items = atchmod.parse(self.mdx.data(self.mdx.find("ATCH")))
            for idx, spec in self.pending["atch"].items():
                a = items[idx]
                if "name" in spec:
                    ed.write_raw("ATCH", a.name_offset, _pack_str(spec["name"], 80))
                    notes.append(f"附着点 #{idx} 名字")
                if "path" in spec:
                    ed.write_raw("ATCH", a.path_offset,
                                 _pack_str(spec["path"], atchmod.PATH_LEN))
                    notes.append(f"附着点 #{idx} 挂载路径")
                if "attachmentId" in spec:
                    ed.write("ATCH", a.id_offset, "<I", spec["attachmentId"] & 0xFFFFFFFF)
                    notes.append(f"附着点 #{idx} 编号")

        after = rebuild(self.mdx, ed.apply())
        issues = check_delta(self.raw, after, self.mdx, ed.abs_spans())
        if issues:
            raise SessionError("改动的字节越界，已拒绝写盘：" + "；".join(issues[:3]))
        return after, notes

    def save(self, dest=None) -> dict:
        dest = pathlib.Path(dest) if dest else self.path
        after, notes = self.build_bytes()

        backup = None
        if dest.exists():
            backup = dest.with_name(dest.name + ".bak")
            if backup.exists():
                backup = dest.with_name(
                    f"{dest.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copyfile(dest, backup)

        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(after)
        shutil.move(str(tmp), str(dest))

        report = {
            "ok": True,
            "dest": str(dest),
            "backup": str(backup) if backup else None,
            "bytes_before": len(self.raw),
            "bytes_after": len(after),
            "changes": [c.as_dict() for c in self.changes()],
            "notes": notes,
        }

        # 保存后重开：用磁盘上的真实字节，确保后续操作基于新偏移
        self.raw = dest.read_bytes()
        self.mdx = parse(self.raw)
        self._base_meta = None
        self._render = None
        self.revert()
        return report
