"""动画切线写回校验（需要 Blender）。

对一个带 Hermite/Bezier 切线的轨道做三种改动，分别确认：

| 测试 | 期望 |
|---|---|
| 关键帧整体平移（含手柄） | **只有那 4 字节的 time 变**，切线逐字节不变 |
| 只改关键帧数值 | 只有 value 段变，切线不动 |
| 只改手柄 y | 只有 outTan 段变 |

用法：
    blender --background --python check_anim_edit.py -- <model.mdx | 目录> [更多...]
"""

from __future__ import annotations

import json
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


def resolve_targets(argv: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in argv:
        p = Path(raw).resolve()
        if p.is_dir():
            out.extend(sorted(p.rglob("*.mdx")))
        elif p.is_file():
            out.append(p)
    return out


def pick_track(model: Path):
    """找一条有 Hermite/Bezier 切线、且关键帧间距够挪动的轨道。

    返回 (骨骼序号, tag, 关键帧序号, 骨骼名, 原始帧列表, 平移量)。平移量按
    相邻间距的一半取（不超过 2 帧），保证挪完顺序不乱。
    """
    core = get_core()
    from mdxcore.chunks import bone as bonemod

    mdx = core.load(model)
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    names = bone_names(bones)
    for b in bones:
        for tag, af in bonemod.parse_tracks(mdx.data(mdx.find("BONE")), b).items():
            if tag not in PROPS or not af.has_tangents or len(af.tracks) < 3:
                continue
            fps = float(bpy.context.scene.render.fps or 24)
            frames = [t.time / 1000.0 * fps for t in af.tracks]
            for i in range(1, len(frames) - 1):
                gap = min(frames[i] - frames[i - 1], frames[i + 1] - frames[i])
                if gap >= 1.0:
                    return b.index, tag, i, names[b.index], frames, min(2.0, gap / 2)
    return None


def shifted(kp, delta: float) -> None:
    """关键帧连同手柄一起平移 —— 斜率不变，切线必须保持不变。"""
    kp.co = (kp.co[0] + delta, kp.co[1])
    kp.handle_left = (kp.handle_left[0] + delta, kp.handle_left[1])
    kp.handle_right = (kp.handle_right[0] + delta, kp.handle_right[1])


def run_case(model: Path, mode: str, out_dir: Path) -> dict:
    core = get_core()
    from mdxcore.chunks import animflag
    from mdxcore.chunks import bone as bonemod
    from mdxcore.diff import diff_ranges

    meta = import_mdx(str(model))
    target = pick_track(model)
    if target is None:
        return {"model": model.name, "mode": mode, "status": "跳过",
                "detail": "没有可用的 Hermite/Bezier 轨道"}

    bone_index, tag, track_index, bone_name, frames, delta = target
    prop, order = PROPS[tag]
    arm = bpy.data.objects["MDX_Armature"]
    action = arm.animation_data.action
    path_base = f'pose.bones["{bone_name}"]'
    curves = [find_fcurve(action, arm, f"{path_base}.{prop}", slot)
              for slot in range(len(order))]
    if any(c is None for c in curves):
        return {"model": model.name, "mode": mode, "status": "失败",
                "detail": "找不到对应的 F-Curve"}

    kp = curves[0].keyframe_points[track_index]
    if mode == "time":
        shifted(kp, delta)
        for c in curves[1:]:
            shifted(c.keyframe_points[track_index], delta)
    elif mode == "value":
        kp.co = (kp.co[0], kp.co[1] + 0.5)
        kp.handle_left = (kp.handle_left[0], kp.handle_left[1] + 0.5)
        kp.handle_right = (kp.handle_right[0], kp.handle_right[1] + 0.5)
    elif mode == "tangent":
        kp.handle_right = (kp.handle_right[0], kp.handle_right[1] + 1.5)
    for c in curves:
        c.update()

    before = model.read_bytes()
    report, written = export_mdx(str(out_dir / f"{model.stem}.{mode}.mdx"))
    after = Path(written).read_bytes()

    # ---- 计算期望的字段范围 ----
    mdx = core.load(model)
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    flags = bonemod.parse_tracks(mdx.data(mdx.find("BONE")), bones[bone_index])
    af = flags[tag]
    track = af.tracks[track_index]
    bone_chunk = mdx.find("BONE")
    base = bone_chunk.data_offset + track.offset

    if mode == "time":
        lo, hi = base, base + 4
        want = int(round((frames[track_index] + delta) * 1000.0 / float(
            bpy.context.scene.render.fps or 24)))
        ok_extra = flags[tag].tracks[track_index + 1].time > want
    elif mode == "value":
        lo, hi = animflag.value_span(track, tag)
        lo += bone_chunk.data_offset
        hi += bone_chunk.data_offset
        ok_extra = True
    else:
        lo, hi = animflag.tan_span(track, tag, "out")
        lo += bone_chunk.data_offset
        hi += bone_chunk.data_offset
        ok_extra = True

    ranges = diff_ranges(before, after)
    outside = [(r.offset, r.length) for r in ranges
               if not (lo <= r.offset and r.end <= hi)]
    problems = list(report["problems"])
    if not ranges:
        problems.append("导出后文件没有任何变化")
    if outside:
        problems.append(f"{len(outside)} 段差异落在字段 {lo}..{hi} 之外：{outside[:4]}")

    # time 模式下，切线字段必须逐字节相同
    tan_untouched = None
    if mode == "time" and af.has_tangents:
        tlo, thi = animflag.tan_span(track, tag, "in")
        olo, ohi = animflag.tan_span(track, tag, "out")
        b0 = bone_chunk.data_offset
        tan_untouched = (before[b0 + tlo:b0 + thi] == after[b0 + tlo:b0 + thi]
                         and before[b0 + olo:b0 + ohi] == after[b0 + olo:b0 + ohi])
        if not tan_untouched:
            problems.append("切线字节被改动了")

    return {
        "model": model.name,
        "mode": mode,
        "status": "通过" if not problems else "失败",
        "bone": bone_name,
        "tag": tag,
        "track": track_index,
        "delta_frames": round(delta, 3),
        "changed_bytes": sum(r.length for r in ranges),
        "allowed_field": f"{lo}..{hi}",
        "tangents_untouched": tan_untouched,
        "mode_of_write": report["mode"],
        "problems": problems,
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    targets = resolve_targets(argv)
    if not targets:
        print("用法：blender --background --python check_anim_edit.py -- <model | 目录>")
        return 2

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "anim-edit"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    results = []
    for model in targets:
        for mode in ("time", "value", "tangent"):
            try:
                results.append(run_case(model, mode, out_dir))
            except Exception as exc:                # noqa: BLE001
                import traceback
                traceback.print_exc()
                results.append({"model": model.name, "mode": mode,
                                "status": "异常", "detail": str(exc)})
    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for r in results:
        extra = ""
        if r.get("tangents_untouched") is not None:
            extra = " 切线未变" if r["tangents_untouched"] else " 切线被改"
        print(f"  {r['status']:<4} {r['model']:<30} {r['mode']:<7} "
              f"改动 {r.get('changed_bytes', '-')}B{extra}")
        for prob in r.get("problems", [])[:3]:
            print(f"       ! {prob}")
        if r.get("detail"):
            print(f"       - {r['detail']}")

    passed = sum(1 for r in results if r["status"] == "通过")
    total = sum(1 for r in results if r["status"] != "跳过")
    print(f"\n通过 {passed}/{total}（跳过 {len(results) - total}）")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
