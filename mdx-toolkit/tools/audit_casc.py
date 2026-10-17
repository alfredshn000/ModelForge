"""全量体检：把游戏里每个 mdx 都跑一遍「解析 → 重建 → 逐字节比对」。

只看一件事：**解析器有没有漏字段**。
如果 parse → build 的输出和原始字节完全相同，说明这一块被完整理解了；
只要有任何一个字节不同，就说明还有没读到的字段。

用法：
    python tools/audit_casc.py                 # 全量
    python tools/audit_casc.py --limit 200     # 抽样
    python tools/audit_casc.py --report out.md # 写出报告
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mdxcore import parse  # noqa: E402
from mdxcore.casc import CascError, open_casc  # noqa: E402
from mdxcore.chunks import (  # noqa: E402
    animflag, bone, geoa, geos, modl, mtls, pivt, seqs, texs,
)


# 能「解析 → 重建」逐字节回放的块
ROUNDTRIP = {
    "GEOS": lambda d, v: geos.build(geos.parse(d, v), v),
    "SEQS": lambda d, v: seqs.build(seqs.parse(d)),
    "TEXS": lambda d, v: texs.build(texs.parse(d)),
    "PIVT": lambda d, v: pivt.build(pivt.parse(d)),
}


def audit_one(data: bytes) -> dict:
    """返回单个模型的检查结果。"""
    out = {
        "ok": True,
        "version": None,
        "chunks": 0,
        "failures": [],
        "tracks": 0,
        "bones": 0,
        "tags": Counter(),
        "skin_wide": 0,          # 16 字节/顶点的宽格式
        "skin_narrow": 0,        # 8 字节/顶点
        "tang": 0,
    }
    try:
        mdx = parse(data)
    except Exception as exc:                       # noqa: BLE001
        out["ok"] = False
        out["failures"].append(("parse", f"容器解析失败：{exc}"))
        return out

    out["chunks"] = len(mdx.chunks)
    for c in mdx.chunks:
        out["tags"][c.tag] += 1
    if any(c.tag == "VERS" for c in mdx.chunks):
        out["version"] = int.from_bytes(mdx.data(mdx.find("VERS"))[:4], "little")
    ver = out["version"] or 0

    for tag, fn in ROUNDTRIP.items():
        hits = [c for c in mdx.chunks if c.tag == tag]
        for i, chunk in enumerate(hits):
            raw = mdx.data(chunk)
            try:
                rebuilt = fn(raw, ver)
            except Exception as exc:               # noqa: BLE001
                out["failures"].append((tag, f"#{i} 解析失败：{type(exc).__name__}: {exc}"))
                continue
            if tag == "GEOS":
                try:
                    for g in geos.parse(raw, ver):
                        if g.skin:
                            if len(g.skin) == g.vertex_count * 16:
                                out["skin_wide"] += 1
                            else:
                                out["skin_narrow"] += 1
                        if g.tangents is not None:
                            out["tang"] += 1
                except Exception:                  # noqa: BLE001
                    pass
            if rebuilt != raw:
                diff = sum(1 for a, b in zip(raw, rebuilt) if a != b) + abs(len(raw) - len(rebuilt))
                out["failures"].append(
                    (tag, f"#{i} 重建不一致：{len(raw)}→{len(rebuilt)}B，{diff} 字节不同"))

    # GEOA / MTLS 目前只有解析、没有重建，单独统计能否解析
    for tag, fn in (("GEOA", lambda d, v: geoa.parse(d)),
                    ("MTLS", lambda d, v: mtls.parse(d, v))):
        hits = [c for c in mdx.chunks if c.tag == tag]
        for i, chunk in enumerate(hits):
            try:
                fn(mdx.data(chunk), ver)
            except Exception as exc:               # noqa: BLE001
                out["failures"].append((tag, f"#{i} 解析失败：{type(exc).__name__}: {exc}"))

    # 骨骼与动画轨道
    if any(c.tag == "BONE" for c in mdx.chunks):
        raw = mdx.data(mdx.find("BONE"))
        try:
            bones = bone.parse(raw)
            out["bones"] = len(bones)
            for b in bones:
                tracks = bone.parse_tracks(raw, b)
                out["tracks"] += sum(len(af.tracks) for af in tracks.values())
                for af in tracks.values():
                    if animflag.build(af) != raw[af.offset:af.offset + af.size]:
                        out["failures"].append(("BONE", f"{b.name} 的 {af.tag} 重建不一致"))
                        break
            if bone.rebuild_block(raw, bones, {}) != raw:
                out["failures"].append(("BONE", "整块重拼不一致"))
        except Exception as exc:                   # noqa: BLE001
            out["failures"].append(("BONE", f"解析失败：{type(exc).__name__}: {exc}"))

    out["ok"] = not out["failures"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    try:
        from mdxcore.casc import find_install

        install = args.install or find_install()
        if not install:
            print("找不到游戏安装目录")
            return 2
        casc = open_casc(install)
    except CascError as exc:
        print(f"打不开：{exc}")
        return 2

    files = casc.find(r"\.mdx$", limit=99999)
    if args.limit:
        files = files[:args.limit]
    print(f"待检 {len(files):,} 个模型 …")

    versions: Counter = Counter()
    failures: dict[str, list[str]] = defaultdict(list)
    ok = 0
    total_tracks = 0
    total_bones = 0
    t0 = time.time()
    bad_files: list[tuple[str, list]] = []
    tag_counts: Counter = Counter()
    skin_wide = skin_narrow = tang = 0

    for n, path in enumerate(files, 1):
        try:
            data = casc.read("war3.w3mod\\" + path)
        except CascError as exc:
            failures["<读取>"].append(f"{path}: {exc}")
            continue
        r = audit_one(data)
        if r["version"] is not None:
            versions[r["version"]] += 1
        total_tracks += r["tracks"]
        total_bones += r["bones"]
        tag_counts.update(r["tags"])
        skin_wide += r["skin_wide"]
        skin_narrow += r["skin_narrow"]
        tang += r["tang"]
        if r["ok"]:
            ok += 1
        else:
            bad_files.append((path, r["failures"]))
            for tag, why in r["failures"]:
                failures[tag].append(f"{path}: {why}")

        if n % 250 == 0:
            print(f"  {n:,}/{len(files):,}  通过 {ok:,}  用时 {time.time() - t0:.0f}s")

    casc.close()
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"模型 {len(files):,} 个，通过 {ok:,}，失败 {len(bad_files):,}"
          f"（{ok / max(len(files), 1) * 100:.2f}%）  用时 {elapsed:.0f}s")
    print(f"版本分布：{dict(sorted(versions.items()))}")
    print(f"骨骼合计 {total_bones:,}，动画关键帧合计 {total_tracks:,}")
    print(f"SKIN 宽格式 {skin_wide:,} 个 geoset / 窄格式 {skin_narrow:,} 个；带 TANG 的 {tang:,} 个")
    print("块出现次数：" + "  ".join(f"{k}:{v:,}" for k, v in tag_counts.most_common(12)))

    if failures:
        print("\n失败分类：")
        for tag, items in sorted(failures.items(), key=lambda kv: -len(kv[1])):
            print(f"  {tag:<8} {len(items):>5} 例")
            for line in items[:3]:
                print(f"      {line[:150]}")
            if len(items) > 3:
                print(f"      …还有 {len(items) - 3} 例")

    if args.report:
        out = Path(args.report)
        lines = [
            "# CASC 全量体检",
            "",
            f"- 模型数：{len(files):,}",
            f"- 通过：{ok:,}",
            f"- 失败：{len(bad_files):,}",
            f"- 用时：{elapsed:.0f}s",
            f"- 版本分布：{dict(sorted(versions.items()))}",
            f"- 骨骼合计：{total_bones:,}",
            f"- 关键帧合计：{total_tracks:,}",
            f"- SKIN 宽格式（16B/顶点）：{skin_wide:,} 个 geoset",
            f"- SKIN 窄格式（8B/顶点）：{skin_narrow:,} 个 geoset",
            f"- 带 TANG 的 geoset：{tang:,} 个",
            "",
            "## 检查了什么",
            "",
            "每个模型都跑「解析 → 重建 → 与原始字节逐字节比对」。",
            "重建能对上，就说明这个块里没有遗漏任何字段。",
            "",
            "| 块 | 做法 |",
            "|---|---|",
            "| GEOS | 完整重建（含 LOD / TANG / SKIN / 多套 UV） |",
            "| BONE | 逐条记录重建 + 每条动画轨道重建 |",
            "| SEQS / TEXS / PIVT | 完整重建 |",
            "| GEOA / MTLS | 只验证能否解析（暂无重建实现） |",
            "",
            "## 块出现次数",
            "",
            "| 块 | 次数 |",
            "|---|---|",
        ] + [f"| {k} | {v:,} |" for k, v in tag_counts.most_common()] + [
            "",
            "## 失败分类",
            "",
        ]
        for tag, items in sorted(failures.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"### {tag}（{len(items)} 例）")
            lines.append("")
            lines.extend(f"- {line}" for line in items[:30])
            if len(items) > 30:
                lines.append(f"- …还有 {len(items) - 30} 例")
            lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n报告已写出：{out}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
