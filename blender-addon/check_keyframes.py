"""关键帧增删的端到端校验（需要 Blender）。

做两件事：
1. 在 Blender 里**插入**一个新关键帧 → 导出
2. 再**删掉**一个关键帧 → 导出

判定标准：
- 只有 BONE 块变化，其它 chunk 逐字节不变
- 目标骨骼的关键帧数 +1 / −1，时间落在预期位置
- **其它骨骼的记录逐字节不变**
- 导出的文件能重新导入，关键帧数与预期一致

用法：
    blender --background --python check_keyframes.py -- <model.mdx>
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
from mdx_io.importer import import_mdx  # noqa: E402

PROPS = {"KGTR": ("location", (0, 1, 2)),
         "KGRT": ("rotation_quaternion", (1, 2, 3, 0)),
         "KGSC": ("scale", (0, 1, 2))}


def pick_target(model: Path):
    """挑一条关键帧足够多、间距够大的轨道。"""
    core = get_core()
    from mdxcore.chunks import bone as bonemod
    from mdx_io.importer import bone_names

    mdx = core.load(model)
    if not any(c.tag == "BONE" for c in mdx.chunks):
        return None
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    names = bone_names(bones)
    fps = float(bpy.context.scene.render.fps or 24)

    for b in bones:
        for tag, af in bonemod.parse_tracks(mdx.data(mdx.find("BONE")), b).items():
            if tag not in PROPS or len(af.tracks) < 4:
                continue
            frames = [t.time / 1000.0 * fps for t in af.tracks]
            for i in range(1, len(frames) - 1):
                if frames[i] - frames[i - 1] >= 6 and frames[i + 1] - frames[i] >= 6:
                    return b.index, tag, i, names[b.index], frames
    return None


def bone_records(model: Path):
    core = get_core()
    from mdxcore.chunks import bone as bonemod

    mdx = core.load(model)
    data = mdx.data(mdx.find("BONE"))
    bones = bonemod.parse(data)
    return [data[b.record_offset:b.record_offset + b.size] for b in bones], bones


def verify(original: Path, written: Path, bone_index: int, tag: str,
           expect_count: int) -> list[str]:
    core = get_core()
    from mdxcore.chunks import bone as bonemod
    from mdxcore.verify import check_chunk_rewrite
    import mdx_io.exporter  # noqa: F401

    problems: list[str] = []
    before = original.read_bytes()
    after = written.read_bytes()
    problems.extend(check_chunk_rewrite(before, after, core.load(original), "BONE"))

    records_a, bones_a = bone_records(original)
    records_b, bones_b = bone_records(written)

    if len(bones_a) != len(bones_b):
        problems.append(f"骨骼数变了：{len(bones_a)} → {len(bones_b)}")
        return problems

    for i in range(len(records_a)):
        if i != bone_index and records_a[i] != records_b[i]:
            problems.append(f"骨骼 {i} 的记录被意外改动")

    data_b = core.load(written).data(core.load(written).find("BONE"))
    tracks = bonemod.parse_tracks(data_b, bones_b[bone_index])
    if tag not in tracks:
        problems.append(f"导出后 {tag} 轨道消失了")
        return problems
    got = len(tracks[tag].tracks)
    if got != expect_count:
        problems.append(f"{tag} 关键帧数 {got}，期望 {expect_count}")

    times = [t.time for t in tracks[tag].tracks]
    if any(b <= a for a, b in zip(times, times[1:])):
        problems.append("关键帧时间不再递增")

    return problems


def resolve_targets(argv):
    out = []
    for raw in argv:
        p = Path(raw).resolve()
        if p.is_dir():
            out.extend(sorted(p.rglob("*.mdx")))
        elif p.is_file():
            out.append(p)
    return out


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    targets = resolve_targets(argv)
    if not targets:
        print("用法：blender --background --python check_keyframes.py -- <model | 目录>")
        return 2
    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "keyframes"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    results = [run_one(model, out_dir) for model in targets]
    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for name, ok, lines in results:
        print(f"  {'通过' if ok else '失败'}  {name}")
        for line in lines:
            print(f"        {line}")
    skipped = sum(1 for _, _, lines in results if any("跳过" in l for l in lines))
    passed = sum(1 for _, ok, lines in results
                 if ok and not any("跳过" in l for l in lines))
    total = len(results) - skipped
    print(f"\n通过 {passed}/{total}（跳过 {skipped}）")
    return 0 if passed == total else 1


def run_one(model, out_dir):
    import_mdx(str(model))
    target = pick_target(model)
    if target is None:
        return model.name, True, ["跳过：没有可用的轨道"]

    bone_index, tag, index, bone_name, frames = target
    prop, order = PROPS[tag]
    arm = bpy.data.objects["MDX_Armature"]
    action = arm.animation_data.action
    base = f'pose.bones["{bone_name}"]'
    curves = [find_fcurve(action, arm, f"{base}.{prop}", s) for s in range(len(order))]

    core = get_core()
    from mdxcore.chunks import bone as bonemod
    mdx = core.load(model)
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    original_count = len(bonemod.parse_tracks(mdx.data(mdx.find("BONE")),
                                              bones[bone_index])[tag].tracks)

    results = []

    # ---- 1. 插入关键帧 ----
    new_frame = round((frames[index] + frames[index + 1]) / 2, 3)
    new_time = int(round(new_frame * 1000.0 / float(bpy.context.scene.render.fps or 24)))
    for c in curves:
        kp = c.keyframe_points.insert(new_frame, c.keyframe_points[index].co[1])
        if kp.handle_left_type != "FREE":
            kp.handle_left_type = "FREE"
            kp.handle_right_type = "FREE"
        c.update()

    report, written = export_mdx(str(out_dir / f"{model.stem}.add-key.mdx"))
    problems = list(report["problems"])
    problems += verify(model, Path(written), bone_index, tag, original_count + 1)
    results.append(("插入", problems, report))

    # 插入的关键帧时间是否落地
    data = core.load(written).data(core.load(written).find("BONE"))
    tb = bonemod.parse(data)
    times = [t.time for t in bonemod.parse_tracks(data, tb[bone_index])[tag].tracks]
    # 帧号是 float32，从帧换算回毫秒会有 ±1 的舍入，不能要求完全相等
    if not any(abs(t - new_time) <= 1 for t in times):
        results[-1][1].append(f"新增关键帧附近（{new_time}±1 ms）没有关键帧")

    # ---- 2. 从原始文件重新导入后删一个关键帧 ----
    import_mdx(str(model))
    action = bpy.data.objects["MDX_Armature"].animation_data.action
    curves = [find_fcurve(action, bpy.data.objects["MDX_Armature"],
                          f"{base}.{prop}", s) for s in range(len(order))]
    for c in curves:
        c.keyframe_points.remove(c.keyframe_points[index], fast=True)
        c.update()

    report2, written2 = export_mdx(str(out_dir / f"{model.stem}.del-key.mdx"))
    problems2 = list(report2["problems"])
    problems2 += verify(model, Path(written2), bone_index, tag, original_count - 1)
    results.append(("删除", problems2, report2))

    # ---- 3. 导出的文件能否重新导入 ----
    try:
        meta = import_mdx(str(written))
        if meta.get("animation_keys", 0) <= 0:
            results[0][1].append("重新导入后没有动画关键帧")
    except Exception as exc:                       # noqa: BLE001
        results[0][1].append(f"导出文件无法重新导入：{exc}")

    ok = True
    lines = [f"骨骼 {bone_index} {bone_name} {tag}"]
    for label, probs, rep in results:
        ok = ok and not probs
        lines.append(f"{label}关键帧  {rep['mode']}  ({rep['changed_bytes']} 字节变化)")
        for prob in probs[:5]:
            lines.append(f"! {prob}")
    return model.name, ok, lines


if __name__ == "__main__":
    raise SystemExit(main())
