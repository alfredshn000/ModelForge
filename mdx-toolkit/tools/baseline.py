"""P0 基线：用现成 Retera（自带 JRE）做 open -> save，字节级比对，产出保真度报告。

不写任何产品代码，只回答一个问题：现引擎往返一趟，会改动哪些字节。

用法（在 mdx-toolkit 目录下）：
    python tools/baseline.py
    python tools/baseline.py --input ../test-models --limit 50
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # mdx-toolkit
WORKSPACE = ROOT.parent                               # TEST APP
RETERA = WORKSPACE / "ReteraModelStudio_0.04.7a9"
WORK = ROOT / "work"
REPORTS = ROOT / "reports"

# 已知字段布局（依据 Retera 的 ModelChunk.java，不是猜的）
FIELD_HINTS: dict[str, list[tuple[str, int, int]]] = {
    "MODL": [
        ("name", 0, 336),
        ("unknownNull", 336, 340),
        ("boundsRadius", 340, 344),
        ("minExtent", 344, 356),
        ("maxExtent", 356, 368),
        ("blendTime", 368, 372),
    ],
    "TEXS": [],  # 无计数字段，每条 268B = replaceableId + path[256] + unknownNull + flags
}


def hint(tag: str, inner: int) -> str:
    if inner < 0:
        return f"{tag} 的 chunk 头（tag/size）"
    for name, start, end in FIELD_HINTS.get(tag, []):
        if start <= inner < end:
            return f"{name}[+{inner - start}]"
    return f"{tag} 数据 +{inner}"


def convert(src: Path, dst: Path, timeout: int = 180) -> tuple[bool, str]:
    """调用 Retera 的命令行转换（自带 JRE，不需要系统装 JDK）。"""
    java = RETERA / "bin" / "java.exe"
    cmd = [
        str(java),
        "-classpath", "lib/*",
        "com.matrixeater.src.MainFrame",
        "-convert", str(src), str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(RETERA), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    except OSError as exc:
        return False, f"启动失败：{exc}"

    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:] or [""]
        return False, f"返回码 {proc.returncode}：{tail[0][:120]}"
    if not dst.exists():
        return False, "没有产出文件"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", default=str(RETERA / "stock" / "particles"),
        help="待测模型目录（递归查找 *.mdx / *.mdl）",
    )
    parser.add_argument("--limit", type=int, default=0, help="只测前 N 个")
    parser.add_argument("--timeout", type=int, default=180, help="单个文件超时秒数")
    args = parser.parse_args()

    src_dir = Path(args.input).resolve()
    if not src_dir.is_dir():
        print(f"目录不存在：{src_dir}")
        return 2

    files = sorted(
        p for p in src_dir.rglob("*")
        if p.suffix.lower() in {".mdx", ".mdl"}
    )
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"{src_dir} 下没有 .mdx / .mdl")
        return 2

    WORK.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    from mdxcore import MdxFormatError, diff_ranges, locate, parse  # noqa: E402

    results = []
    print(f"待测 {len(files)} 个文件，来源 {src_dir}\n")

    for i, src in enumerate(files, 1):
        rel = src.relative_to(src_dir)
        dst = WORK / rel.with_name(rel.stem + ".roundtrip.mdx")
        dst.parent.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        ok, msg = convert(src, dst, args.timeout)
        cost = time.time() - t0

        entry = {
            "file": str(rel).replace("\\", "/"),
            "ok": ok,
            "message": msg,
            "seconds": round(cost, 2),
            "size_in": src.stat().st_size,
            "size_out": dst.stat().st_size if dst.exists() else None,
            "identical": False,
            "diff_bytes": None,
            "ranges": [],
            "chunks": {},
        }

        if ok:
            a = src.read_bytes()
            b = dst.read_bytes()
            entry["identical"] = a == b
            ranges = diff_ranges(a, b)
            entry["diff_bytes"] = sum(r.length for r in ranges)

            try:
                mdx = parse(a)
                entry["chunk_count"] = len(mdx.chunks)
                for r in ranges:
                    where = locate(mdx, r.offset)
                    tag, inner = where if where else ("<file>", r.offset)
                    key = tag if tag == "<file>" else tag
                    entry["chunks"][key] = entry["chunks"].get(key, 0) + r.length
                    entry["ranges"].append({
                        "offset": r.offset,
                        "length": r.length,
                        "a": r.a.hex()[:32],
                        "b": r.b.hex()[:32],
                        "where": f"{tag} {hint(tag, inner)}",
                    })
            except MdxFormatError as exc:
                entry["chunks"]["<解析失败>"] = str(exc)

        mark = "同" if entry["identical"] else ("异" if ok else "错")
        print(
            f"[{i:>2}/{len(files)}] {mark} {entry['file']:<34} "
            f"{entry['size_in']:>7}B  "
            f"{'' if entry['diff_bytes'] is None else str(entry['diff_bytes']) + 'B 差异':<10} "
            f"{cost:.1f}s"
        )
        results.append(entry)

    # ---------- 汇总 ----------
    total = len(results)
    same = sum(1 for r in results if r["identical"])
    failed = sum(1 for r in results if not r["ok"])
    differs = total - same - failed

    chunk_agg: dict[str, int] = {}
    for r in results:
        for k, v in r["chunks"].items():
            chunk_agg[k] = chunk_agg.get(k, 0) + v

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    md = REPORTS / f"baseline-{stamp}.md"
    js = REPORTS / f"baseline-{stamp}.json"

    lines = [
        "# P0 基线报告：现引擎 open → save 保真度",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本目录：`{src_dir}`",
        f"- 引擎：Retera Model Studio（自带 JRE 17，命令行 `-convert`）",
        "",
        "## 总览",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| 样本 | {total} |",
        f"| 字节完全相同 | {same} |",
        f"| 有差异 | {differs} |",
        f"| 转换失败 | {failed} |",
        "",
        "## 明细",
        "",
        "| 文件 | 原始大小 | 往返后 | 差异字节 | 秒 | 判定 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        verdict = "相同" if r["identical"] else ("失败" if not r["ok"] else "有差异")
        lines.append(
            f"| {r['file']} | {r['size_in']} | {r['size_out']} | "
            f"{r['diff_bytes'] if r['diff_bytes'] is not None else '-'} | "
            f"{r['seconds']} | {verdict} |"
        )

    if chunk_agg:
        lines += [
            "",
            "## 差异落点（按字段聚合）",
            "",
            "| 落点 | 累计差异字节 |",
            "|---|---:|",
        ]
        for k, v in sorted(chunk_agg.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {v} |")

    detail = [r for r in results if r["ranges"]]
    if detail:
        lines += ["", "## 逐字节明细（前 40 条）", "", "| 文件 | 偏移 | 长度 | 原始 | 往返后 | 落点 |", "|---|---:|---:|---|---|---|"]
        shown = 0
        for r in detail:
            for d in r["ranges"]:
                lines.append(
                    f"| {r['file']} | {d['offset']} | {d['length']} | "
                    f"`{d['a']}` | `{d['b']}` | {d['where']} |"
                )
                shown += 1
                if shown >= 40:
                    break
            if shown >= 40:
                break

    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(
        {"summary": {"total": total, "identical": same, "differs": differs,
                     "failed": failed, "chunk_agg": chunk_agg},
         "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n相同 {same} / 有差异 {differs} / 失败 {failed}")
    print(f"报告：{md}")
    print(f"数据：{js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
