"""端到端写回验证：在 Blender 里移动一个顶点 → 导出 → 检查文件只变了那一个顶点。

用法：
    blender --background --python check_edit.py -- <model.mdx> <geoset> <vertex> <dx> <dy> <dz>

判定：
1. 文件长度不变
2. 差异只出现在该顶点的 12 字节区间内
3. 重新读回，该顶点坐标 == 原坐标 + (dx,dy,dz)
4. 其余顶点一个都没变
"""

import json
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
WORKSPACE = ADDON_DIR.parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) < 6:
    print("用法：blender --background --python check_edit.py -- <model.mdx> <geoset> <vertex> <dx> <dy> <dz>")
    raise SystemExit(2)

model = Path(argv[0]).resolve()
gi, vi = int(argv[1]), int(argv[2])
delta = tuple(float(x) for x in argv[3:6])

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402

core = get_core()
from mdxcore.chunks import geos  # noqa: E402
from mdxcore.ops import format_version, vertex_spans  # noqa: E402
from mdxcore.diff import diff_ranges  # noqa: E402

mdx_io.register()

print(f"=== 导入 {model.name} ===")
import_mdx(str(model))

obj = bpy.data.objects.get(f"GEOS_{gi}")
if obj is None:
    print(f"找不到 GEOS_{gi}")
    raise SystemExit(2)

v = obj.data.vertices[vi]
before_xyz = tuple(v.co)
v.co = (before_xyz[0] + delta[0], before_xyz[1] + delta[1], before_xyz[2] + delta[2])
obj.data.update()
print(f"移动顶点 [{gi}][{vi}]：{before_xyz} → {tuple(v.co)}")

out_dir = WORKSPACE / "mdx-toolkit" / "out" / "blender-edit"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / f"edit-{model.name}"

print("\n=== 导出 ===")
report, _ = export_mdx(str(out_path))
print(json.dumps({k: report[k] for k in
                  ("mode", "verified", "patched_vertices", "byte_identical", "problems")},
                 ensure_ascii=False, indent=2))

print("\n=== 独立复核（不经 Blender，直接读文件） ===")
src_bytes = model.read_bytes()
dst_bytes = Path(out_path).read_bytes()

mdx_src = core.load(model)
mdx_dst = core.load(out_path)
ver = format_version(mdx_src)
gs_src = geos.parse(mdx_src.data(mdx_src.find("GEOS")), ver)
gs_dst = geos.parse(mdx_dst.data(mdx_dst.find("GEOS")), ver)

ok = True
if len(src_bytes) != len(dst_bytes):
    print(f"  失败：文件长度变化 {len(src_bytes)} → {len(dst_bytes)}")
    ok = False

ranges = diff_ranges(src_bytes, dst_bytes)
spans = vertex_spans(mdx_src, gi, [vi])
inner = [r for r in ranges if any(lo <= r.offset and r.end <= hi for lo, hi in spans)]
outer = [r for r in ranges if r not in inner]
print(f"  差异 {len(ranges)} 段 / {sum(r.length for r in ranges)} 字节；落在目标顶点内 {len(inner)} 段，其外 {len(outer)} 段")
if outer:
    print(f"  失败：有差异落在目标顶点之外：{[(r.offset, r.length) for r in outer]}")
    ok = False

got = gs_dst[gi].vertices[vi]
want = tuple(before_xyz[i] + delta[i] for i in range(3))
dev = max(abs(got[i] - want[i]) for i in range(3))
print(f"  读回坐标 {got}，期望 {want}，偏差 {dev:.3g}")
if dev > 1e-6:
    ok = False

other = 0
for a, b in zip(gs_src[gi].vertices, gs_dst[gi].vertices):
    if a != b:
        other += 1
print(f"  该 geoset 内变化的顶点数：{other}（应为 1）")
if other != 1:
    ok = False

# 其它 geoset 必须完全未变
for k in range(len(gs_src)):
    if k == gi:
        continue
    if gs_src[k].vertices != gs_dst[k].vertices:
        print(f"  失败：geoset {k} 的顶点被意外改动")
        ok = False

print("\n=== 结论 ===")
print("通过：只改了目标顶点，其余字节未动" if ok else "未通过，见上面的失败项")

mdx_io.unregister()
raise SystemExit(0 if ok else 1)
