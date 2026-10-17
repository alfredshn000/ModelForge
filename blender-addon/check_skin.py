"""蒙皮校验：导入后在 Blender 里核对骨骼与权重是否与文件一致。

检查项：
1. 骨骼数量、名字、父子关系
2. 每个顶点的顶点组权重是否等于文件里的蒙皮数据（SKIN / 矩阵组）
3. 有无顶点漏权重

用法：
    blender --background --python check_skin.py -- <model.mdx> [更多模型...]
"""

import json
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    print("用法：blender --background --python check_skin.py -- <model.mdx | 目录>")
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
from mdx_io.importer import bone_names, import_mdx  # noqa: E402

core = get_core()
from mdxcore import skin  # noqa: E402
from mdxcore.chunks import bone as bonemod  # noqa: E402
from mdxcore.chunks import geos as geosmod  # noqa: E402
from mdxcore.ops import format_version  # noqa: E402

mdx_io.register()

TOL = 1e-6
# 权重在文件里是 u8（/255），往返有量化误差，判定容差取 2/255
WEIGHT_TOL = 2.0 / 255.0
summary = []

for model in targets:
    print(f"\n=== {model.name} ===")
    try:
        import_mdx(str(model))
    except Exception as exc:                        # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"  导入失败：{exc}")
        summary.append((model.name, "导入失败", str(exc)))
        continue

    mdx = core.load(model)
    ver = format_version(mdx)
    if not any(c.tag == "BONE" for c in mdx.chunks):
        print("  无骨骼，跳过")
        summary.append((model.name, "无骨骼", ""))
        continue

    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    geosets = geosmod.parse(mdx.data(mdx.find("GEOS")), ver) if any(
        c.tag == "GEOS" for c in mdx.chunks) else []

    arm = bpy.data.objects.get("MDX_Armature")
    problems = []

    if arm is None:
        problems.append("没有生成 MDX_Armature")
    else:
        if len(arm.data.bones) != len(bones):
            problems.append(f"骨骼数 {len(arm.data.bones)} ≠ 文件 {len(bones)}")

        names = bone_names(bones)
        by_name = {b.name: b for b in arm.data.bones}
        for b in bones:
            mine = names[b.index]
            if mine not in by_name:
                problems.append(f"缺少骨骼 {mine}")
                continue
            parent = bonemod.parent_of(bones, b)
            expect = names[parent.index] if parent else None
            got = by_name[mine].parent.name if by_name[mine].parent else None
            if expect != got:
                problems.append(f"{mine} 的父级是 {got}，应为 {expect}")

    # ---- 权重 ----
    weight_mismatch = 0
    uncovered = 0
    for gi, g in enumerate(geosets):
        obj = bpy.data.objects.get(f"GEOS_{gi}")
        if obj is None:
            problems.append(f"缺少 GEOS_{gi}")
            continue
        expected = skin.influences(g)
        group_names = {vg.index: vg.name for vg in obj.vertex_groups}

        if len(obj.data.vertices) != len(expected):
            problems.append(f"GEOS_{gi} 顶点数不符")
            continue

        for vi, vert in enumerate(obj.data.vertices):
            got = {}
            for ge in vert.groups:
                name = group_names.get(ge.group)
                if name and ge.weight > TOL:
                    got[name] = ge.weight
            want = {}
            for inf in expected[vi]:
                name = None
                # 文件里的 node_index 就是骨骼序号
                if inf.node_index < len(bones):
                    name = names[inf.node_index]
                if name:
                    want[name] = want.get(name, 0.0) + inf.weight

            if not want and not got:
                continue
            if not got:
                uncovered += 1
                continue
            keys = set(want) | set(got)
            bad = any(abs(want.get(k, 0.0) - got.get(k, 0.0)) > WEIGHT_TOL for k in keys)
            if bad:
                weight_mismatch += 1

    if weight_mismatch:
        problems.append(f"{weight_mismatch} 个顶点权重与文件不符")
    if uncovered:
        problems.append(f"{uncovered} 个顶点在 Blender 里没有权重")

    ok = not problems
    print(f"  骨骼={len(bones)}  顶点组={sum(len(o.vertex_groups) for o in bpy.data.objects if o.type == 'MESH')}")
    print(f"  权重不符顶点={weight_mismatch}  漏权重顶点={uncovered}")
    for p in problems[:8]:
        print(f"    ! {p}")
    print("  结论：" + ("通过" if ok else "未通过"))
    summary.append((model.name, "通过" if ok else "未通过",
                    f"权重不符={weight_mismatch} 漏权重={uncovered}"))

print("\n\n=== 汇总 ===")
for name, status, detail in summary:
    print(f"  {status:<6} {name:<32} {detail}")
passed = sum(1 for _, s, _ in summary if s == "通过")
print(f"\n通过 {passed}/{len(summary)}")

mdx_io.unregister()
raise SystemExit(0 if passed == len(summary) else 1)
