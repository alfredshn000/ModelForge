"""顶点增删的端到端校验（需要 Blender）。

做两件事，每件都跑完整回路：

1. **增**：在某个三角形上"挤出"3 个新顶点 + 1 个新面
2. **删**：删掉一个被三角形引用的顶点

判定标准：
- 只有 GEOS 块变化，其它 chunk 逐字节不变
- 顶点/三角形数量符合预期
- 属性数组（法线 / UV / 顶点组 / 切线 / 蒙皮）长度随顶点数同步
- 未被修改的其它 geoset 逐字节不变
- 导出的文件能被重新导入，且顶点数一致

用法：
    blender --background --python check_topology.py -- <model.mdx> [geoset]
"""

from __future__ import annotations

import sys
from pathlib import Path

import bmesh
import bpy

ADDON = Path(__file__).resolve().parent
WORKSPACE = ADDON.parent
sys.path.insert(0, str(ADDON))

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402


def object_for(gi: int):
    coll = bpy.data.collections.get("MDX")
    for obj in coll.objects:
        if obj.type == "MESH" and int(obj.get("mdx_geoset_index", -1)) == gi:
            return obj
    return None


def extrude_triangle(obj, offset: float = 6.0) -> tuple[int, int]:
    """复制一个三角形并沿法线外移，返回 (原顶点数, 新顶点数)。"""
    mesh = obj.data
    before = len(mesh.vertices)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()
    uv_layer = bm.loops.layers.uv.active

    src = bm.faces[0]
    normal = src.normal.copy()
    cos = [v.co + normal * offset for v in src.verts]
    fresh = [bm.verts.new(co) for co in cos]
    for new_v, src_v in zip(fresh, src.verts):
        # 权重照抄，否则新面不会跟着骨骼动
        for gidx, weight in src_v[deform].items():
            new_v[deform][gidx] = weight
    bm.verts.ensure_lookup_table()

    face = bm.faces.new(fresh)
    face.smooth = src.smooth
    if uv_layer is not None:
        for loop, src_loop in zip(face.loops, src.loops):
            loop[uv_layer].uv = src_loop[uv_layer].uv

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return before, len(mesh.vertices)


def delete_one_vertex(obj) -> tuple[int, int, int]:
    """删掉第一个三角形的一个顶点（连带所有引用它的面）。

    返回 (原顶点数, 新顶点数, 被连带删掉的面数)。
    一个顶点常被 5–8 个三角形共用，所以"删 1 个顶点"往往要掉好几个面。
    """
    mesh = obj.data
    before = len(mesh.vertices)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    victim = bm.faces[0].verts[0]
    refs = sum(1 for f in bm.faces if victim in f.verts)
    bmesh.ops.delete(bm, geom=[victim], context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return before, len(mesh.vertices), refs


def geoset_bytes(mdx, version: int, gi: int) -> bytes:
    from mdxcore.chunks import geos
    gs = geos.parse(mdx.data(mdx.find("GEOS")), version)
    return geos.build([gs[gi]], version)


def verify(model: Path, written: Path, gi: int, expect_delta: tuple[int, int],
           label: str) -> dict:
    core = get_core()
    from mdxcore.chunks import geos
    from mdxcore.ops import format_version
    from mdxcore.verify import check_chunk_rewrite

    problems: list[str] = []
    before = model.read_bytes()
    after = written.read_bytes()

    mdx_a = core.load(model)
    mdx_b = core.load(written)
    ver = format_version(mdx_a)

    issues = check_chunk_rewrite(before, after, mdx_a, "GEOS")
    problems.extend(issues)

    gs_a = geos.parse(mdx_a.data(mdx_a.find("GEOS")), ver)
    gs_b = geos.parse(mdx_b.data(mdx_b.find("GEOS")), ver)
    if len(gs_a) != len(gs_b):
        problems.append(f"geoset 数变了：{len(gs_a)} → {len(gs_b)}")

    ga, gb = gs_a[gi], gs_b[gi]
    dv = gb.vertex_count - ga.vertex_count
    dt = gb.triangle_count - ga.triangle_count
    if (dv, dt) != expect_delta:
        problems.append(f"顶点/三角变化 {dv:+d}/{dt:+d}，期望 {expect_delta[0]:+d}/{expect_delta[1]:+d}")

    # 属性数组必须与新的顶点数对齐（这正是 geos.validate 检查的东西）
    nv = gb.vertex_count
    attrs = {
        "normals": len(gb.normals),
        "gndx": len(gb.vertex_groups),
        "tangents": None if gb.tangents is None else len(gb.tangents),
        "skin": None if gb.skin is None else len(gb.skin),
        "uv": [len(x) for x in gb.uv_sets],
    }
    expect = {
        "normals": nv,
        "gndx": (0, nv),
        "tangents": None if gb.tangents is None else nv * 4,
        "skin": None if gb.skin is None else nv * 8,
        "uv": [nv] * len(gb.uv_sets),
    }
    for key, got in attrs.items():
        want = expect[key]
        ok = got in want if isinstance(want, tuple) else got == want
        if not ok:
            problems.append(f"{key} 与顶点数不匹配：{got} vs {want}")

    try:
        geos.validate(gb)
    except geos.GeosError as exc:
        problems.append(f"重建后的 geoset 不合法：{exc}")

    # 其它 geoset 必须逐字节不变
    for k in range(len(gs_a)):
        if k == gi:
            continue
        if geoset_bytes(mdx_a, ver, k) != geoset_bytes(mdx_b, ver, k):
            problems.append(f"geoset {k} 被意外改动")

    return {"label": label, "delta": (dv, dt),
            "vertices": gb.vertex_count, "triangles": gb.triangle_count,
            "size": f"{len(before)}→{len(after)}",
            "problems": problems}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print("用法：blender --background --python check_topology.py -- <model.mdx> [geoset]")
        return 2
    model = Path(argv[0]).resolve()
    gi = int(argv[1]) if len(argv) > 1 else 0

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "topology"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    results = []

    for action in ("add", "delete"):
        import_mdx(str(model))
        obj = object_for(gi)
        if obj is None:
            results.append({"label": action, "problems": [f"geoset {gi} 不存在"]})
            continue
        refs = 1
        if action == "add":
            before, after = extrude_triangle(obj)
        else:
            before, after, refs = delete_one_vertex(obj)

        report, written = export_mdx(str(out_dir / f"{model.stem}.{action}.mdx"))
        problems = list(report["problems"])
        expected = (after - before, 1 if action == "add" else -refs)
        r = verify(model, Path(written), gi, expected, action)
        problems.extend(r["problems"])

        # 导出的文件必须还能被导入
        try:
            meta = import_mdx(str(written))
            counts = meta.get("vertex_counts") or []
            reimported = counts[gi] if gi < len(counts) else None
            if reimported != r["vertices"]:
                problems.append(f"重新导入后顶点数 {reimported} ≠ {r['vertices']}")
        except Exception as exc:                    # noqa: BLE001
            problems.append(f"导出文件无法重新导入：{exc}")

        results.append({"label": action,
                        "vertices": r["vertices"],
                        "triangles": r["triangles"],
                        "delta": r["delta"],
                        "size": r["size"],
                        "mode": report["mode"],
                        "problems": problems})
        # 最后一轮把场景恢复成原模型，避免影响后续
        import_mdx(str(model))

    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for r in results:
        status = "通过" if not r.get("problems") else "失败"
        print(f"  {status}  {model.name:<28} {r.get('label', '-'):<7} "
              f"顶点={r.get('vertices', '-')} 三角={r.get('triangles', '-')} "
              f"{r.get('delta', '-')}  {r.get('size', '')}")
        if r.get("mode"):
            print(f"        写出方式：{r['mode']}")
        for prob in r.get("problems", [])[:5]:
            print(f"        ! {prob}")

    ok = all(not r.get("problems") for r in results)
    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
