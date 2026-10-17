"""场景状态：MDX 里所有「定长、可直接编辑」字段的当前值。

为什么单独存一份而不是每次重读文件：

- **文件是真相，场景是视图。** 导出时要拿"用户眼里的值"和"文件里的值"比对，
  差的那几个字段才写回；没动的字节一个都不碰。
- 基线必须跟着 .blend 走。存进场景自定义属性（JSON）后，保存再打开还在；
  每次重读文件反而会把"上次没导出的改动"抹掉。

覆盖四类：贴图表 / 材质图层 / 几何动画 / 动画序列。
这四类的字段全是定长，所以改它们**不会改变文件长度**——
这也正是能用 `check_delta`（只允许某些字节变化）来机器证明"没碰别处"的前提。

几何（GEOS）与骨骼动画（BONE）不走这里：它们有自己的定点/重建路径。
"""

from __future__ import annotations

import json

import bpy

from .bridge import get_core

SCENE_KEY = "mdx_state"
SCHEMA = 1


# ---------- 存取 ----------

def load() -> dict:
    raw = bpy.context.scene.get(SCENE_KEY)
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except Exception:                              # noqa: BLE001
        return {}
    if state.get("schema") != SCHEMA:
        return {}
    return state


def save(state: dict) -> None:
    state["schema"] = SCHEMA
    bpy.context.scene[SCENE_KEY] = json.dumps(state, ensure_ascii=False)


def clear() -> None:
    if SCENE_KEY in bpy.context.scene:
        del bpy.context.scene[SCENE_KEY]


# ---------- 快照 ----------

def snapshot(mdx, path: str = "") -> dict:
    """从已解析的 MdxFile 生成初始状态。缺哪个块就留空列表。"""
    from mdxcore import paths as pathmod

    core = get_core()
    state = {
        "schema": SCHEMA,
        "source_path": path,
        "format_version": None,
        "textures": [],
        "materials": [],
        "geoa": [],
        "sequences": [],
        "pivots": [],
        "attachments": [],
    }

    for c in mdx.chunks:
        if c.tag == "VERS":
            state["format_version"] = int.from_bytes(mdx.data(c)[:4], "little")
            break

    if any(c.tag == "TEXS" for c in mdx.chunks):
        from mdxcore.chunks import texs as texsmod
        for i, t in enumerate(texsmod.parse(mdx.data(mdx.find("TEXS")))):
            cls = pathmod.classify(t.path, t.replaceable_id)
            state["textures"].append({
                "index": i,
                "replaceable_id": t.replaceable_id,
                "path": t.path,
                "flags": t.flags,
                "class": cls,
                # 用户可以把某条翻成"别改我"，或从"别改"翻回来
                "locked": cls != pathmod.EXTERNAL,
            })

    if any(c.tag == "MTLS" for c in mdx.chunks):
        from mdxcore.chunks import mtls as mtlsmod
        version = state["format_version"] or 800
        for mi, mat in enumerate(mtlsmod.parse(mdx.data(mdx.find("MTLS")), version)):
            layers = []
            for li, lay in enumerate(mat.layers):
                hd = None
                if lay.hd_slots:
                    hd = {s.type_name: s.texture_id for s in lay.hd_slots}
                layers.append({
                    "index": li,
                    "filter": lay.filter_mode,
                    "alpha": lay.alpha,
                    "texture_id": lay.texture_id,
                    "coord_id": lay.coord_id,
                    "hd": hd,
                    "hd_trusted": bool(lay.hd_slots_trusted),
                })
            state["materials"].append({
                "index": mi,
                "priority": mat.priority_plane,
                "flags": mat.flags,
                "layers": layers,
            })

    if any(c.tag == "GEOA" for c in mdx.chunks):
        from mdxcore.chunks import geoa as geoamod
        for i, item in enumerate(geoamod.parse(mdx.data(mdx.find("GEOA")))):
            state["geoa"].append({
                "index": i,
                "geoset": item.geoset_id,
                "alpha": item.alpha,
                "color": list(item.color),
            })

    if any(c.tag == "SEQS" for c in mdx.chunks):
        from mdxcore.chunks import seqs as seqsmod
        for i, s in enumerate(seqsmod.parse(mdx.data(mdx.find("SEQS")))):
            state["sequences"].append({
                "index": i,
                "name": s.name,
                "start": s.start,
                "end": s.end,
                "non_looping": bool(s.non_looping),
            })

    # 轴心点：是一串扁平的 float，按节点声明顺序对应。
    # 名字来自 nodes.analyze，无法自证的位置留空（宁可不给名字，也不给错名字）。
    if any(c.tag == "PIVT" for c in mdx.chunks):
        from mdxcore.chunks import nodes as nodesmod
        from mdxcore.chunks import pivt as pivtmod

        points = pivtmod.parse(mdx.data(mdx.find("PIVT")))
        try:
            slots, note = nodesmod.analyze(mdx, len(points))
        except Exception:                          # noqa: BLE001
            slots, note = [None] * len(points), "节点名解析失败"
        state["pivot_note"] = note
        for i, p in enumerate(points):
            ref = slots[i] if i < len(slots) else None
            state["pivots"].append({
                "index": i,
                "xyz": [p.x, p.y, p.z],
                "node": ref.name if ref else "",
                "tag": ref.tag if ref else "",
                "confirmed": ref is not None,
            })

    # 附着点：挂在骨骼上的子模型。objectId 就是 PIVT 下标，
    # 所以父节点名字可以直接从轴心点表里查到。
    if any(c.tag == "ATCH" for c in mdx.chunks):
        from mdxcore.chunks import atch as atchmod

        for i, a in enumerate(atchmod.parse(mdx.data(mdx.find("ATCH")))):
            state["attachments"].append({
                "index": i,
                "name": a.name,
                "object_id": a.object_id,
                "parent_id": a.parent_id,
                "path": a.path,
                "attachment_id": a.attachment_id,
            })

    return state


# ---------- 查询（面板用） ----------

def texture_of(state: dict, index: int) -> dict | None:
    items = state.get("textures") or []
    if 0 <= index < len(items):
        return items[index]
    return None


def texture_path(state: dict, index: int) -> str:
    item = texture_of(state, index)
    return item["path"] if item else ""


def layer_of(state: dict, mat: int, layer: int = 0) -> dict | None:
    mats = state.get("materials") or []
    if not 0 <= mat < len(mats):
        return None
    layers = mats[mat].get("layers") or []
    if not 0 <= layer < len(layers):
        return None
    return layers[layer]


def diffuse_index(state: dict, mat: int, layer: int = 0) -> int:
    """该图层的漫反射贴图索引。

    HD 模型的图层前缀 textureId 是历史字段，真值在合并槽的 Diffuse 里；
    没有槽（1000 及以下）才回落到前缀。
    """
    lay = layer_of(state, mat, layer)
    if lay is None:
        return -1
    hd = lay.get("hd")
    if hd and "Diffuse" in hd:
        return hd["Diffuse"]
    return lay.get("texture_id", -1)


def counts(state: dict) -> dict:
    tex = state.get("textures") or []
    cls = {"builtin": 0, "external": 0, "replaceable": 0, "empty": 0}
    for t in tex:
        cls[t.get("class", "empty")] = cls.get(t.get("class", "empty"), 0) + 1
    return {
        "textures": len(tex),
        "builtin": cls["builtin"],
        "external": cls["external"],
        "replaceable": cls["replaceable"],
        "materials": len(state.get("materials") or []),
        "layers": sum(len(m.get("layers") or []) for m in (state.get("materials") or [])),
        "geoa": len(state.get("geoa") or []),
        "sequences": len(state.get("sequences") or []),
        "locked": sum(1 for t in tex if t.get("locked")),
    }
