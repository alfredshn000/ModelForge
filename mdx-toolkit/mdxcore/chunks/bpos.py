"""BPOS 块：绑定姿势（Reforged）。

布局依据 Retera 的 `BindPoseChunk.java`：

    u32 matrixCount
    matrixCount × 12 个 float（3×4 仿射矩阵，48 字节）

平移分量位于 m[3] / m[7] / m[11]。

本模块**只读**：写绑定姿势需要保证矩阵仍是合法仿射变换，
属于结构变更，不在这里做。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MATRIX_FLOATS = 12
MATRIX_SIZE = MATRIX_FLOATS * 4


class BposError(ValueError):
    """BPOS 块结构不合法。"""


@dataclass
class BindPoseMatrix:
    index: int
    values: tuple[float, ...]

    @property
    def translation(self) -> tuple[float, float, float]:
        return self.values[3], self.values[7], self.values[11]


def parse(data: bytes) -> list[BindPoseMatrix]:
    if len(data) < 4:
        raise BposError(f"BPOS 长度 {len(data)} 太短")
    count = struct.unpack_from("<I", data, 0)[0]
    expected = 4 + count * MATRIX_SIZE
    if len(data) != expected:
        raise BposError(
            f"BPOS 声明 {count} 个矩阵，应为 {expected} 字节，实际 {len(data)}"
        )
    out: list[BindPoseMatrix] = []
    for i in range(count):
        vals = struct.unpack_from(f"<{MATRIX_FLOATS}f", data, 4 + i * MATRIX_SIZE)
        out.append(BindPoseMatrix(i, vals))
    return out
