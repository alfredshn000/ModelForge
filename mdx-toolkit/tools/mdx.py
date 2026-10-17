"""mdx — 统一的 MDX 参数编辑工具。

所有写操作都走同一条路：
    读入 → 只重写目标 chunk → 校验差异范围 → 写出

用法：
    python tools/mdx.py info  <file>
    python tools/mdx.py tex   <file>                         列出贴图
    python tools/mdx.py tex   <file> --index 0 --path a.blp  改贴图路径
    python tools/mdx.py seq   <file>                         列出动画序列
    python tools/mdx.py seq   <file> --index 0 --start 0 --end 1000
    python tools/mdx.py seq   <file> --index 0 --noloop
    python tools/mdx.py model <file> --name NewName
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mdxcore import load, parse, rebuild  # noqa: E402
from mdxcore.chunks import modl, mtls, seqs, texs  # noqa: E402
from mdxcore import ops  # noqa: E402
from mdxcore.chunks import animflag  # noqa: E402
from mdxcore.ops import (  # noqa: E402
    OpError, animation_tracks, bone_list, extrude_triangle, format_version,
    list_soft_tracks, patch_soft_tracks, soft_spans, soft_tracks,
    geoset_alpha_span,
    geoset_color_span, patch_tracks, track_spans,
    layer_alpha_span, layer_filter_span, layer_hd_texture_span, layer_texture_span,
    list_bind_pose, list_geoset_animations, list_geosets, list_materials, list_pivots,
    list_sequences, list_textures,
    model_header, model_name_span, rename_model, rename_texture, rename_textures_bulk,
    sequence_interval_span, sequence_looping_span, set_geoset_alpha, set_geoset_color,
    set_layer_alpha, set_layer_filter, set_layer_hd_texture, set_layer_texture,
    set_vertex, vertex_span,
    pivot_span, set_pivot, set_sequence_interval, set_sequence_looping,
    texture_span, texture_spans_bulk,
)
from mdxcore.verify import abs_span, check_delta  # noqa: E402

DEFAULT_OUT = ROOT / "out"


# ---------- 输出 ----------

def _write_result(src: Path, before: bytes, replacements, spans, out: Path | None) -> int:
    mdx = parse(before)
    after = rebuild(mdx, replacements)
    problems = check_delta(before, after, mdx, spans)

    if problems:
        print("校验失败，未写出：")
        for p in problems:
            print(f"  - {p}")
        return 1

    from mdxcore.diff import diff_ranges
    changed = sum(r.length for r in diff_ranges(before, after))
    target = out or (DEFAULT_OUT / f"{src.stem}.edited.mdx")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(after)
    print(f"已写出 {target}")
    print(f"  大小 {len(before)} → {len(after)} B（不变）")
    print(f"  变化 {changed} 字节（{changed / len(before) * 100:.4f}%）")
    print(f"  允许范围 {spans}")
    return 0


# ---------- 子命令 ----------

def cmd_info(path: Path) -> int:
    mdx = load(path)
    print(f"文件   {path.name}  ({len(mdx.raw)} B)")
    version = None
    for c in mdx.chunks:
        if c.tag == "VERS":
            version = int.from_bytes(mdx.data(c)[:4], "little")
    print(f"版本   FormatVersion {version}")
    try:
        print(f"模型名 {model_header(mdx).name!r}")
    except OpError:
        pass
    try:
        tex = list_textures(mdx)
        files = sum(1 for t in tex if not t.is_replaceable)
        print(f"贴图   {len(tex)} 条（{files} 个文件 + {len(tex) - files} 个可替换）")
    except OpError:
        pass
    try:
        seq = list_sequences(mdx)
        total = sum(s.duration for s in seq) / 1000
        print(f"动画   {len(seq)} 条，合计 {total:.2f} 秒")
    except OpError:
        pass
    print(f"块     {len(mdx.chunks)} 个")
    for c in mdx.chunks:
        print(f"  {c.tag}  {c.size:>9} B")
    return 0


def cmd_tex(path: Path, index: int | None, new_path: str | None, out: Path | None) -> int:
    mdx = load(path)
    if new_path is None:
        items = list_textures(mdx)
        print(f"{path.name}  共 {len(items)} 条贴图\n")
        for i, t in enumerate(items):
            kind = f"可替换(rid={t.replaceable_id})" if t.is_replaceable else "文件"
            print(f"  [{i:>2}] {kind:<18} flags={t.flags}  {t.path or '(空)'}")
        return 0
    if index is None:
        print("改路径需要 --index")
        return 2
    before = path.read_bytes()
    chunk_idx, span = texture_span(mdx, index)
    spans = [abs_span(mdx, chunk_idx, span)]
    return _write_result(path, before, rename_texture(mdx, index, new_path), spans, out)


def cmd_seq(path: Path, index: int | None, start: int | None, end: int | None,
            loop: bool | None, out: Path | None) -> int:
    mdx = load(path)
    before = path.read_bytes()

    if index is None:
        items = list_sequences(mdx)
        print(f"{path.name}  共 {len(items)} 条动画\n")
        for i, s in enumerate(items):
            print(f"  [{i:>2}] {s.name:<20} {s.start:>6}-{s.end:<6} "
                  f"{s.duration:>6}ms  {'不循环' if s.non_looping else '循环'}")
        return 0

    if start is not None or end is not None:
        if start is None or end is None:
            print("--start 与 --end 必须同时给出")
            return 2
        chunk_idx, span = sequence_interval_span(mdx, index)
        return _write_result(path, before,
                             set_sequence_interval(mdx, index, start, end),
                             [abs_span(mdx, chunk_idx, span)], out)

    if loop is not None:
        chunk_idx, span = sequence_looping_span(mdx, index)
        return _write_result(path, before,
                             set_sequence_looping(mdx, index, loop),
                             [abs_span(mdx, chunk_idx, span)], out)

    print("需要 --start/--end 或 --loop/--noloop")
    return 2


def cmd_mat(path: Path, mi: int | None, li: int | None, alpha: float | None,
            flt: str | None, tex: int | None, hd_type: str | None,
            out: Path | None) -> int:
    mdx = load(path)
    version = format_version(mdx)
    mats = list_materials(mdx)

    if mi is None:
        try:
            texs_items = list_textures(mdx)
        except OpError:
            texs_items = []
        print(f"{path.name}  FormatVersion {version}  "
              f"材质 {len(mats)} 个，图层 {sum(len(m.layers) for m in mats)} 个\n")
        for i, m in enumerate(mats):
            print(f"材质 [{i}]  priority={m.priority_plane} flags=0x{m.flags:08X} "
                  f"图层={len(m.layers)}  ({m.size}B)")
            for j, lay in enumerate(m.layers):
                if 0 <= lay.texture_id < len(texs_items):
                    tex_path = texs_items[lay.texture_id].path
                elif lay.texture_id < 0:
                    tex_path = "(无贴图)"
                else:
                    tex_path = f"(无效索引 {lay.texture_id})"
                print(f"   [{j}] {lay.filter_name:<11} alpha={lay.alpha:<5.2f} "
                      f"tex={lay.texture_id:<3} {tex_path}")
                if lay.hd_slots:
                    note = "" if lay.hd_slots_trusted else "（不可写：后面带动画子块）"
                    for s in lay.hd_slots:
                        p = (texs_items[s.texture_id].path
                             if 0 <= s.texture_id < len(texs_items) else "(无贴图)")
                        print(f"        HD {s.type_name:<10} tex={s.texture_id:<3} {p}{note}")
        return 0

    if li is None:
        print("需要 --layer")
        return 2

    before = path.read_bytes()
    if alpha is not None:
        ci, span = layer_alpha_span(mdx, mi, li)
        return _write_result(path, before, set_layer_alpha(mdx, mi, li, alpha),
                             [abs_span(mdx, ci, span)], out)
    if flt is not None:
        ci, span = layer_filter_span(mdx, mi, li)
        return _write_result(path, before, set_layer_filter(mdx, mi, li, flt),
                             [abs_span(mdx, ci, span)], out)
    if tex is not None:
        if version >= 1100:
            type_index = mtls.resolve_hd_type(hd_type or "Diffuse")
            ci, span = layer_hd_texture_span(mdx, mi, li, type_index)
            print(f"目标是 HD 槽 {mtls.HD_TEXTURE_TYPES[type_index]}"
                  f"（{version} 的前缀 textureId 是历史字段，改了不生效）")
            return _write_result(
                path, before, set_layer_hd_texture(mdx, mi, li, type_index, tex),
                [abs_span(mdx, ci, span)], out)
        ci, span = layer_texture_span(mdx, mi, li)
        return _write_result(path, before, set_layer_texture(mdx, mi, li, tex),
                             [abs_span(mdx, ci, span)], out)

    print("需要 --alpha / --filter / --tex 之一")
    return 2


def cmd_geoa(path: Path, index: int | None, alpha: float | None,
             color: str | None, out: Path | None) -> int:
    mdx = load(path)
    try:
        items = list_geoset_animations(mdx)
    except OpError:
        print(f"{path.name} 没有 GEOA 块（该模型没有几何动画）")
        return 0

    if index is None:
        print(f"{path.name}  共 {len(items)} 条几何动画\n")
        for i, g in enumerate(items):
            c = ", ".join(f"{v:.2f}" for v in g.color)
            print(f"  [{i:>2}] geosetId={g.geoset_id:<4} alpha={g.alpha:<5.2f} "
                  f"flags=0x{g.flags:08X}  color=({c})  ({g.size}B)")
        return 0

    before = path.read_bytes()
    if alpha is not None:
        ci, span = geoset_alpha_span(mdx, index)
        return _write_result(path, before, set_geoset_alpha(mdx, index, alpha),
                             [abs_span(mdx, ci, span)], out)
    if color is not None:
        parts = [p for p in color.replace(",", " ").split() if p]
        if len(parts) != 3:
            print("--color 需要 3 个分量，例如 1,0,0 或 0.5 0.5 0.5")
            return 2
        rgb = tuple(float(p) for p in parts)
        ci, span = geoset_color_span(mdx, index)
        return _write_result(path, before, set_geoset_color(mdx, index, rgb),
                             [abs_span(mdx, ci, span)], out)

    print("需要 --alpha 或 --color")
    return 2


def cmd_batch(src_dir: Path, tex_from: str | None, tex_to: str | None,
              out_dir: Path | None) -> int:
    if not tex_from or tex_to is None:
        print("batch 需要 --tex-from 与 --tex-to")
        return 2
    if not src_dir.is_dir():
        print(f"目录不存在：{src_dir}")
        return 2

    files = sorted(src_dir.rglob("*.mdx"))
    if not files:
        print(f"{src_dir} 下没有 .mdx")
        return 2

    target = out_dir or (DEFAULT_OUT / "batch")
    done = skipped = failed = 0
    print(f"扫描 {len(files)} 个模型，匹配贴图路径 {tex_from!r}\n")

    for path in files:
        rel = path.relative_to(src_dir)
        try:
            mdx = load(path)
            items = list_textures(mdx)
        except (OpError, Exception):        # noqa: BLE001
            skipped += 1
            continue

        hits = {i: tex_to for i, t in enumerate(items)
                if t.path.lower() == tex_from.lower()}
        if not hits:
            skipped += 1
            continue

        before = path.read_bytes()
        after = rebuild(mdx, rename_textures_bulk(mdx, hits))
        problems = check_delta(before, after, mdx, texture_spans_bulk(mdx, hits))
        if problems:
            print(f"  跳过 {rel}: {'; '.join(problems)}")
            failed += 1
            continue

        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(after)
        print(f"  {rel}: 改 {len(hits)} 条 → {dst.name}")
        done += 1

    print(f"\n完成 {done} / 跳过 {skipped} / 失败 {failed}")
    if done:
        print(f"输出目录：{target}")
    return 0 if failed == 0 else 1


def cmd_pivt(path: Path, index: int | None, xyz: str | None, out: Path | None) -> int:
    mdx = load(path)
    try:
        points, slots, note = list_pivots(mdx)
    except OpError:
        print(f"{path.name} 没有 PIVT 块")
        return 0

    if index is None:
        print(f"{path.name}  {len(points)} 个轴心点")
        print(f"映射  {note}\n")
        for i, p in enumerate(points):
            ref = slots[i]
            label = ref.label if ref else "—"
            print(f"  [{i:>3}] {label:<28} ({p.x:>10.3f}, {p.y:>10.3f}, {p.z:>10.3f})")
        return 0

    if xyz is None:
        print("需要 --xyz x,y,z")
        return 2
    parts = [v for v in xyz.replace(",", " ").split() if v]
    if len(parts) != 3:
        print("--xyz 需要 3 个数，例如 0,0,0 或 -12.5 3 0")
        return 2
    vec = tuple(float(v) for v in parts)

    before = path.read_bytes()
    ci, span = pivot_span(mdx, index)
    return _write_result(path, before, set_pivot(mdx, index, vec),
                         [abs_span(mdx, ci, span)], out)


def _write_structural(src: Path, before: bytes, replacements, tag: str,
                      out: Path | None, note: str) -> int:
    """结构变更写出：长度可能变化，但只允许目标 chunk 动。"""
    from mdxcore.verify import check_chunk_rewrite

    mdx = parse(before)
    after = rebuild(mdx, replacements)
    problems = check_chunk_rewrite(before, after, mdx, tag)
    if problems:
        print("校验失败，未写出：")
        for prob in problems:
            print(f"  - {prob}")
        return 1

    target = out or (DEFAULT_OUT / f"{src.stem}.edited.mdx")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(after)
    print(f"已写出 {target}")
    print(f"  大小 {len(before)} → {len(after)} B（{len(after) - len(before):+d}）")
    print(f"  {note}")
    return 0


def _vec(text: str, n: int = 3) -> tuple:
    parts = [v for v in text.replace(",", " ").split() if v]
    if len(parts) != n:
        raise OpError(f"需要 {n} 个数，收到 {len(parts)} 个")
    return tuple(float(v) for v in parts)


def cmd_geoset(path: Path, args, out: Path | None) -> int:
    mdx = load(path)
    try:
        gs = list_geosets(mdx)
    except OpError:
        print(f"{path.name} 没有 GEOS 块")
        return 0

    listing = (args.geoset is None and args.add_vertex is None
               and args.delete_vertex is None and args.add_tri is None)
    if listing:
        print(f"{path.name}  共 {len(gs)} 个 geoset\n")
        for i, g in enumerate(gs):
            print(f"  [{i:>2}] 顶点={g.vertex_count:<6} 三角={g.triangle_count:<6} "
                  f"材质={g.material_id:<3} UV组={len(g.uv_sets)}  "
                  f"切线={'有' if g.tangents is not None else '无'}  "
                  f"蒙皮={'有' if g.skin is not None else '无'}  "
                  f"包围半径={g.bounds_radius:.2f}")
        return 0

    if args.geoset is None:
        print("增删操作需要 --geoset")
        return 2
    gi = args.geoset
    if not 0 <= gi < len(gs):
        print(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
        return 2

    before = path.read_bytes()
    target = gs[gi]

    try:
        if args.delete_vertex is not None:
            vi = args.delete_vertex
            if not 0 <= vi < target.vertex_count:
                print(f"顶点索引 {vi} 越界（共 {target.vertex_count} 个）")
                return 2
            hit = sum(1 for t in range(target.triangle_count)
                      if vi in target.faces[t * 3:t * 3 + 3])
            after = rebuild(mdx, ops.delete_vertex(mdx, gi, vi))
            m2 = parse(after)
            g2 = list_geosets(m2)[gi]
            return _write_structural(
                path, before, ops.delete_vertex(mdx, gi, vi), "GEOS", out,
                f"删除顶点 {vi}：顶点 {target.vertex_count}→{g2.vertex_count}，"
                f"三角 {target.triangle_count}→{g2.triangle_count}（该顶点被 {hit} 个三角形引用）")

        if args.add_vertex is not None:
            pos = _vec(args.add_vertex)
            source = args.from_index
            after = rebuild(mdx, ops.add_vertex(mdx, gi, pos, source=source))
            m2 = parse(after)
            g2 = list_geosets(m2)[gi]
            return _write_structural(
                path, before, ops.add_vertex(mdx, gi, pos, source=source), "GEOS", out,
                f"新增顶点 {g2.vertex_count - 1} @ {pos}：顶点 "
                f"{target.vertex_count}→{g2.vertex_count}，三角数不变"
                f"（悬空顶点，需 add-tri 才会成面）")

        if args.extrude_tri is not None:
            if not args.offset:
                print("挤出一个三角形需要 --offset")
                return 2
            off = _vec(args.offset)
            after = rebuild(mdx, ops.extrude_triangle(mdx, gi, args.extrude_tri, off))
            m2 = parse(after)
            g2 = list_geosets(m2)[gi]
            return _write_structural(
                path, before, ops.extrude_triangle(mdx, gi, args.extrude_tri, off),
                "GEOS", out,
                f"挤出三角形 {args.extrude_tri} 偏移 {off}：顶点 "
                f"{target.vertex_count}→{g2.vertex_count}，三角 "
                f"{target.triangle_count}→{g2.triangle_count}")

        if args.add_tri is not None:
            a, b, c = (int(v) for v in _vec(args.add_tri))
            after = rebuild(mdx, ops.add_triangle(mdx, gi, a, b, c))
            m2 = parse(after)
            g2 = list_geosets(m2)[gi]
            return _write_structural(
                path, before, ops.add_triangle(mdx, gi, a, b, c), "GEOS", out,
                f"新增三角形 ({a},{b},{c})：三角 "
                f"{target.triangle_count}→{g2.triangle_count}")

        if args.index is None or args.xyz is None:
            print("移动顶点需要 --index 与 --xyz")
            return 2
        ci, span = vertex_span(mdx, gi, args.index)
        return _write_result(path, before, set_vertex(mdx, gi, args.index, _vec(args.xyz)),
                             [abs_span(mdx, ci, span)], out)
    except OpError as exc:
        print(f"错误：{exc}")
        return 2


def cmd_anim(path: Path, args, out: Path | None) -> int:
    mdx = load(path)
    try:
        bones = bone_list(mdx)
    except OpError:
        print(f"{path.name} 没有 BONE 块")
        return 0

    if args.bone is None:
        print(f"{path.name}  共 {len(bones)} 根骨骼（只列有动画的）\n")
        for b in bones:
            tracks = animation_tracks(mdx, b.index)
            if not tracks:
                continue
            summary = "  ".join(
                f"{tag}×{len(af.tracks)}({af.interp_name})" for tag, af in tracks.items())
            print(f"  [{b.index:>3}] {b.name:<32} {summary}")
        return 0

    bi = args.bone
    if not 0 <= bi < len(bones):
        print(f"骨骼索引 {bi} 越界（共 {len(bones)} 根）")
        return 2
    tracks = animation_tracks(mdx, bi)

    if args.tag is None:
        name = bones[bi].name
        print(f"骨骼 [{bi}] {name}\n")
        for tag, af in tracks.items():
            print(f"  {tag}  {len(af.tracks)} 条  {af.interp_name}  "
                  f"分量 {animflag.components(tag)}")
        if not tracks:
            print("  （没有动画轨道）")
        return 0

    if args.tag not in tracks:
        print(f"骨骼 {bi} 没有 {args.tag} 轨道（现有：{', '.join(tracks) or '无'}）")
        return 2
    af = tracks[args.tag]

    if (args.index is None and args.shift is None and args.value is None
            and not args.add_key and args.delete_key is None):
        print(f"骨骼 [{bi}] {bones[bi].name}  {args.tag}  {af.interp_name}  "
              f"{len(af.tracks)} 条\n")
        for i, t in enumerate(af.tracks):
            extra = ""
            if t.in_tan is not None:
                extra = (f"  in={tuple(round(v, 3) for v in t.in_tan)}"
                         f" out={tuple(round(v, 3) for v in t.out_tan)}")
            print(f"  [{i:>4}] {t.time:>8} ms  "
                  f"{tuple(round(v, 4) for v in t.value)}{extra}")
        return 0

    before = path.read_bytes()

    # 增删关键帧会改变轨道块长度 → 走结构变更写出
    if args.add_key or args.delete_key is not None:
        tag = args.tag
        if args.delete_key is not None:
            if not 0 <= args.delete_key < len(af.tracks):
                print(f"关键帧索引 {args.delete_key} 越界（共 {len(af.tracks)} 条）")
                return 2
            after = rebuild(mdx, ops.delete_keyframe(mdx, bi, tag, args.delete_key))
            m2 = parse(after)
            left = animation_tracks(m2, bi).get(tag)
            return _write_structural(
                path, before, ops.delete_keyframe(mdx, bi, tag, args.delete_key),
                "BONE", out,
                f"删除关键帧：{tag} {len(af.tracks)} → "
                f"{len(left.tracks) if left else 0} 条")
        if args.time is None or args.value is None:
            print("新增关键帧需要 --time 与 --value")
            return 2
        comps = animflag.components(tag)
        value = _vec(args.value, comps)
        try:
            in_tan = _vec(args.intan, comps) if args.intan else None
            out_tan = _vec(args.outtan, comps) if args.outtan else None
            replacements = ops.add_keyframe(mdx, bi, tag, args.time, value,
                                            in_tan=in_tan, out_tan=out_tan)
        except OpError as exc:
            print(f"错误：{exc}")
            return 2
        m2 = parse(rebuild(mdx, replacements))
        now = animation_tracks(m2, bi)[tag]
        return _write_structural(
            path, before, replacements, "BONE", out,
            f"新增关键帧 @{args.time} ms：{tag} {len(af.tracks)} → {len(now.tracks)} 条")

    items = []
    if args.shift is not None:
        items = [{"bone": bi, "tag": args.tag, "index": i, "time": t.time + args.shift}
                 for i, t in enumerate(af.tracks)]
    else:
        if args.index is None:
            print("改单条轨道需要 --index")
            return 2
        if not 0 <= args.index < len(af.tracks):
            print(f"轨道索引 {args.index} 越界（共 {len(af.tracks)} 条）")
            return 2
        item = {"bone": bi, "tag": args.tag, "index": args.index}
        if args.time is not None:
            item["time"] = args.time
        if args.value is not None:
            item["value"] = _vec(args.value, animflag.components(args.tag))
        if args.intan is not None:
            item["in_tan"] = _vec(args.intan, animflag.components(args.tag))
        if args.outtan is not None:
            item["out_tan"] = _vec(args.outtan, animflag.components(args.tag))
        if len(item) == 4:
            print("没有指定要改的字段（--time / --value / --intan / --outtan）")
            return 2
        items = [item]

    try:
        replacements = patch_tracks(mdx, items)
        spans = track_spans(mdx, items)
    except OpError as exc:
        print(f"错误：{exc}")
        return 2
    return _write_result(path, before, replacements, spans, out)


def _soft_item_from_args(args) -> dict:
    item = {"kind": args.kind, "tag": args.tag}
    if args.kind == "layer":
        if args.mat_index is None or args.layer is None:
            raise OpError("材质图层需要 --mat 与 --layer")
        item.update(mat=args.mat_index, layer=args.layer)
    else:
        if args.record is None:
            raise OpError("几何动画需要 --record")
        item.update(record=args.record)
    return item


def cmd_track(path: Path, args, out: Path | None) -> int:
    """材质图层 / 几何动画的轨道（非节点轨道）。"""
    mdx = load(path)

    if args.kind is None:
        rows = list_soft_tracks(mdx)
        if not rows:
            print(f"{path.name} 没有材质或几何动画轨道")
            return 0
        print(f"{path.name}  非节点轨道 {len(rows)} 条\n")
        for r in rows:
            where = (f"材质 {r['mat']} 图层 {r['layer']}" if r["kind"] == "layer"
                     else f"几何动画 {r['record']}（geoset {r['geoset']}）")
            print(f"  {where:<26} {r['tag']}  {r['count']:>4} 帧  {r['interp']}")
        return 0

    try:
        item = _soft_item_from_args(args)
        tracks = soft_tracks(mdx, args.kind, args.mat_index, args.layer, args.record)
    except OpError as exc:
        print(f"错误：{exc}")
        return 2

    if args.tag is None:
        where = (f"材质 {args.mat_index} 图层 {args.layer}" if args.kind == "layer"
                 else f"几何动画 {args.record}")
        print(f"{where}\n")
        if not tracks:
            print("  （没有轨道）")
        for tag, flag in tracks.items():
            print(f"  {tag}  {len(flag.tracks)} 帧  {flag.interp_name}")
        return 0

    if args.tag not in tracks:
        print(f"没有 {args.tag} 轨道；现有：{', '.join(tracks) or '无'}")
        return 2
    flag = tracks[args.tag]

    if args.index is None and args.value is None and args.time is None:
        print(f"{args.kind} {args.tag}  {flag.interp_name}  {len(flag.tracks)} 帧\n")
        for i, t in enumerate(flag.tracks):
            extra = ""
            if t.in_tan is not None:
                extra = (f"  in={tuple(round(v, 3) for v in t.in_tan)}"
                         f" out={tuple(round(v, 3) for v in t.out_tan)}")
            print(f"  [{i:>4}] {t.time:>8} ms  "
                  f"{tuple(round(v, 4) for v in t.value)}{extra}")
        return 0

    if args.index is None:
        print("改轨道需要 --index")
        return 2
    if not 0 <= args.index < len(flag.tracks):
        print(f"索引 {args.index} 越界（共 {len(flag.tracks)} 帧）")
        return 2

    comps = animflag.components(args.tag)
    item["index"] = args.index
    changed = False
    if args.time is not None:
        item["time"] = args.time
        changed = True
    if args.value is not None:
        item["value"] = _vec(args.value, comps)
        changed = True
    if args.intan is not None:
        item["in_tan"] = _vec(args.intan, comps)
        changed = True
    if args.outtan is not None:
        item["out_tan"] = _vec(args.outtan, comps)
        changed = True
    if not changed:
        print("没有指定要改的字段（--time / --value / --intan / --outtan）")
        return 2

    before = path.read_bytes()
    try:
        replacements = patch_soft_tracks(mdx, [item])
        spans = soft_spans(mdx, [item])
    except OpError as exc:
        print(f"错误：{exc}")
        return 2
    return _write_result(path, before, replacements, spans, out)


def cmd_casc(args) -> int:
    """直接读 Reforged 的 CASC 存储（游戏安装目录）。"""
    from mdxcore.casc import CascError, deepest_install, find_install, open_casc

    raw = args.install or args.file
    install = deepest_install(Path(raw)) if raw else find_install()
    if install is None:
        print("找不到 WC3 Reforged 安装目录；请用 --install 指定（含 .build.info 的那层）")
        return 2

    try:
        casc = open_casc(install)
    except CascError as exc:
        print(f"打不开 {install}：{exc}")
        return 2

    try:
        if not args.find and not args.get:
            files = casc.list_all()
            mdx = [f for f in files if f.lower().endswith(".mdx")]
            dds = [f for f in files if f.lower().endswith(".dds")]
            print(f"安装目录 {install}")
            print(f"版本 {casc.build.version}  分支 {casc.build.branch}")
            print(f"容器 {', '.join(casc.containers())}")
            print(f"war3.w3mod：{len(files):,} 个文件"
                  f"（{len(mdx):,} 个 .mdx / {len(dds):,} 个 .dds）")
            return 0

        if args.find:
            hits = casc.find(args.find)
            if not hits:
                print(f"没有匹配 {args.find!r} 的路径")
                return 1
            print(f"匹配 {len(hits)} 条：")
            for h in hits:
                size = ""
                entry = casc._split("war3.w3mod\\" + h.lower())
                if entry:
                    size = f"{entry[0].files[entry[1]].total_size:>9,} B"
                print(f"  {size}  {h}")
            return 0

        target = args.get
        data = casc.read(target)
        out = Path(args.out) if args.out else Path(target.replace("\\", "_"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        print(f"{target} → {out}（{len(data):,} 字节）")
        return 0
    except CascError as exc:
        print(f"错误：{exc}")
        return 2
    finally:
        casc.close()


def cmd_bpos(path: Path) -> int:
    mdx = load(path)
    try:
        mats = list_bind_pose(mdx)
    except OpError:
        print(f"{path.name} 没有 BPOS 块（非 Reforged 模型，或未使用绑定姿势）")
        return 0

    print(f"{path.name}  绑定姿势 {len(mats)} 个矩阵（只读）\n")
    try:
        points, slots, _ = list_pivots(mdx)
    except OpError:
        points, slots = [], []

    for m in mats:
        ref = slots[m.index] if m.index < len(slots) else None
        label = ref.label if ref else "—"
        t = m.translation
        print(f"  [{m.index:>3}] {label:<28} 平移 ({t[0]:>10.3f}, {t[1]:>10.3f}, {t[2]:>10.3f})")
    return 0


def cmd_model(path: Path, name: str | None, out: Path | None) -> int:
    mdx = load(path)
    if name is None:
        h = model_header(mdx)
        print(f"{path.name}")
        print(f"  name          {h.name!r}")
        print(f"  boundsRadius  {h.bounds_radius}")
        print(f"  blendTime     {h.blend_time}")
        return 0
    before = path.read_bytes()
    chunk_idx, span = model_name_span(mdx)
    return _write_result(path, before, rename_model(mdx, name),
                         [abs_span(mdx, chunk_idx, span)], out)


def main() -> int:
    parser = argparse.ArgumentParser(description="MDX 参数编辑（delta 写入）")
    parser.add_argument("cmd", choices=["info", "tex", "seq", "model", "mat", "geoa", "pivt", "bpos",
                             "geoset", "anim", "track", "batch", "casc"])
    parser.add_argument("file", nargs="?")
    parser.add_argument("--index", type=int)
    parser.add_argument("--path")
    parser.add_argument("--name")
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--mat", type=int, dest="mat_index")
    parser.add_argument("--layer", type=int)
    parser.add_argument("--alpha", type=float)
    parser.add_argument("--filter")
    parser.add_argument("--tex", type=int)
    parser.add_argument("--hd-type", dest="hd_type",
                        help="HD 模型的贴图槽：Diffuse / Normal / ORM / Emissive / TeamColor / Reflections（默认 Diffuse）")
    parser.add_argument("--color", help="GEOA 颜色，如 1,0,0")
    parser.add_argument("--xyz", help="坐标，如 0,0,0")
    parser.add_argument("--geoset", type=int, help="GEOS 索引")
    parser.add_argument("--add-vertex", dest="add_vertex", help="新增顶点坐标 x,y,z")
    parser.add_argument("--from", dest="from_index", type=int,
                        help="新增顶点时克隆哪个顶点的属性（法线/UV/顶点组/切线/蒙皮）")
    parser.add_argument("--delete-vertex", dest="delete_vertex", type=int,
                        help="删除顶点（引用它的三角形会一并丢弃）")
    parser.add_argument("--add-tri", dest="add_tri", help="新增三角形 a,b,c")
    parser.add_argument("--extrude-tri", dest="extrude_tri", type=int,
                        help="克隆第 N 个三角形并平移（配合 --offset）")
    parser.add_argument("--offset", help="挤出偏移，如 0,0,30")
    parser.add_argument("--bone", type=int, help="骨骼索引（anim）")
    parser.add_argument("--tag", help="动画轨道 tag：KGTR / KGRT / KGSC")
    parser.add_argument("--time", type=int, help="关键帧时间（毫秒）")
    parser.add_argument("--value", help="关键帧数值，如 0,0,0")
    parser.add_argument("--intan", help="入切线，如 0,0,0")
    parser.add_argument("--outtan", help="出切线，如 0,0,0")
    parser.add_argument("--shift", type=int, help="该轨道所有关键帧时间整体平移（毫秒）")
    parser.add_argument("--add-key", dest="add_key", action="store_true",
                        help="新增关键帧（需 --time 与 --value）")
    parser.add_argument("--delete-key", dest="delete_key", type=int,
                        help="删除第 N 条关键帧")
    parser.add_argument("--kind", choices=["layer", "geoa"],
                        help="非节点轨道位置：layer（材质图层）/ geoa（几何动画）")
    parser.add_argument("--record", type=int, help="几何动画记录索引（track）")
    parser.add_argument("--tex-from", dest="tex_from", help="batch：要替换的贴图路径")
    parser.add_argument("--tex-to", dest="tex_to", help="batch：替换成的新路径")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true")
    group.add_argument("--noloop", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--install", help="casc：WC3 Reforged 安装目录")
    parser.add_argument("--find", help="casc：按正则搜索路径")
    parser.add_argument("--get", help="casc：导出指定路径（配合 --out）")
    args = parser.parse_args()

    if args.cmd == "casc":
        return cmd_casc(args)

    if not args.file:
        print("缺少文件参数")
        return 2
    path = Path(args.file)
    if args.cmd == "batch":
        if not path.is_dir():
            print(f"目录不存在：{path}")
            return 2
    elif not path.is_file():
        print(f"文件不存在：{path}")
        return 2
    out = Path(args.out) if args.out else None

    try:
        if args.cmd == "info":
            return cmd_info(path)
        if args.cmd == "tex":
            return cmd_tex(path, args.index, args.path, out)
        if args.cmd == "model":
            return cmd_model(path, args.name, out)
        if args.cmd == "mat":
            return cmd_mat(path, args.mat_index, args.layer, args.alpha,
                           args.filter, args.tex, args.hd_type, out)
        if args.cmd == "geoa":
            return cmd_geoa(path, args.index, args.alpha, args.color, out)
        if args.cmd == "geoset":
            return cmd_geoset(path, args, out)
        if args.cmd == "track":
            return cmd_track(path, args, out)
        if args.cmd == "anim":
            return cmd_anim(path, args, out)
        if args.cmd == "bpos":
            return cmd_bpos(path)
        if args.cmd == "pivt":
            return cmd_pivt(path, args.index, args.xyz, out)
        if args.cmd == "batch":
            return cmd_batch(path, args.tex_from, args.tex_to, out)
        loop = True if args.loop else (False if args.noloop else None)
        return cmd_seq(path, args.index, args.start, args.end, loop, out)
    except (OpError, texs.TexsError, seqs.SeqsError, modl.ModlError, mtls.MtlsError) as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
