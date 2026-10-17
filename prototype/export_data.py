"""从真实模型抽出界面原型所需的全部数据，写成 data.js（window.MDX_DATA）。

原型不编假数据：贴图 367 条、材质、序列、附着点全部来自 David.mdx 实测值。
以后接真实后端时，把 data.js 换成 HTTP 返回即可，字段名保持不变。
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mdx-toolkit"))

from mdxcore import load, rebuild                       # noqa: E402
from mdxcore import paths as P                          # noqa: E402
from mdxcore.chunks import atch as A                    # noqa: E402
from mdxcore.chunks import bone as B                    # noqa: E402
from mdxcore.ops import (format_version, list_materials, list_pivots,
                         list_sequences, list_textures)  # noqa: E402

MODEL = next((ROOT / "test-models").rglob("David.mdx"))  # 可用 argv 覆盖
OUT = pathlib.Path(__file__).resolve().parent / "data.js"


def main() -> None:
    # 参数：argv[1]=模型路径  argv[2]=输出目录（可选）
    global MODEL, OUT, OUT_DIR
    if len(sys.argv) > 1:
        MODEL = pathlib.Path(sys.argv[1])
    if len(sys.argv) > 2:
        _d = pathlib.Path(sys.argv[2])
        _d.mkdir(parents=True, exist_ok=True)
        OUT = _d / OUT.name
        OUT_DIR = _d / 'tex'
    mdx = load(MODEL)
    has = lambda tag: any(c.tag == tag for c in mdx.chunks)
    version = format_version(mdx)

    # ---- 贴图 ----
    textures = []
    texs = list_textures(mdx) if has("TEXS") else []
    for i, t in enumerate(texs):
        cls = P.classify(t.path, t.replaceable_id)
        textures.append({
            "index": i,
            "path": t.path,
            "replaceableId": t.replaceable_id,
            "cls": cls,
            "reason": P.reason(t.path, t.replaceable_id),
            "file": P.basename(t.path),
            "locked": False,
        })

    # ---- 材质 / 图层 ----
    materials = []
    mats = list_materials(mdx) if has("MTLS") else []
    for mi, mat in enumerate(mats):
        layers = []
        for li, lay in enumerate(mat.layers):
            hd = {}
            for slot in (lay.hd_slots or []):
                if slot.texture_id >= 0:
                    hd[slot.type_name] = slot.texture_id
            layers.append({
                "index": li,
                "filter": lay.filter_name,
                "alpha": round(float(lay.alpha), 3),
                "textureId": lay.texture_id,
                "hd": hd,
                "hdTrusted": bool(lay.hd_slots_trusted),
                "twoSided": bool(lay.shading_flags & 0x10),
                "unshaded": bool(lay.shading_flags & 0x01),
            })
        materials.append({
            "index": mi,
            "priorityPlane": mat.priority_plane,
            "layers": layers,
        })

    # ---- 序列 ----
    seq_src = list_sequences(mdx) if has("SEQS") else []
    sequences = [{
        "index": i,
        "name": s.name,
        "start": s.start,
        "end": s.end,
        "loop": not s.non_looping,
    } for i, s in enumerate(seq_src)]

    # ---- 节点 / 附着点 ----
    points, slots, note = list_pivots(mdx) if has("PIVT") else ([], [], "无 PIVT")
    pivots = [{"index": i,
               "name": (s.name if s else ""),
               "tag": (s.tag if s else ""),
               "xyz": [round(v, 3) for v in p.xyz]} for i, (p, s) in enumerate(zip(points, slots))]

    attachments = []
    if has("ATCH"):
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

    # ---- 规模统计 ----
    bone_keys = 0
    if any(c.tag == "BONE" for c in mdx.chunks):
        bdata = mdx.data(mdx.find("BONE"))
        for b in B.parse(bdata):
            for flag in B.parse_tracks(bdata, b).values():
                bone_keys += len(flag.tracks)

    data = {
        "model": {
            "file": MODEL.name,
            "path": str(MODEL),
            "bytes": len(mdx.raw),
            "version": version,
            "chunks": [c.tag for c in mdx.chunks],
            "pivotNote": note,
            "bones": len(B.parse(mdx.data(mdx.find("BONE")))) if any(c.tag == "BONE" for c in mdx.chunks) else 0,
            "keys": bone_keys,
            "roundTrip": rebuild(mdx, {}) == mdx.raw,
        },
        "textures": textures,
        "materials": materials,
        "sequences": sequences,
        "attachments": attachments,
        "pivots": pivots,
    }

    payload = "window.MDX_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT.write_text(payload, encoding="utf-8")

    cls = {}
    for t in textures:
        cls[t["cls"]] = cls.get(t["cls"], 0) + 1
    print(f"写出 {OUT}  {OUT.stat().st_size:,} B")
    print(f"  贴图 {len(textures)}（{cls}）  材质 {len(materials)}  "
          f"序列 {len(sequences)}  附着点 {len(attachments)}  轴心点 {len(pivots)}")
    print(f"  骨骼 {data['model']['bones']}  关键帧 {bone_keys:,}  "
          f"版本 {version}  往返一致 {data['model']['roundTrip']}")


if __name__ == "__main__":
    main()
