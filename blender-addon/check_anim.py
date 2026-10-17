"""动画校验：导入后核对 Blender 关键帧是否与文件里的轨道一致。

检查项：
1. F-Curve 数量 = 文件里有轨道的骨骼 × 通道数
2. 每条曲线关键帧数量一致
3. 时间（毫秒→帧）与数值逐点一致（旋转按四元数顺序转换）

用法：
    blender --background --python check_anim.py -- <model.mdx | 目录>
"""

import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    print("用法：blender --background --python check_anim.py -- <model.mdx | 目录>")
    raise SystemExit(2)

targets = []
for raw in argv:
    p = Path(raw).resolve()
    if p.is_dir():
        targets.extend(sorted(p.rglob("*.mdx")))
    elif p.is_file():
        targets.append(p)

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.bpy_compat import find_fcurve  # noqa: E402
from mdx_io.importer import bone_names, import_mdx  # noqa: E402

core = get_core()
from mdxcore.chunks import bone as bonemod  # noqa: E402

mdx_io.register()

CHANNELS = {
    "KGTR": ("location", (0, 1, 2)),
    "KGRT": ("rotation_quaternion", (1, 2, 3, 0)),
    "KGSC": ("scale", (0, 1, 2)),
}
# Blender 的关键帧 co 是 float32：帧号到几千时，float32 的间隔已有 ~2e-4，
# 所以容差必须跟量级挂钩，不能用固定绝对值。
def time_ok(a, b):
    return abs(a - b) <= 1e-4 + abs(b) * 2e-6


def value_ok(a, b):
    return abs(a - b) <= 1e-6 + abs(b) * 2e-6

summary = []

for model in targets:
    print(f"\n=== {model.name} ===")
    try:
        meta = import_mdx(str(model))
    except Exception as exc:                        # noqa: BLE001
        print(f"  导入失败：{exc}")
        summary.append((model.name, "导入失败", str(exc)))
        continue

    mdx = core.load(model)
    if not any(c.tag == "BONE" for c in mdx.chunks):
        print("  无骨骼，跳过")
        summary.append((model.name, "无骨骼", ""))
        continue

    bone_data = mdx.data(mdx.find("BONE"))
    bones = bonemod.parse(bone_data)
    names = bone_names(bones)
    fps = bpy.context.scene.render.fps or 24

    arm = bpy.data.objects.get("MDX_Armature")
    action = bpy.data.actions.get("MDX_Main")
    if action is None:
        expected_total = sum(len(af.tracks) for b in bones
                             for af in bonemod.parse_tracks(bone_data, b).values())
        if expected_total == 0:
            print("  文件里没有骨骼动画，跳过")
            summary.append((model.name, "无动画", ""))
        else:
            print("  失败：文件有动画但没有生成 Action")
            summary.append((model.name, "未通过", "未生成 Action"))
        continue

    problems = []
    checked_curves = 0
    checked_keys = 0

    for b in bones:
        tracks = bonemod.parse_tracks(bone_data, b)
        if not tracks:
            continue
        path_base = f'pose.bones["{names[b.index]}"]'
        for tag, af in tracks.items():
            if tag not in CHANNELS or not af.tracks:
                continue
            prop, order = CHANNELS[tag]
            for slot, comp in enumerate(order):
                fc = find_fcurve(action, arm, f"{path_base}.{prop}", slot)
                if fc is None:
                    problems.append(f"缺少曲线 {names[b.index]}.{prop}[{slot}]")
                    continue
                checked_curves += 1
                n = len(fc.keyframe_points)
                if n != len(af.tracks):
                    problems.append(
                        f"{names[b.index]}.{prop}[{slot}] 关键帧 {n} ≠ {len(af.tracks)}")
                    continue
                co = [0.0] * (2 * n)
                fc.keyframe_points.foreach_get("co", co)
                for i, t in enumerate(af.tracks):
                    fr = co[2 * i]
                    val = co[2 * i + 1]
                    want_fr = t.time / 1000.0 * fps
                    if not time_ok(fr, want_fr) or not value_ok(val, t.value[comp]):
                        problems.append(
                            f"{names[b.index]}.{prop}[{slot}] 第{i}点 "
                            f"帧 {fr!r} vs {want_fr!r}；"
                            f"值 {val!r} vs {t.value[comp]!r}")
                        break
                checked_keys += n

    print(f"  曲线={checked_curves}  关键帧={checked_keys}  fps={fps}")
    for p in problems[:6]:
        print(f"    ! {p}")
    ok = not problems
    print("  结论：" + ("通过" if ok else f"未通过（{len(problems)} 项）"))
    summary.append((model.name, "通过" if ok else "未通过",
                    f"曲线={checked_curves} 关键帧={checked_keys}"))

print("\n\n=== 汇总 ===")
for name, status, detail in summary:
    print(f"  {status:<6} {name:<32} {detail}")
passed = sum(1 for _, s, _ in summary if s in ("通过", "无骨骼", "无动画"))
print(f"\n通过 {passed}/{len(summary)}")

mdx_io.unregister()
raise SystemExit(0 if passed == len(summary) else 1)
