"""把场景状态与文件比对，产出定点写入。

分成两层是刻意的：
- `state.py` 只管"用户眼里的值"，不碰文件
- 本模块只管"哪些字段不一样、写到哪个偏移"，不碰 Blender

所有改动都是定长字段覆写，因此**文件长度永远不变**，
校验走 `check_delta`（只允许这些字节不同）就够了，不需要允许整块重建。

拒绝写入的两种情况（宁可不动，也不交坏模型）：
1. HD 贴图槽后面还有动画子块 → 槽位坐标不可信，找不到安全写点
2. 序列起止帧改成 end <= start → 引擎会读到空区间
"""

from __future__ import annotations

from .bridge import get_core

# 浮点比较：文件里是 float32，state 里的值就是从同一串字节解出来的，
# 没动过时应当完全相等。留 1e-7 只为吸收 JSON 往返的理论误差，
# 比 float32 在 1.0 附近的间隔（约 6e-8）略大，不会吞掉真实的微小改动。
FLOAT_EPS = 1e-7


def _diff_float(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) > FLOAT_EPS
    except (TypeError, ValueError):
        return True


def _diff_int(a, b) -> bool:
    try:
        return int(a) != int(b)
    except (TypeError, ValueError):
        return True


def _fixed(text: str, width: int) -> bytes:
    """把文本编码成定长 NUL 填充字段（名字 80 字节、路径 260 字节都用它）。"""
    raw = (text or "").encode("latin-1", "replace")
    if len(raw) > width - 1:
        raise ValueError(f"超过 {width - 1} 字节：{text!r}")
    return raw.ljust(width, b"\x00")


def build(mdx, state: dict):
    """返回 (ChunkEditor, edits, problems, warnings)。

    - `problem`：阻断写入（索引对不上，说明状态与文件已经脱节）
    - `warning`：跳过这一项但继续写别的（例如 HD 槽坐标不可信）
    - `edits`：给人看的改动清单
    """
    from mdxcore.chunks import geoa as geoamod
    from mdxcore.chunks import mtls as mtlsmod
    from mdxcore.chunks import seqs as seqsmod
    from mdxcore.chunks import texs as texsmod
    from mdxcore.writers import ChunkEditor, pack_path_field

    ed = ChunkEditor(mdx)
    edits: list[dict] = []
    problems: list[str] = []
    warnings: list[str] = []

    # ---- TEXS ----
    if state.get("textures") and ed.has("TEXS"):
        file_texs = texsmod.parse(mdx.data(mdx.find("TEXS")))
        for item in state["textures"]:
            i = item["index"]
            if not 0 <= i < len(file_texs):
                problems.append(f"贴图索引 {i} 超出文件里的 {len(file_texs)} 条")
                continue
            old, new = file_texs[i].path, item.get("path", "")
            if old == new:
                continue
            try:
                ed.write_raw("TEXS", i * texsmod.ENTRY_SIZE + texsmod.PATH_FIELD_OFFSET,
                             pack_path_field(new, texsmod.PATH_FIELD_SIZE))
            except ValueError as exc:
                problems.append(f"贴图 {i}: {exc}")
                continue
            edits.append({"kind": "texture", "index": i, "from": old, "to": new})

    # ---- MTLS ----
    version = state.get("format_version") or 800
    if state.get("materials") and ed.has("MTLS"):
        file_mats = mtlsmod.parse(mdx.data(mdx.find("MTLS")), version)
        for m in state["materials"]:
            mi = m["index"]
            if not 0 <= mi < len(file_mats):
                problems.append(f"材质索引 {mi} 超出文件里的 {len(file_mats)} 个")
                continue
            file_layers = file_mats[mi].layers
            for lay in m.get("layers") or []:
                li = lay["index"]
                if not 0 <= li < len(file_layers):
                    problems.append(f"材质 {mi} 图层 {li} 超出文件里的 {len(file_layers)} 层")
                    continue
                f = file_layers[li]

                if _diff_int(lay.get("filter", f.filter_mode), f.filter_mode):
                    ed.write("MTLS", f.record_offset + mtlsmod.OFF_FILTER_MODE,
                             "<I", int(lay["filter"]))
                    edits.append({"kind": "layer_filter", "mat": mi, "layer": li,
                                  "from": f.filter_name, "to": int(lay["filter"])})

                if _diff_float(lay.get("alpha"), f.alpha):
                    alpha = min(1.0, max(0.0, float(lay["alpha"])))
                    ed.write("MTLS", f.record_offset + mtlsmod.OFF_ALPHA, "<f", alpha)
                    edits.append({"kind": "layer_alpha", "mat": mi, "layer": li,
                                  "from": round(f.alpha, 4), "to": round(alpha, 4)})

                if _diff_int(lay.get("texture_id", f.texture_id), f.texture_id):
                    ed.write("MTLS", f.record_offset + mtlsmod.OFF_TEXTURE_ID,
                             "<i", int(lay["texture_id"]))
                    edits.append({"kind": "layer_texture", "mat": mi, "layer": li,
                                  "from": f.texture_id, "to": int(lay["texture_id"])})

                hd = lay.get("hd") or {}
                if hd:
                    if not f.hd_slots:
                        warnings.append(
                            f"材质 {mi} 图层 {li}: 文件里没有 HD 贴图槽，HD 改动已跳过")
                    elif not f.hd_slots_trusted:
                        warnings.append(
                            f"材质 {mi} 图层 {li}: HD 槽后面还有动画子块，"
                            "槽位坐标不可信，HD 改动已跳过（写进去会破坏数据）")
                    else:
                        for name, want in hd.items():
                            slot = f.slot_of_type(mtlsmod.resolve_hd_type(name))
                            if slot is None:
                                warnings.append(
                                    f"材质 {mi} 图层 {li}: 文件里没有 {name} 槽，已跳过")
                                continue
                            if _diff_int(want, slot.texture_id):
                                ed.write("MTLS", slot.offset, "<i", int(want))
                                edits.append({"kind": "hd_texture", "mat": mi, "layer": li,
                                              "slot": name, "from": slot.texture_id,
                                              "to": int(want)})

    # ---- GEOA ----
    if state.get("geoa") and ed.has("GEOA"):
        file_geoa = geoamod.parse(mdx.data(mdx.find("GEOA")))
        for item in state["geoa"]:
            i = item["index"]
            if not 0 <= i < len(file_geoa):
                problems.append(f"几何动画索引 {i} 超出文件里的 {len(file_geoa)} 条")
                continue
            f = file_geoa[i]
            if _diff_float(item.get("alpha"), f.alpha):
                alpha = min(1.0, max(0.0, float(item["alpha"])))
                ed.write("GEOA", f.record_offset + geoamod.OFF_ALPHA, "<f", alpha)
                edits.append({"kind": "geoset_alpha", "index": i, "geoset": f.geoset_id,
                              "from": round(f.alpha, 4), "to": round(alpha, 4)})
            color = item.get("color") or list(f.color)
            if any(_diff_float(color[k], f.color[k]) for k in range(3)):
                c = [min(1.0, max(0.0, float(color[k]))) for k in range(3)]
                ed.write("GEOA", f.record_offset + geoamod.OFF_COLOR, "<3f", *c)
                edits.append({"kind": "geoset_color", "index": i, "geoset": f.geoset_id,
                              "from": [round(x, 4) for x in f.color],
                              "to": [round(x, 4) for x in c]})

    # ---- SEQS ----
    if state.get("sequences") and ed.has("SEQS"):
        file_seqs = seqsmod.parse(mdx.data(mdx.find("SEQS")))
        for item in state["sequences"]:
            i = item["index"]
            if not 0 <= i < len(file_seqs):
                problems.append(f"序列索引 {i} 超出文件里的 {len(file_seqs)} 条")
                continue
            f = file_seqs[i]
            start, end = int(item.get("start", f.start)), int(item.get("end", f.end))
            if (start, end) != (f.start, f.end):
                if start < 0 or end <= start:
                    problems.append(
                        f"序列 {i}（{f.name}）: 起止 {start}–{end} 不合法，结束必须大于起始")
                else:
                    ed.write("SEQS", i * seqsmod.ENTRY_SIZE + seqsmod.OFF_INTERVAL,
                             "<II", start, end)
                    edits.append({"kind": "sequence_interval", "index": i, "name": f.name,
                                  "from": [f.start, f.end], "to": [start, end]})
            loop = bool(item.get("non_looping", f.non_looping))
            if int(loop) != int(bool(f.non_looping)):
                ed.write("SEQS", i * seqsmod.ENTRY_SIZE + seqsmod.OFF_NON_LOOPING,
                         "<I", 1 if loop else 0)
                edits.append({"kind": "sequence_looping", "index": i, "name": f.name,
                              "from": not bool(f.non_looping), "to": not loop})

    # ---- PIVT ----
    # 轴心点是 12 字节一个的扁平数组，改它同样不改变文件长度。
    if state.get("pivots") and ed.has("PIVT"):
        from mdxcore.chunks import pivt as pivtmod

        points = pivtmod.parse(mdx.data(mdx.find("PIVT")))
        for item in state["pivots"]:
            i = item["index"]
            if not 0 <= i < len(points):
                problems.append(f"轴心点索引 {i} 超出文件里的 {len(points)} 个")
                continue
            old = [points[i].x, points[i].y, points[i].z]
            xyz = item.get("xyz") or list(old)
            if any(_diff_float(xyz[k], old[k]) for k in range(3)):
                v = [float(xyz[k]) for k in range(3)]
                ed.write("PIVT", i * 12, "<3f", *v)
                edits.append({"kind": "pivot", "index": i,
                              "name": item.get("node", ""),
                              "from": [round(x, 4) for x in old],
                              "to": [round(x, 4) for x in v]})

    # ---- ATCH（附着点） ----
    if state.get("attachments") and ed.has("ATCH"):
        from mdxcore.chunks import atch as atchmod

        file_atc = atchmod.parse(mdx.data(mdx.find("ATCH")))
        for item in state["attachments"]:
            i = item["index"]
            if not 0 <= i < len(file_atc):
                problems.append(f"附着点索引 {i} 超出文件里的 {len(file_atc)} 条")
                continue
            f = file_atc[i]

            name = item.get("name", f.name)
            if (name or "") != f.name:
                try:
                    ed.write_raw("ATCH", f.name_offset, _fixed(name, 80))
                except ValueError as exc:
                    warnings.append(f"附着点 {i} 改名跳过：{exc}")
                else:
                    edits.append({"kind": "attach_name", "index": i,
                                  "from": f.name, "to": name})

            path = item.get("path", f.path)
            if (path or "") != f.path:
                try:
                    ed.write_raw("ATCH", f.path_offset,
                                 _fixed(path, atchmod.PATH_LEN))
                except ValueError as exc:
                    warnings.append(f"附着点 {i} 改挂载路径跳过：{exc}")
                else:
                    edits.append({"kind": "attach_path", "index": i, "name": f.name,
                                  "from": f.path, "to": path})

            aid = item.get("attachment_id", f.attachment_id)
            if _diff_int(aid, f.attachment_id):
                ed.write("ATCH", f.id_offset, "<I", int(aid))
                edits.append({"kind": "attach_id", "index": i, "name": f.name,
                              "from": f.attachment_id, "to": int(aid)})

    return ed, edits, problems, warnings

def summarize(edits: list[dict]) -> str:
    if not edits:
        return ""
    kinds = {
        "texture": "贴图路径",
        "layer_filter": "FilterMode",
        "layer_alpha": "图层 alpha",
        "layer_texture": "图层贴图",
        "hd_texture": "HD 槽贴图",
        "geoset_alpha": "geoset alpha",
        "geoset_color": "geoset 颜色",
        "sequence_interval": "序列区间",
        "sequence_looping": "序列循环",
    }
    tally: dict[str, int] = {}
    for e in edits:
        name = kinds.get(e["kind"], e["kind"])
        tally[name] = tally.get(name, 0) + 1
    return "、".join(f"{k} {v} 处" for k, v in tally.items())
