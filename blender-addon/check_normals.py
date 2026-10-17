"""法线的端到端校验（需要 Blender）。

1. 导入后法线必须与文件一致（否则说明自定义法线没写进去）
2. 把某个顶点的法线转一下 → 导出，**只有那 12 字节变**
3. 不改任何东西 → 导出必须字节完全相同

用法：
    blender --background --python check_normals.py -- <model.mdx | 目录> [更多...]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ADDON = Path(__file__).resolve().parent
WORKSPACE = ADDON.parent
sys.path.insert(0, str(ADDON))

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402


def resolve_targets(argv):
    out = []
    for raw in argv:
        p = Path(raw).resolve()
        if p.is_dir():
            out.extend(sorted(p.rglob("*.mdx")))
        elif p.is_file():
            out.append(p)
    return out


def geoset_object(gi: int):
    coll = bpy.data.collections.get("MDX")
    for obj in coll.objects:
        if obj.type == "MESH" and int(obj.get("mdx_geoset_index", -1)) == gi:
            return obj
    return None


def run(model: Path, out_dir: Path) -> dict:
    core = get_core()
    from mdxcore.chunks import geos
    from mdxcore.diff import diff_ranges
    from mdxcore.ops import format_version
    from mdxcore.verify import check_chunk_rewrite

    meta = import_mdx(str(model))
    mdx = core.load(model)
    ver = format_version(mdx)
    if not any(c.tag == "GEOS" for c in mdx.chunks):
        return {"model": model.name, "status": "跳过", "detail": "没有 GEOS"}
    gs = geos.parse(mdx.data(mdx.find("GEOS")), ver)
    if not gs or not gs[0].normals:
        return {"model": model.name, "status": "跳过", "detail": "没有几何"}
    if not meta.get("custom_normals"):
        return {"model": model.name, "status": "跳过", "detail": "未导入自定义法线"}

    obj = geoset_object(0)
    mesh = obj.data
    problems: list[str] = []

    # ---- 1. 导入后法线是否与文件一致 ----
    per_vertex = [None] * len(mesh.vertices)
    for loop in mesh.loops:
        vi = loop.vertex_index
        if per_vertex[vi] is None:
            per_vertex[vi] = tuple(mesh.corner_normals[loop.index].vector)
    worst = 0.0
    for i, n in enumerate(per_vertex):
        if n is None or i >= len(gs[0].normals):
            continue
        src = gs[0].normals[i]
        worst = max(worst, max(abs(n[k] - src[k]) for k in range(3)))
    # 自定义法线在 Blender 里是 2×int16 球面编码，实测解码误差最大 5.3e-3（约 0.3°）。
    # 这是存储格式的上限，不是 bug；作为观测量，超过 1e-2 才说明链路有问题。
    if worst > 1e-2:
        problems.append(f"导入后法线与文件偏差 {worst:.2e}")

    # ---- 2. 不改任何东西 → 必须字节相同 ----
    report0, written0 = export_mdx(str(out_dir / f"{model.stem}.normals-asis.mdx"))
    if Path(written0).read_bytes() != model.read_bytes():
        problems.append(f"未改动却写出了不同字节（{report0['mode']}）")

    # ---- 3. 改一个法线 ----
    target = 0
    normals = [tuple(n) if n else (0.0, 0.0, 1.0) for n in per_vertex]
    # 绕 X 轴转 30°，保证是单位向量
    a = math.radians(30)
    x, y, z = normals[target]
    normals[target] = (x, y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a))
    mesh.normals_split_custom_set_from_vertices(normals)

    report, written = export_mdx(str(out_dir / f"{model.stem}.normals-edit.mdx"))
    before = model.read_bytes()
    after = Path(written).read_bytes()
    problems.extend(report["problems"])

    ranges = diff_ranges(before, after)
    if not ranges:
        problems.append("改了法线但文件没变")
    else:
        base = mdx.find("GEOS").data_offset
        lo, hi = gs[0].normal_span(target)
        lo, hi = base + lo, base + hi
        outside = [(r.offset, r.length) for r in ranges
                   if not (lo <= r.offset and r.end <= hi)]
        if outside:
            problems.append(f"{len(outside)} 段差异落在目标法线之外：{outside[:3]}")

    problems.extend(check_chunk_rewrite(before, after, mdx, "GEOS"))

    return {"model": model.name, "status": "通过" if not problems else "失败",
            "import_dev": worst,
            "normal_edits": report.get("patched_normals", 0),
            "changed_bytes": sum(r.length for r in ranges),
            "problems": problems}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    targets = resolve_targets(argv)
    if not targets:
        print("用法：blender --background --python check_normals.py -- <model | 目录>")
        return 2

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "normals"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    results = []
    for model in targets:
        try:
            results.append(run(model, out_dir))
        except Exception as exc:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            results.append({"model": model.name, "status": "异常", "problems": [str(exc)]})
    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for r in results:
        print(f"  {r['status']:<4} {r['model']:<28} "
              f"导入偏差 {r.get('import_dev', 0):.1e}  "
              f"改法线 {r.get('normal_edits', '-')}  变 {r.get('changed_bytes', '-')}B")
        for prob in r.get("problems", [])[:4]:
            print(f"       ! {prob}")
        if r.get("detail"):
            print(f"       - {r['detail']}")

    passed = sum(1 for r in results if r["status"] == "通过")
    total = sum(1 for r in results if r["status"] != "跳过")
    print(f"\n通过 {passed}/{total}（跳过 {len(results) - total}）")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
