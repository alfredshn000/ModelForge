"""从 MdxFile 提取前端要的两份数据：界面用的 meta、渲染用的 render。

这两个函数原来是脚本（`prototype/export_data.py`、`export_geometry.py`），
把结果写成静态 `.js` 文件；接上后端之后改成实时调用，字段名保持不变，
前端不用改结构。

为什么分两份：

- `meta` 只有几十 KB，打开模型立刻要（列表、属性面板）；
- `render` 有 2–3 MB（顶点/骨骼/关键帧全在里面），大模型要 100–200 ms，
  所以只在切模型时取一次，改材质之类的操作不必重算。
"""

from __future__ import annotations

import base64
import struct

from mdxcore import rebuild
from mdxcore import paths as P
from mdxcore import skin as SK
from mdxcore.chunks import atch as A
from mdxcore.chunks import bone as B
from mdxcore.chunks import geos as G
from mdxcore.chunks import pivt as PIVT
from mdxcore.ops import (format_version, list_materials, list_pivots,
                         list_sequences, list_textures)

TAG_TYPE = {"KGTR": 0, "KGRT": 1, "KGSC": 2}
PALETTE = ["#7f93a8", "#8e9cab", "#75899d", "#9aa7b4", "#6f8398", "#8695a6"]

# Reforged 的六个固定图层。HD 模型（ver 1100+）把它们合并在 hd_slots 里；
# SD 模型（ver 1000）则是老老实实六个图层，顺序就是下面这个。
REFORGED_LAYERS = ["diffuse", "normal", "orm", "emissive", "teamColor", "reflection"]


def b64(fmt: str, vals) -> str:
    return base64.b64encode(struct.pack("<" + fmt * len(vals), *vals)).decode()


def has(mdx, tag: str) -> bool:
    return any(c.tag == tag for c in mdx.chunks)


# ---------------------------------------------------------------- 界面数据

def meta(mdx, name: str, path: str) -> dict:
    """贴图 / 材质 / 序列 / 附着点 / 轴心点 + 规模统计。"""
    version = format_version(mdx)

    textures = []
    for i, t in enumerate(list_textures(mdx) if has(mdx, "TEXS") else []):
        textures.append({
            "index": i,
            "path": t.path,
            "replaceableId": t.replaceable_id,
            "cls": P.classify(t.path, t.replaceable_id),
            "reason": P.reason(t.path, t.replaceable_id),
            "file": P.basename(t.path),
            "locked": False,
        })

    materials = []
    for mi, mat in enumerate(list_materials(mdx) if has(mdx, "MTLS") else []):
        # ver 1000 且六层齐全 → Reforged SD 的固定图层表。
        # 只认这个组合：经典模型（ver 800 等）的多层是"叠加上色"，语义完全不同，
        # 把第 2 层当法线贴图会让老模型整个花掉。
        reforged_sd = version == 1000 and len(mat.layers) >= 6
        layers = []
        for li, lay in enumerate(mat.layers):
            hd = {s.type_name: s.texture_id
                  for s in (lay.hd_slots or []) if s.texture_id >= 0}
            layers.append({
                "index": li,
                "filter": lay.filter_name,
                "alpha": round(float(lay.alpha), 3),
                "textureId": lay.texture_id,
                "hd": hd,
                "hdTrusted": bool(lay.hd_slots_trusted),
                "twoSided": bool(lay.shading_flags & 0x10),
                "unshaded": bool(lay.shading_flags & 0x01),
                "role": REFORGED_LAYERS[li] if reforged_sd and li < 6 else "",
            })

        def tex_of(role: str) -> int:
            """法线/发光贴图：HD 模型在合并槽里，SD 模型在固定层号上。

            两种写法都要认——否则 Reforged 模型只剩漫反射，表面起伏全丢了，
            看上去就是"糊成一片"。
            """
            slot = {"normal": "Normal", "emissive": "Emissive", "orm": "ORM",
                    "teamColor": "TeamColor"}.get(role)
            if slot:
                for lay in layers:
                    v = (lay.get("hd") or {}).get(slot)
                    if v is not None and v >= 0:
                        return v
            for lay in layers:
                if lay["role"] == role:
                    return lay["textureId"]
            return -1

        materials.append({
            "index": mi,
            "priorityPlane": mat.priority_plane,
            "layers": layers,
            "reforgedSd": reforged_sd,
            "normalTex": tex_of("normal"),
            "emissiveTex": tex_of("emissive"),
            # ORM 的 alpha 通道是**队伍色遮罩**：1 = 该处涂玩家色。
            # 不读它就只能把整块模型染色，会把细节一起染掉。
            "ormTex": tex_of("orm"),
            # 队伍色槽位存在才说明这个材质吃玩家色
            "teamTex": tex_of("teamColor"),
        })

    seq_src = list_sequences(mdx) if has(mdx, "SEQS") else []
    sequences = [{"index": i, "name": s.name, "start": s.start, "end": s.end,
                  "loop": not s.non_looping} for i, s in enumerate(seq_src)]

    points, slots, note = list_pivots(mdx) if has(mdx, "PIVT") else ([], [], "无 PIVT")
    pivots = [{"index": i, "name": (s.name if s else ""), "tag": (s.tag if s else ""),
               "xyz": [round(v, 3) for v in p.xyz]}
              for i, (p, s) in enumerate(zip(points, slots))]

    attachments = []
    if has(mdx, "ATCH"):
        for i, a in enumerate(A.parse(mdx.data(mdx.find("ATCH")))):
            parent = slots[a.parent_id] if 0 <= a.parent_id < len(slots) else None
            attachments.append({
                "index": i,
                "name": a.name,
                "objectId": a.object_id,
                "parentId": a.parent_id,
                "parentName": parent.name if parent else f"#{a.parent_id}",
                "attachmentId": a.attachment_id,
                "mount": a.path,
            })

    bones = B.parse(mdx.data(mdx.find("BONE"))) if has(mdx, "BONE") else []
    bone_keys = 0
    if bones:
        bdata = mdx.data(mdx.find("BONE"))
        for b in bones:
            for flag in B.parse_tracks(bdata, b).values():
                bone_keys += len(flag.tracks)

    return {
        "model": {
            "file": name,
            "path": path,
            "bytes": len(mdx.raw),
            "version": version,
            "chunks": [c.tag for c in mdx.chunks],
            "pivotNote": note,
            "bones": len(bones),
            "keys": bone_keys,
            "roundTrip": rebuild(mdx, {}) == mdx.raw,
        },
        "textures": textures,
        "materials": materials,
        "sequences": sequences,
        "attachments": attachments,
        "pivots": pivots,
    }


# ---------------------------------------------------------------- 渲染数据

def render(mdx) -> dict:
    """顶点 / 索引 / UV / 法线 / 蒙皮 / 骨骼 / 动画轨道 / 粒子 / 包围盒。"""
    ver = format_version(mdx)
    pivots = PIVT.parse(mdx.data(mdx.find("PIVT"))) if has(mdx, "PIVT") else []
    bdata = mdx.data(mdx.find("BONE")) if has(mdx, "BONE") else b""
    bones = B.parse(bdata) if bdata else []

    blist = []
    for b in bones:
        pv = pivots[b.object_id].xyz if 0 <= b.object_id < len(pivots) else (0.0, 0.0, 0.0)
        blist.append({"n": b.name, "id": b.object_id, "p": b.parent_id,
                      "pv": [round(float(v), 4) for v in pv]})

    geosets = []
    total_v = total_t = 0
    lo = [1e18] * 3
    hi = [-1e18] * 3
    for gi, g in enumerate(G.parse(mdx.data(mdx.find("GEOS")), ver) if has(mdx, "GEOS") else []):
        nv = g.vertex_count
        total_v += nv
        total_t += len(g.faces) // 3

        pos = [c for v in g.vertices for c in v]
        for k in range(0, len(pos), 3):
            for ax in range(3):
                v = pos[k + ax]
                if v < lo[ax]:
                    lo[ax] = v
                if v > hi[ax]:
                    hi[ax] = v

        nrm = [c for v in g.normals for c in v] if g.normals else [0.0] * (nv * 3)
        if len(nrm) < nv * 3:
            nrm = (nrm + [0.0] * (nv * 3))[: nv * 3]

        if g.uv_sets and g.uv_sets[0]:
            uv = [c for p in g.uv_sets[0] for c in p]
        else:
            uv = [0.0] * (nv * 2)
        if len(uv) < nv * 2:
            uv = (uv + [0.0] * (nv * 2))[: nv * 2]

        infl = SK.influences(g)
        jnt, wgt, no_skin = [], [], 0
        for vi in range(nv):
            cand = infl[vi] if vi < len(infl) else []
            top = sorted(cand, key=lambda x: -x.weight)[:4]
            s = sum(x.weight for x in top)
            if s <= 0:
                no_skin += 1
                jnt += [0, 0, 0, 0]
                wgt += [1.0, 0.0, 0.0, 0.0]
                continue
            jnt += [x.node_index for x in top] + [0] * (4 - len(top))
            wgt += [x.weight / s for x in top] + [0.0] * (4 - len(top))

        geosets.append({
            "i": gi, "mat": g.material_id, "nv": nv, "nt": len(g.faces) // 3,
            "col": PALETTE[gi % len(PALETTE)],
            "pos": b64("f", pos), "nrm": b64("f", nrm), "uv": b64("f", uv),
            "idx": b64("H", list(g.faces)),
            "jnt": b64("H", jnt), "wgt": b64("f", wgt),
            "skinless": no_skin,
        })

    tracks = []
    nkeys = 0
    for bi, b in enumerate(bones):
        for tag, flag in (B.parse_tracks(bdata, b).items() if bdata else []):
            typ = TAG_TYPE.get(tag)
            if typ is None:
                continue
            keys = []
            for t in flag.tracks:
                keys.append(int(t.time))
                keys.extend(float(c) for c in t.value)
            if not keys:
                continue
            nkeys += len(flag.tracks)
            tracks.append({
                "b": bi, "t": typ, "n": len(flag.tracks),
                "s": 5 if tag == "KGRT" else 4,
                "interp": int(flag.interp),
                "k": b64("f", keys),
            })

    particles = []
    if has(mdx, "PRE2"):
        from mdxcore.chunks import pre2 as pre2mod
        try:
            for e in pre2mod.parse(mdx.data(mdx.find("PRE2"))):
                if not e.active:
                    continue
                particles.append({
                    "i": e.index, "n": e.name, "obj": e.object_id, "par": e.parent_id,
                    "rate": e.emission_rate, "life": e.lifespan, "speed": e.speed,
                    "var": e.variation, "lat": e.latitude, "grav": e.gravity,
                    "len": e.length, "wid": e.width,
                    "rows": e.rows, "cols": e.columns, "filt": e.filter_mode,
                    "tex": e.texture_id,
                    "col": [[round(c, 4) for c in rgb] for rgb in e.colors],
                    "alpha": [round(a, 3) for a in e.alphas],
                })
        except Exception:                                  # noqa: BLE001
            pass

    seqs = [{"i": i, "n": s.name, "s": s.start, "e": s.end, "loop": not s.non_looping}
            for i, s in enumerate(list_sequences(mdx) if has(mdx, "SEQS") else [])]

    empty = total_v == 0
    return {
        "ver": ver,
        "bytes": len(mdx.raw),
        "bones": blist,
        "geosets": geosets,
        "tracks": tracks,
        "particles": particles,
        "sequences": seqs,
        "bounds": {"min": [round(v, 3) for v in lo] if not empty else [0, 0, 0],
                   "max": [round(v, 3) for v in hi] if not empty else [0, 0, 0]},
        "stats": {"verts": total_v, "tris": total_t, "bones": len(blist), "keys": nkeys},
    }
