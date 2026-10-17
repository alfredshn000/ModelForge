"""几何 + 动画同时改，确认两条写入路径能共存。

同时做两件事：
1. 移动一个顶点（GEOS 定点覆写）
2. 平移一条关键帧的时间（BONE 定点覆写）

期望：文件长度不变，差异全部落在「该顶点的 12 字节」和「该关键帧的 4 字节」里，
两个 chunk 各改一处，其它 chunk 一个字节都不动。
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ADDON = Path(__file__).resolve().parent
WORKSPACE = ADDON.parent
sys.path.insert(0, str(ADDON))

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.bpy_compat import find_fcurve  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import bone_names, import_mdx  # noqa: E402

PROPS = {"KGTR": ("location", (0, 1, 2)),
         "KGRT": ("rotation_quaternion", (1, 2, 3, 0)),
         "KGSC": ("scale", (0, 1, 2))}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print("用法：blender --background --python check_mixed.py -- <model.mdx>")
        return 2
    model = Path(argv[0]).resolve()

    core = get_core()
    from mdxcore.chunks import bone as bonemod
    from mdxcore.diff import diff_ranges
    from mdxcore.ops import format_version

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "mixed"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    import_mdx(str(model))

    mdx = core.load(model)
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    names = bone_names(bones)
    fps = float(bpy.context.scene.render.fps or 24)

    # 1) 移动 geoset 0 的 0 号顶点
    obj = None
    for cand in bpy.data.collections["MDX"].objects:
        if cand.type == "MESH" and int(cand.get("mdx_geoset_index", -1)) == 0:
            obj = cand
    v = obj.data.vertices[0]
    obj.data.vertices[0].co = (v.co[0], v.co[1], v.co[2] + 3.0)

    # 2) 平移一条关键帧的时间（连手柄一起，避免切线被判改动）
    target = None
    arm = bpy.data.objects["MDX_Armature"]
    action = arm.animation_data.action
    for b in bones:
        for tag, af in bonemod.parse_tracks(mdx.data(mdx.find("BONE")), b).items():
            if tag not in PROPS or not af.tracks:
                continue
            prop, order = PROPS[tag]
            base = f'pose.bones["{names[b.index]}"]'
            curves = [find_fcurve(action, arm, f"{base}.{prop}", s) for s in range(len(order))]
            if any(c is None for c in curves):
                continue
            if len(curves[0].keyframe_points) != len(af.tracks) or len(af.tracks) < 3:
                continue
            frames = [kp.co[0] for kp in curves[0].keyframe_points]
            for i in range(1, len(frames) - 1):
                gap = min(frames[i] - frames[i - 1], frames[i + 1] - frames[i])
                if gap >= 1.0:
                    target = (b, tag, i, curves, min(1.0, gap / 2))
                    break
            if target:
                break
        if target:
            break

    if target is None:
        mdx_io.unregister()
        print("  跳过  没有可用于平移的关键帧")
        return 0

    b, tag, i, curves, delta = target
    for c in curves:
        kp = c.keyframe_points[i]
        kp.co = (kp.co[0] + delta, kp.co[1])
        kp.handle_left = (kp.handle_left[0] + delta, kp.handle_left[1])
        kp.handle_right = (kp.handle_right[0] + delta, kp.handle_right[1])
        c.update()

    before = model.read_bytes()
    report, written = export_mdx(str(out_dir / f"{model.stem}.mixed.mdx"))
    after = Path(written).read_bytes()
    mdx_io.unregister()

    problems = list(report["problems"])
    if len(before) != len(after):
        problems.append(f"文件长度变了：{len(before)} → {len(after)}")
    if report["changed_bytes"] == 0:
        problems.append("文件没有任何变化")

    # 差异必须落在 GEOS 与 BONE 两个块内
    from mdxcore.container import parse as parse_mdx
    re_mdx = parse_mdx(after)
    allowed = []
    for i2, chunk in enumerate(mdx.chunks):
        if chunk.tag in ("GEOS", "BONE"):
            allowed.append((chunk.data_offset, re_mdx.chunks[i2].data_offset,
                            re_mdx.chunks[i2].end))
    ranges = diff_ranges(before, after)
    outside = []
    for r in ranges:
        hit = False
        for _, lo, hi in allowed:
            if lo <= r.offset and r.end <= hi:
                hit = True
                break
        if not hit:
            outside.append((r.offset, r.length))
    if outside:
        problems.append(f"{len(outside)} 段差异落在 GEOS/BONE 之外：{outside[:4]}")

    from mdxcore.verify import check_chunk_rewrite
    problems.extend(check_chunk_rewrite(before, after, mdx, {"GEOS", "BONE"}))

    print(f"\n=== {model.name} ===")
    print(f"  写出方式：{report['mode']}")
    print(f"  几何补丁 {report['patched_vertices']} 个顶点，"
          f"动画改动 {report['animation_edits']} 处，共 {report['changed_bytes']} 字节")
    print(f"  格式版本 {report['format_version']}，fps {report['fps']}")
    for prob in problems[:6]:
        print(f"  ! {prob}")
    ok = not problems
    print("\n" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
