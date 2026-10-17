"""字节差分：找出两段字节的差异区间，并映射回它属于哪个 chunk。"""

from __future__ import annotations

from dataclasses import dataclass

from .container import MdxFile


@dataclass(frozen=True)
class DiffRange:
    offset: int
    length: int
    a: bytes
    b: bytes

    @property
    def end(self) -> int:
        return self.offset + self.length


def diff_ranges(a: bytes, b: bytes, *, max_ranges: int = 500) -> list[DiffRange]:
    """逐字节比较，把连续的差异合并成区间。"""
    limit = min(len(a), len(b))
    ranges: list[DiffRange] = []
    i = 0

    while i < limit:
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < limit and a[i] != b[i]:
            i += 1
        ranges.append(DiffRange(start, i - start, a[start:i], b[start:i]))
        if len(ranges) >= max_ranges:
            break

    if len(a) != len(b) and len(ranges) < max_ranges:
        tail_start = limit
        ranges.append(
            DiffRange(tail_start, max(len(a), len(b)) - limit,
                      a[tail_start:], b[tail_start:])
        )
    return ranges


def locate(mdx: MdxFile, offset: int) -> tuple[str, int] | None:
    """文件偏移 → (chunk tag, chunk 内数据偏移)。

    落在 chunk 头（tag/size）上时内偏移为 -1；落在文件尾或头部之外返回 None。
    """
    for chunk in mdx.chunks:
        if chunk.header_offset <= offset < chunk.end:
            inner = offset - chunk.data_offset
            return (chunk.tag, inner if inner >= 0 else -1)
    return None
