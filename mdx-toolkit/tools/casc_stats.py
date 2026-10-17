"""CASC 只读能力的实测统计：模型 / 贴图 / 耗时。

用法：
    python tools/casc_stats.py [安装目录] [--sample N]
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mdxcore import load, parse  # noqa: E402
from mdxcore.casc import CascError, open_casc  # noqa: E402
from mdxcore.ops import list_textures  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("install", nargs="?", default=r"D:\Games\War3 2.0\2.0.2.22796\2.0.2.22796\Warcraft III")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    t0 = time.time()
    try:
        casc = open_casc(args.install)
    except CascError as exc:
        print(f"打不开：{exc}")
        return 2
    print(f"版本 {casc.build.version}  分支 {casc.build.branch}")
    print(f"容器 {casc.containers()}")

    files = casc.list_all()
    mdx = [f for f in files if f.lower().endswith(".mdx")]
    print(f"war3.w3mod：{len(files):,} 个文件，其中 {len(mdx):,} 个 .mdx")
    print(f"打开 + 列目录：{time.time() - t0:.2f}s\n")

    random.seed(args.seed)
    picks = random.sample(mdx, min(args.sample, len(mdx)))

    ok = bad = 0
    tex_ok = tex_miss = tex_skip = 0
    versions: dict[int, int] = {}
    read_time = 0.0
    failures: list[tuple[str, str]] = []

    for path in picks:
        t = time.time()
        try:
            data = casc.read("war3.w3mod\\" + path)
        except Exception as exc:                   # noqa: BLE001
            failures.append((path, f"读取失败：{exc}"))
            bad += 1
            continue
        read_time += time.time() - t

        try:
            m = parse(data)
        except Exception as exc:                   # noqa: BLE001
            failures.append((path, f"解析失败：{exc}"))
            bad += 1
            continue
        ok += 1

        if any(c.tag == "VERS" for c in m.chunks):
            ver = int.from_bytes(m.data(m.find("VERS"))[:4], "little")
            versions[ver] = versions.get(ver, 0) + 1

        try:
            texs = list_textures(m)
        except Exception:                          # noqa: BLE001
            continue
        for tex in texs:
            if tex.replaceable_id:
                tex_skip += 1
                continue
            if casc.resolve(tex.path) is None:
                tex_miss += 1
            else:
                tex_ok += 1

    print(f"模型：成功 {ok} / 失败 {bad}（样本 {len(picks)}）")
    print(f"平均读取耗时：{read_time / max(ok, 1) * 1000:.1f} ms")
    print(f"FormatVersion 分布：{dict(sorted(versions.items()))}")
    print(f"贴图：命中 {tex_ok} / 缺失 {tex_miss} / 跳过（可替换ID）{tex_skip}")
    if failures:
        print("\n失败明细：")
        for path, why in failures[:10]:
            print(f"  {path}  {why}")

    total = tex_ok + tex_miss
    if total:
        print(f"\n贴图命中率：{tex_ok / total * 100:.1f}%")
    casc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
