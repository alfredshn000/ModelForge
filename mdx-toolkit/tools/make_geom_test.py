"""造一个「一定看得见」的几何标记，用来验证顶点/三角形写入在引擎里真的生效。

为什么需要这个：内置的 `--extrude-tri` 是复制已有三角形再平移，位置取决于那个
三角形在哪。实测 BigBookshelf 的 1 号三角形只有 1.5×5 单位，往 +Z 抬 30 之后
正好埋进书架内部——文件结构没错，但肉眼看不到。

所以这里换成**在模型包围盒上方造一个独立的金字塔**：

- 位置在包围盒顶端再往上，不会和本体重叠
- 底面 70×70、高 55，尺度上不可能看漏
- **每个面正反两遍**，无论引擎按哪种绕序剔除背面，都至少有一面朝外
- 法线按径向给，避免整块发黑

用法：
    python tools/make_geom_test.py <model.mdx> --geoset 0 --out out/xxx.mdx
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mdxcore import diff_ranges, load, parse, rebuild  # noqa: E402
from mdxcore.chunks import geos  # noqa: E402
from mdxcore.ops import add_geometry, format_version, geoset_summary  # noqa: E402
from mdxcore.verify import check_chunk_rewrite  # noqa: E402


def pyramid(base_center, half: float, height: float):
    """返回 (positions, triangles, normals)：底面正方形 + 顶点，双面。"""
    cx, cy, z0 = base_center
    b = [(cx - half, cy - half, z0),
         (cx + half, cy - half, z0),
         (cx + half, cy + half, z0),
         (cx - half, cy + half, z0)]
    apex = (cx, cy, z0 + height)
    positions = b + [apex]

    center = (cx, cy, z0 + height * 0.3)

    def radial(p):
        dx, dy, dz = p[0] - center[0], p[1] - center[1], p[2] - center[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        return (dx / length, dy / length, dz / length)

    normals = [radial(p) for p in positions]

    sides = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    base = [(0, 3, 2), (0, 2, 1)]
    tris = []
    for tri in sides + base:
        tris.append(tri)
        tris.append((tri[0], tri[2], tri[1]))      # 反绕序，保证总有一面朝外
    return positions, tris, normals


def main() -> int:
    parser = argparse.ArgumentParser(description="在模型上方加一个金字塔标记")
    parser.add_argument("file")
    parser.add_argument("--geoset", type=int, default=0)
    parser.add_argument("--half", type=float, default=35.0, help="底面半宽")
    parser.add_argument("--height", type=float, default=55.0, help="金字塔高度")
    parser.add_argument("--gap", type=float, default=12.0, help="离模型顶部留空")
    parser.add_argument("--out")
    args = parser.parse_args()

    src = Path(args.file)
    mdx = load(src)
    before = src.read_bytes()
    summary = geoset_summary(mdx)
    if not 0 <= args.geoset < len(summary):
        print(f"geoset 索引 {args.geoset} 越界（共 {len(summary)} 个）")
        return 2

    ver = format_version(mdx)
    g = geos.parse(mdx.data(mdx.find("GEOS")), ver)[args.geoset]
    xs = [v[0] for v in g.vertices]
    ys = [v[1] for v in g.vertices]
    zs = [v[2] for v in g.vertices]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(zs) + args.gap)
    print(f"模型包围盒 x[{min(xs):.1f},{max(xs):.1f}] "
          f"y[{min(ys):.1f},{max(ys):.1f}] z[{min(zs):.1f},{max(zs):.1f}]")
    print(f"标记底面中心 {tuple(round(v, 1) for v in center)}  "
          f"底面 {args.half * 2:.0f}×{args.half * 2:.0f}  高 {args.height:.0f}")

    positions, tris, normals = pyramid(center, args.half, args.height)
    replacements = add_geometry(mdx, args.geoset, positions, tris,
                                source=0, normals=normals)
    after = rebuild(mdx, replacements)

    problems = check_chunk_rewrite(before, after, mdx, "GEOS")
    if problems:
        print("校验失败，未写出：")
        for prob in problems:
            print(f"  - {prob}")
        return 1

    target = Path(args.out) if args.out else ROOT / "out" / f"{src.stem}.marker.mdx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(after)

    m2 = parse(after)
    old, new = summary[args.geoset], geoset_summary(m2)[args.geoset]
    changed = sum(r.length for r in diff_ranges(before, after))
    print(f"\n已写出 {target}")
    print(f"  大小 {len(before)} → {len(after)} B（{len(after) - len(before):+d}）")
    print(f"  顶点 {old['vertices']} → {new['vertices']}，"
          f"三角 {old['triangles']} → {new['triangles']}")
    print(f"  其它 chunk 逐字节不变，差异 {changed} 字节（含后续整体移位）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
