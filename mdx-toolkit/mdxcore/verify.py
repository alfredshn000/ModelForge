"""写入校验：确认一次修改只动了该动的字节。

所有写操作都必须过这一关。它是「不破坏模型」的唯一机器证明。
"""

from __future__ import annotations

from .container import MdxFile, parse
from .diff import diff_ranges

Span = tuple[int, int]          # 文件绝对偏移 [起, 止)


def merge_spans(spans: list[Span]) -> list[Span]:
    """把重叠或首尾相接的范围并起来。

    这一步是必需的：差异是按「连续不同的字节」成段给出来的，
    当两个允许字段紧挨着（例如三条关键帧的 time 字段分别在
    34316–34328 / 34328–34340 / 34340–34352）而改动跨过了分界，
    差异段会横跨两个允许范围。逐段判断"是否完整落在某一个范围里"
    就会把完全合法的改动判成越界——结果是导出静默退回原始字节。
    """
    out: list[Span] = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def check_delta(before: bytes, after: bytes, mdx: MdxFile,
                spans: list[Span]) -> list[str]:
    """返回问题列表；空列表表示通过。"""
    problems: list[str] = []

    if len(before) != len(after):
        problems.append(f"文件长度变化：{len(before)} → {len(after)}")
        return problems

    ranges = diff_ranges(before, after)
    if not ranges:
        problems.append("没有任何差异，修改没有真正写入")
        return problems

    merged = merge_spans(spans)
    for r in ranges:
        if not any(lo <= r.offset and r.end <= hi for lo, hi in merged):
            problems.append(
                f"偏移 {r.offset}（{r.length}B）落在允许范围之外：{merged}"
            )

    try:
        re_mdx = parse(after)
    except Exception as exc:                      # noqa: BLE001
        problems.append(f"写出后无法重新解析：{exc}")
        return problems

    if [c.tag for c in re_mdx.chunks] != [c.tag for c in mdx.chunks]:
        problems.append("chunk 序列发生变化")

    return problems


def abs_span(mdx: MdxFile, chunk_index: int, inner: tuple[int, int]) -> Span:
    """把 chunk 内偏移转换成文件绝对偏移。"""
    base = mdx.chunks[chunk_index].data_offset
    return base + inner[0], base + inner[1]


def check_chunk_rewrite(before: bytes, after: bytes, mdx: MdxFile,
                        tag, expect_change: bool = True) -> list[str]:
    """结构变更（长度可能变化）的校验：只允许目标 chunk 变化。

    用于顶点增删这类会改变块大小的操作——此时 check_delta 的「长度不得变化」
    前提不成立，但"其余 chunk 一个字节都不许动"这条仍然必须成立。
    """
    problems: list[str] = []

    try:
        re_mdx = parse(after)
    except Exception as exc:                      # noqa: BLE001
        return [f"写出后无法重新解析：{exc}"]

    if len(re_mdx.chunks) != len(mdx.chunks):
        return [f"chunk 数变化：{len(mdx.chunks)} → {len(re_mdx.chunks)}"]

    touched: list[str] = []
    for old, new in zip(mdx.chunks, re_mdx.chunks):
        if old.tag != new.tag:
            problems.append(f"chunk 顺序变化：{old.tag} → {new.tag}")
            continue
        a = mdx.data(old)
        b = re_mdx.data(new)
        if a != b:
            touched.append(old.tag)

    if problems:
        return problems

    allowed = {tag} if isinstance(tag, str) else set(tag)
    label = " / ".join(sorted(allowed))
    if expect_change:
        if not (allowed & set(touched)):
            problems.append(f"{label} 没有任何变化")
    extra = [t for t in touched if t not in allowed]
    if extra:
        problems.append(f"{label} 之外的 chunk 被改动：{extra}")

    return problems
