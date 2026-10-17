"""PIVT 块：轴心点。

布局依据 Retera 的 `PivotPointChunk.java`：

    PIVT = 一串 float，**没有计数字段**，读到块尾为止
    每个轴心点 = 3 个 float = 12 字节
    第 i 个点对应第 i 个节点（顺序见 `nodes.NODE_ORDER`）
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

POINT_SIZE = 12


class PivtError(ValueError):
    """PIVT 块结构不合法。"""


@dataclass
class Pivot:
    index: int
    x: float
    y: float
    z: float

    @property
    def xyz(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


def parse(data: bytes) -> list[Pivot]:
    if len(data) % POINT_SIZE:
        raise PivtError(f"PIVT 长度 {len(data)} 不是 {POINT_SIZE} 的整数倍")
    out: list[Pivot] = []
    for i in range(len(data) // POINT_SIZE):
        x, y, z = struct.unpack_from("<3f", data, i * POINT_SIZE)
        out.append(Pivot(i, x, y, z))
    return out


def build(points: list[Pivot]) -> bytes:
    out = bytearray()
    for p in points:
        out += struct.pack("<3f", p.x, p.y, p.z)
    return bytes(out)


def set_pivot(data: bytes, index: int, xyz: tuple[float, float, float]) -> bytes:
    """只改第 index 个点的 12 字节。"""
    count = len(data) // POINT_SIZE
    if len(data) % POINT_SIZE:
        raise PivtError(f"PIVT 长度 {len(data)} 不是 {POINT_SIZE} 的整数倍")
    if not 0 <= index < count:
        raise PivtError(f"索引 {index} 越界（共 {count} 个轴心点）")
    out = bytearray(data)
    struct.pack_into("<3f", out, index * POINT_SIZE, *xyz)
    return bytes(out)


def pivot_span(index: int) -> tuple[int, int]:
    start = index * POINT_SIZE
    return start, start + POINT_SIZE
