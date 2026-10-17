"""容器层 + 语义层自测。

验收标准：透传无损、解析可逆、写入只改目标字段。

用法：python tools/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from mdxcore import MdxFormatError, diff_ranges, load, parse, rebuild  # noqa: E402
from mdxcore import skin
from mdxcore import tangents  # noqa: E402
from mdxcore.chunks import animflag, bone, geos, modl, mtls, seqs, texs  # noqa: E402
from mdxcore.ops import (  # noqa: E402
    format_version, geoset_alpha_span, layer_alpha_span, layer_filter_span,
    layer_hd_texture_span, layer_texture_span, list_bind_pose, list_geoset_animations,
    list_geosets, list_materials, list_pivots,
    model_name_span, pivot_span, rename_model, rename_texture, rename_textures_bulk,
    sequence_interval_span, set_geoset_alpha, set_layer_alpha, set_layer_filter,
    set_layer_hd_texture, set_layer_texture, set_pivot, set_sequence_interval, set_vertex,
    texture_span, vertex_span,
    texture_spans_bulk,
)
from mdxcore.ops import add_triangle, add_vertex, delete_vertex, patch_tracks  # noqa: E402
from mdxcore.ops import (add_keyframe, delete_keyframe, list_soft_tracks,  # noqa: E402
                         patch_soft_tracks, soft_spans)
from mdxcore.ops import track_spans  # noqa: E402
from mdxcore.ops import apply_mesh_patches, mesh_spans  # noqa: E402
from mdxcore.ops import animation_tracks  # noqa: E402
from mdxcore.verify import abs_span, check_chunk_rewrite, check_delta  # noqa: E402

DIRS = [
    WORKSPACE / "ReteraModelStudio_0.04.7a9" / "stock" / "particles",
    WORKSPACE / "test-models",
]

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def collect() -> list[Path]:
    out: list[Path] = []
    for d in DIRS:
        if d.is_dir():
            out += sorted(p for p in d.rglob("*.mdx"))
    return out


def tag_of(mdx, tag: str):
    hits = [i for i, c in enumerate(mdx.chunks) if c.tag == tag]
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    files = collect()
    if not files:
        print("找不到任何样本")
        return 2
    print(f"样本 {len(files)} 个\n")

    print("[1] 容器：解析 → 重建 == 原始")
    for path in files:
        raw = path.read_bytes()
        out = rebuild(parse(raw))
        check(path.name, out == raw,
              f"差异 {sum(r.length for r in diff_ranges(raw, out))} 字节")

    print("\n[2] 空替换（用原始数据覆盖自身）→ 输出必须完全不变")
    for path in files:
        raw = path.read_bytes()
        mdx = parse(raw)
        reps = {}
        for tag in ("TEXS", "SEQS", "MODL"):
            i = tag_of(mdx, tag)
            if i is not None:
                reps[i] = mdx.data(mdx.chunks[i])
        if not reps:
            continue
        check(path.name, rebuild(mdx, reps) == raw)

    print("\n[3] 语义层：解析 → 重建 == 原始")
    for path in files:
        mdx = load(path)
        for tag, parser, builder in (
            ("TEXS", texs.parse, texs.build),
            ("SEQS", seqs.parse, seqs.build),
        ):
            i = tag_of(mdx, tag)
            if i is None:
                continue
            data = mdx.data(mdx.chunks[i])
            items = parser(data)
            check(f"{path.name} · {tag}({len(items)})", builder(items) == data)
        i = tag_of(mdx, "MODL")
        if i is not None:
            data = mdx.data(mdx.chunks[i])
            rebuilt = modl.build(modl.parse(data))
            # MODL 的语义重建会规范化 name 字段（清掉尾部残留字节），
            # 所以只要求差异全部落在 name[0:336] 内，而不是逐字节相同。
            ranges = diff_ranges(data, rebuilt)
            ok = all(
                r.offset >= modl.OFF_NAME and r.end <= modl.OFF_NAME + 336
                for r in ranges
            )
            check(f"{path.name} · MODL（差异仅限 name 字段）", ok,
                  f"越界段 {[(r.offset, r.length) for r in ranges]}")

    print("\n[3b] GEOS：解析 → 重建必须逐字节相同")
    for path in files:
        mdx = load(path)
        i = tag_of(mdx, "GEOS")
        if i is None:
            continue
        data = mdx.data(mdx.chunks[i])
        ver = int.from_bytes(mdx.data(mdx.chunks[tag_of(mdx, "VERS")])[:4], "little")
        try:
            gs = geos.parse(data, ver)
        except geos.GeosError as exc:
            check(f"{path.name} · GEOS", False, str(exc))
            continue
        verts = sum(g.vertex_count for g in gs)
        tris = sum(g.triangle_count for g in gs)
        check(f"{path.name} · GEOS {len(gs)} 块 / {verts} 顶点 / {tris} 三角",
              geos.build(gs, ver) == data)

    print("\n[3c] 骨骼与蒙皮")
    for path in files:
        mdx = load(path)
        bi = tag_of(mdx, "BONE")
        if bi is not None:
            bones = bone.parse(mdx.data(mdx.chunks[bi]))
            # 父级可以指向非骨骼节点（辅助/附着点等），所以只有在该模型
            # 完全没有其它节点块时，"父级找不到"才算异常。
            has_other_nodes = any(c.tag in ("HELP", "ATCH", "LITE", "PRE2", "PREM",
                                            "RIBB", "EVTS", "CLID", "CORN")
                                  for c in mdx.chunks)
            orphans = [b.name for b in bones
                       if b.parent_id >= 0 and bone.parent_of(bones, b) is None]
            ok = len(bones) > 0 and all(b.name for b in bones)
            if not has_other_nodes:
                ok = ok and not orphans
            check(f"{path.name} · BONE {len(bones)} 根（非骨骼父级 {len(orphans)}）", ok)
        gi = tag_of(mdx, "GEOS")
        if gi is None or bi is None:
            continue
        bones = bone.parse(mdx.data(mdx.chunks[bi]))
        vers = int.from_bytes(mdx.data(mdx.chunks[tag_of(mdx, "VERS")])[:4], "little")
        gs = geos.parse(mdx.data(mdx.chunks[gi]), vers)
        empty = 0
        bad_sum = 0
        over = 0
        for g in gs:
            for infl in skin.influences(g):
                if not infl:
                    empty += 1
                    continue
                total = sum(i.weight for i in infl)
                if abs(total - 1.0) > 0.01:
                    bad_sum += 1
                if any(i.node_index >= len(bones) for i in infl):
                    over += 1
        check(f"{path.name} · 蒙皮（无权重 {empty} / 权重和异常 {bad_sum} / 越界 {over}）",
              empty == 0 and bad_sum == 0 and over == 0)

    print("\n[3d] 骨骼动画轨道")
    for path in files:
        mdx = load(path)
        bi = tag_of(mdx, "BONE")
        if bi is None:
            continue
        data = mdx.data(mdx.chunks[bi])
        bones = bone.parse(data)
        total = 0
        bad_interp = 0
        neg_time = 0
        seq_end = 0
        si = tag_of(mdx, "SEQS")
        if si is not None:
            seqs_list = seqs.parse(mdx.data(mdx.chunks[si]))
            seq_end = max([x.end for x in seqs_list], default=0)
        for b in bones:
            for af in bone.parse_tracks(data, b).values():
                total += len(af.tracks)
                if af.interp not in (0, 1, 2, 3):
                    bad_interp += 1
                neg_time += sum(1 for t in af.tracks if t.time < 0)
        if total:
            check(f"{path.name} · 轨道 {total} 条（interp 异常 {bad_interp} / 负时间 {neg_time}）",
                  bad_interp == 0 and neg_time == 0)

    print("\n[3e] 切线换算（WC3 值/毫秒 ↔ Blender 值/帧）")
    import random
    rng = random.Random(20261017)
    worst = 0.0
    for _ in range(5000):
        n = rng.randint(2, 8)
        frames = sorted(rng.uniform(0, 300) for _ in range(n))
        i = rng.randrange(n)
        v = rng.uniform(-50, 50)
        tin, tout = rng.uniform(-8, 8), rng.uniform(-8, 8)
        fps = rng.choice([24.0, 30.0, 60.0])
        left, right = tangents.to_handles(frames, i, v, tin, tout, fps)
        a, b = tangents.from_handles(frames, i, v, left, right, fps)
        worst = max(worst, abs(a - tin), abs(b - tout))
    check(f"手柄往返误差 {worst:.2e}（5000 组随机）", worst < 1e-6)

    frames = [0.0, 10.0, 20.0]
    left, right = tangents.to_handles(frames, 1, 3.0, 0.5, -0.2, 24.0)
    moved = tangents.from_handles([5.0, 15.0, 20.0], 1, 3.0,
                                  (left[0] + 5, left[1]), (right[0] + 5, right[1]), 24.0)
    check("关键帧连手柄一起平移后切线不变",
          abs(moved[0] - 0.5) < 1e-9 and abs(moved[1] + 0.2) < 1e-9, str(moved))

    print("\n[3f] 动画轨道：重建可逆 + 字段级写入")
    for path in files:
        mdx = load(path)
        bi = tag_of(mdx, "BONE")
        if bi is None:
            continue
        data = mdx.data(mdx.chunks[bi])
        bones = bone.parse(data)
        flags = 0
        bad = 0
        for b in bones:
            for af in bone.parse_tracks(data, b).values():
                flags += 1
                if animflag.build(af) != data[af.offset:af.offset + af.size]:
                    bad += 1
        if flags:
            check(f"{path.name} · {flags} 个轨道块重建一致", bad == 0, f"{bad} 个不一致")

        # 找一个有 KGTR 且有 Hermite/Bezier 切线的骨骼，改一条 track 的时间
        target = None
        for b in bones:
            tracks = bone.parse_tracks(data, b)
            if "KGTR" in tracks and tracks["KGTR"].tracks:
                target = (b, tracks["KGTR"])
                break
        if target is None:
            continue
        b, af = target
        before_bytes = path.read_bytes()
        item = {"bone": b.index, "tag": "KGTR", "index": 0,
                "time": af.tracks[0].time + 1}
        after_bytes = rebuild(mdx, patch_tracks(mdx, [item]))
        spans = track_spans(mdx, [item])
        problems = check_delta(before_bytes, after_bytes, mdx, spans)
        changed = sum(r.length for r in diff_ranges(before_bytes, after_bytes))
        check(f"{path.name} · 改 1 条轨道时间（变 {changed}B）",
              not problems and changed <= 4, "; ".join(problems))

        if af.has_tangents:
            item = {"bone": b.index, "tag": "KGTR", "index": 0,
                    "out_tan": tuple(v + 0.25 for v in af.tracks[0].out_tan)}
            after_bytes = rebuild(mdx, patch_tracks(mdx, [item]))
            problems = check_delta(before_bytes, after_bytes, mdx,
                                   track_spans(mdx, [item]))
            check(f"{path.name} · 改 outTan 只动切线字段", not problems,
                  "; ".join(problems))

    print("\n[3g] 关键帧增删：加一个再删掉必须还原原文件")
    for path in files:
        mdx = load(path)
        bi = tag_of(mdx, "BONE")
        if bi is None:
            continue
        data = mdx.data(mdx.chunks[bi])
        bones = bone.parse(data)
        target = None
        for b in bones:
            for tag, af in bone.parse_tracks(data, b).items():
                if len(af.tracks) >= 2:
                    target = (b, tag, af)
                    break
            if target:
                break
        if target is None:
            continue
        b, tag, af = target
        t0, t1 = af.tracks[0].time, af.tracks[1].time
        if t1 - t0 < 2:
            continue
        new_time = (t0 + t1) // 2
        before = path.read_bytes()

        added = rebuild(mdx, add_keyframe(mdx, b.index, tag, new_time,
                                          af.tracks[0].value))
        problems = check_chunk_rewrite(before, added, mdx, "BONE")
        m2 = parse(added)
        tracks2 = animation_tracks(m2, b.index)[tag]
        ok_count = len(tracks2.tracks) == len(af.tracks) + 1
        check(f"{path.name} · 插入关键帧 {len(af.tracks)}→{len(tracks2.tracks)}",
              not problems and ok_count, "; ".join(problems))

        idx = next((i for i, t in enumerate(tracks2.tracks) if t.time == new_time), -1)
        restored = rebuild(m2, delete_keyframe(m2, b.index, tag, idx))
        check(f"{path.name} · 删回后逐字节还原", restored == before,
              f"长度 {len(restored)} vs {len(before)}")

    print("\n[3h] 非节点轨道（材质图层 / 几何动画）")
    for path in files:
        mdx = load(path)
        rows = list_soft_tracks(mdx)
        if not rows:
            continue
        r = rows[0]
        item = {"kind": r["kind"], "tag": r["tag"], "index": 0}
        if r["kind"] == "layer":
            item.update(mat=r["mat"], layer=r["layer"])
        else:
            item.update(record=r["record"])
        comps = animflag.components(r["tag"])
        item["value"] = tuple(0.5 for _ in range(comps))
        before = path.read_bytes()
        try:
            after = rebuild(mdx, patch_soft_tracks(mdx, [item]))
        except Exception as exc:                   # noqa: BLE001
            check(f"{path.name} · 轨道 {r['tag']} 写入", False, str(exc))
            continue
        problems = check_delta(before, after, mdx, soft_spans(mdx, [item]))
        changed = sum(x.length for x in diff_ranges(before, after))
        check(f"{path.name} · {len(rows)} 条轨道，改 1 条变 {changed}B",
              not problems and changed <= comps * 4, "; ".join(problems))

    print("\n[4] 写入：差异必须全部落在目标字段内")
    for path in files:
        mdx = load(path)
        before = path.read_bytes()

        i = tag_of(mdx, "GEOS")
        if i is not None:
            ver = format_version(mdx)
            gs_now = geos.parse(mdx.data(mdx.chunks[i]), ver)
            if gs_now and gs_now[0].normals:
                n0 = gs_now[0].normals[0]
                patches_n = {0: {0: (n0[0], n0[1], n0[2] + 0.1)}}
                with_normal = rebuild(mdx, apply_mesh_patches(mdx, {}, patches_n))
                problems = check_delta(before, with_normal, mdx,
                                       mesh_spans(mdx, {}, patches_n))
                check(f"{path.name} · 法线定点写回", not problems, "; ".join(problems))

        i = tag_of(mdx, "TEXS")
        if i is not None and len(mdx.data(mdx.chunks[i])) >= texs.ENTRY_SIZE:
            ci, span = texture_span(mdx, 0)
            after = rebuild(mdx, rename_texture(mdx, 0, "Textures/_selftest_.blp"))
            problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
            check(f"{path.name} · TEXS 路径", not problems, "; ".join(problems))

        i = tag_of(mdx, "SEQS")
        if i is not None and len(mdx.data(mdx.chunks[i])) >= seqs.ENTRY_SIZE:
            cur = seqs.parse(mdx.data(mdx.chunks[i]))[0]
            ci, span = sequence_interval_span(mdx, 0)
            after = rebuild(mdx, set_sequence_interval(mdx, 0, cur.start, cur.end + 100))
            problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
            check(f"{path.name} · SEQS 区间", not problems, "; ".join(problems))

        i = tag_of(mdx, "MODL")
        if i is not None:
            ci, span = model_name_span(mdx)
            after = rebuild(mdx, rename_model(mdx, "Selftest"))
            problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
            check(f"{path.name} · MODL 名字", not problems, "; ".join(problems))

        i = tag_of(mdx, "MTLS")
        if i is not None:
            mats = list_materials(mdx)
            layers = sum(len(m.layers) for m in mats)
            check(f"{path.name} · MTLS 解析（{len(mats)} 材质 / {layers} 图层）",
                  len(mats) > 0)
            if mats and mats[0].layers:
                ci, span = layer_alpha_span(mdx, 0, 0)
                after = rebuild(mdx, set_layer_alpha(mdx, 0, 0, 0.5))
                problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
                check(f"{path.name} · MTLS alpha", not problems, "; ".join(problems))

                lay = mats[0].layers[0]
                ver = format_version(mdx)
                if ver >= 1100:
                    check(f"{path.name} · HD 槽 {len(lay.hd_slots)} 个",
                          len(lay.hd_slots) in (0, 6))
                    slot = lay.slot_of_type(0)
                    if slot is not None and lay.hd_slots_trusted:
                        new_id = 0 if slot.texture_id != 0 else 1
                        ci, span = layer_hd_texture_span(mdx, 0, 0, 0)
                        after = rebuild(mdx, set_layer_hd_texture(mdx, 0, 0, 0, new_id))
                        problems = check_delta(before, after, mdx,
                                               [abs_span(mdx, ci, span)])
                        check(f"{path.name} · MTLS HD Diffuse 槽", not problems,
                              "; ".join(problems))
                else:
                    new_id = 0 if lay.texture_id != 0 else 1
                    ci, span = layer_texture_span(mdx, 0, 0)
                    after = rebuild(mdx, set_layer_texture(mdx, 0, 0, new_id))
                    problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
                    check(f"{path.name} · MTLS 前缀 tex", not problems, "; ".join(problems))

        i = tag_of(mdx, "GEOA")
        if i is not None:
            items = list_geoset_animations(mdx)
            check(f"{path.name} · GEOA {len(items)} 条", len(items) > 0)
            if items:
                new_alpha = 0.5 if abs(items[0].alpha - 0.5) > 1e-6 else 0.25
                ci, span = geoset_alpha_span(mdx, 0)
                after = rebuild(mdx, set_geoset_alpha(mdx, 0, new_alpha))
                problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
                check(f"{path.name} · GEOA alpha", not problems, "; ".join(problems))

        i = tag_of(mdx, "PIVT")
        if i is not None:
            points, slots, note = list_pivots(mdx)
            check(f"{path.name} · PIVT {len(points)} 点", len(points) > 0)
            named = sum(1 for s_ in slots if s_)
            # 一个块都自证不了时，宁可不给名字也不给错名字 —— 这种情况下不苛求命名。
            # （之前这条断言能过，是因为旧实现会把某个尾长"走通"后读出假名字。）
            if named:
                check(f"{path.name} · PIVT 命名 {named}/{len(points)}", True)
            else:
                check(f"{path.name} · PIVT 命名（无自证块，跳过）",
                      "已命名 0/" in note)
            first = points[0]
            ci, span = pivot_span(mdx, 0)
            after = rebuild(mdx, set_pivot(mdx, 0, (first.x + 1.0, first.y, first.z)))
            problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
            check(f"{path.name} · PIVT 写入", not problems, "; ".join(problems))

        i = tag_of(mdx, "BPOS")
        if i is not None:
            mats = list_bind_pose(mdx)
            data_len = len(mdx.data(mdx.chunks[i]))
            check(f"{path.name} · BPOS {len(mats)} 矩阵",
                  data_len == 4 + len(mats) * 48)

        i = tag_of(mdx, "GEOS")
        if i is not None:
            gs = list_geosets(mdx)
            if gs and gs[0].vertex_count:
                v0 = gs[0].vertices[0]
                ci, span = vertex_span(mdx, 0, 0)
                after = rebuild(mdx, set_vertex(mdx, 0, 0, (v0[0], v0[1], v0[2] + 1.0)))
                problems = check_delta(before, after, mdx, [abs_span(mdx, ci, span)])
                check(f"{path.name} · GEOS 顶点写回", not problems, "; ".join(problems))

        i = tag_of(mdx, "TEXS")
        if i is not None and len(mdx.data(mdx.chunks[i])) >= texs.ENTRY_SIZE * 2:
            after = rebuild(mdx, rename_textures_bulk(
                mdx, {0: "Textures/_bulk0_.blp", 1: "Textures/_bulk1_.blp"}))
            problems = check_delta(before, after, mdx, texture_spans_bulk(mdx, [0, 1]))
            check(f"{path.name} · 批量改 2 条", not problems, "; ".join(problems))

    print("\n[4b] 结构变更：增删顶点只允许动 GEOS")
    for path in files:
        mdx = load(path)
        gi = tag_of(mdx, "GEOS")
        if gi is None:
            continue
        ver = format_version(mdx)
        gs = geos.parse(mdx.data(mdx.chunks[gi]), ver)
        if not gs or not gs[0].vertices:
            continue
        g0 = gs[0]
        before = path.read_bytes()

        src_v = g0.vertices[0]
        after = rebuild(mdx, add_vertex(mdx, 0, (src_v[0], src_v[1], src_v[2] + 1.0)))
        problems = check_chunk_rewrite(before, after, mdx, "GEOS")
        g2 = geos.parse(parse(after).data(parse(after).find("GEOS")), ver)[0]
        check(f"{path.name} · 增顶点 {g0.vertex_count}→{g2.vertex_count}",
              not problems
              and g2.vertex_count == g0.vertex_count + 1
              and g2.triangle_count == g0.triangle_count,
              "; ".join(problems))

        # 删一个被三角形引用的顶点，三角形应一并减少
        used = g0.faces[0] if g0.faces else 0
        refs = sum(1 for t in range(g0.triangle_count)
                   if used in g0.faces[t * 3:t * 3 + 3])
        after = rebuild(mdx, delete_vertex(mdx, 0, used))
        problems = check_chunk_rewrite(before, after, mdx, "GEOS")
        m2 = parse(after)
        g2 = geos.parse(m2.data(m2.find("GEOS")), ver)[0]
        check(f"{path.name} · 删顶点 {used}（连带 {refs} 个三角形）",
              not problems
              and g2.vertex_count == g0.vertex_count - 1
              and g2.triangle_count == g0.triangle_count - refs,
              "; ".join(problems))

        # 新增一个三角形，必须挂到面组上
        if g0.vertex_count >= 3:
            a, b, c = g0.faces[0], g0.faces[1], g0.faces[2]
            after = rebuild(mdx, add_triangle(mdx, 0, a, b, c))
            problems = check_chunk_rewrite(before, after, mdx, "GEOS")
            m2 = parse(after)
            g2 = geos.parse(m2.data(m2.find("GEOS")), ver)[0]
            check(f"{path.name} · 增三角形 {g0.triangle_count}→{g2.triangle_count}",
                  not problems and g2.triangle_count == g0.triangle_count + 1,
                  "; ".join(problems))

    print("\n[5] 非法输入必须报错，不能静默产出垃圾")
    for name, data in [("空文件", b""), ("错 magic", b"XXXX" + b"\x00" * 8),
                       ("chunk 越界", b"MDLX" + b"VERS" + (9999).to_bytes(4, "little") + b"\x00" * 4)]:
        try:
            parse(data)
            check(name, False, "没有抛异常")
        except MdxFormatError:
            check(name, True)

    for name, fn in [
        ("TEXS 路径超 255", lambda: texs.set_path(b"\x00" * texs.ENTRY_SIZE, 0, "x" * 256)),        ("TEXS 索引越界", lambda: texs.set_path(b"\x00" * texs.ENTRY_SIZE, 5, "a.blp")),
        ("TEXS 长度非整数倍", lambda: texs.parse(b"\x00" * 10)),
        ("SEQS 区间倒置", lambda: seqs.set_interval(b"\x00" * seqs.ENTRY_SIZE, 0, 500, 100)),
        ("SEQS 索引越界", lambda: seqs.set_interval(b"\x00" * seqs.ENTRY_SIZE, 3, 0, 10)),
        ("MODL 名字超长", lambda: modl.set_name(b"\x00" * modl.ENTRY_SIZE, "x" * 336)),
        ("MODL 长度不对", lambda: modl.parse(b"\x00" * 100)),
    ]:
        try:
            fn()
            check(name, False, "没有抛异常")
        except (texs.TexsError, seqs.SeqsError, modl.ModlError):
            check(name, True)

    # 允许范围必须先合并再判断：相邻字段（三条关键帧的 time 紧挨在一起）
    # 上的改动会横跨两个范围，逐段判断会把合法改动误判成越界。
    from mdxcore.verify import merge_spans

    check("span 合并（首尾相接）", merge_spans([(0, 4), (4, 8)]) == [(0, 8)])
    check("span 合并（重叠）", merge_spans([(0, 8), (4, 12)]) == [(0, 12)])
    check("span 合并（分离）", merge_spans([(0, 4), (8, 12)]) == [(0, 4), (8, 12)])
    check("span 合并（乱序输入）", merge_spans([(8, 12), (0, 4), (4, 8)]) == [(0, 12)])

    print()
    if failures:
        print(f"{len(failures)} 项失败：{failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
