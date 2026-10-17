"""SEQS 块：动画序列。

布局依据 Retera 的 `SequenceChunk.java`：

    每条 132 字节，无计数字段，读到块尾为止
    name[80] + intervalStart(u32) + intervalEnd(u32) + moveSpeed(f32)
    + nonLooping(u32) + rarity(f32) + syncPoint(u32) + boundsRadius(f32)
    + minExtent(3×f32) + maxExtent(3×f32)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ENTRY = struct.Struct("<80sIIfIfI f 3f 3f".replace(" ", ""))
ENTRY_SIZE = ENTRY.size  # 132

OFF_INTERVAL = 80
OFF_NON_LOOPING = 92
OFF_MOVE_SPEED = 88


class SeqsError(ValueError):
    """SEQS 块结构不合法。"""


@dataclass
class Sequence:
    name: str = ""
    start: int = 0
    end: int = 0
    move_speed: float = 0.0
    non_looping: int = 0
    rarity: float = 0.0
    sync_point: int = 0
    bounds_radius: float = 0.0
    min_extent: tuple[float, float, float] = field(default_factory=lambda: (0.0, 0.0, 0.0))
    max_extent: tuple[float, float, float] = field(default_factory=lambda: (0.0, 0.0, 0.0))

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def seconds(self) -> float:
        """WC3 动画帧率按 1000 帧/秒计时。"""
        return self.duration / 1000.0


def parse(data: bytes) -> list[Sequence]:
    if len(data) % ENTRY_SIZE:
        raise SeqsError(f"SEQS 长度 {len(data)} 不是 {ENTRY_SIZE} 的整数倍")
    out: list[Sequence] = []
    for i in range(len(data) // ENTRY_SIZE):
        fields = ENTRY.unpack_from(data, i * ENTRY_SIZE)
        name, start, end, speed, non_loop, rarity, sync, radius = fields[:8]
        extents = fields[8:]
        out.append(Sequence(
            name=name.split(b"\x00", 1)[0].decode("latin-1"),
            start=start, end=end, move_speed=speed, non_looping=non_loop,
            rarity=rarity, sync_point=sync, bounds_radius=radius,
            min_extent=tuple(extents[0:3]), max_extent=tuple(extents[3:6]),
        ))
    return out


def build(items: list[Sequence]) -> bytes:
    out = bytearray()
    for s in items:
        raw = s.name.encode("latin-1")[:79]
        out += ENTRY.pack(raw.ljust(80, b"\x00"), s.start, s.end, s.move_speed,
                          s.non_looping, s.rarity, s.sync_point,
                          s.bounds_radius, *s.min_extent, *s.max_extent)
    return bytes(out)


def _count(data: bytes) -> int:
    if len(data) % ENTRY_SIZE:
        raise SeqsError(f"SEQS 长度 {len(data)} 不是 {ENTRY_SIZE} 的整数倍")
    return len(data) // ENTRY_SIZE


def set_interval(data: bytes, index: int, start: int, end: int) -> bytes:
    """只改第 index 条的起止帧，其余字节不动。"""
    n = _count(data)
    if not 0 <= index < n:
        raise SeqsError(f"索引 {index} 越界（共 {n} 条）")
    if start < 0 or end < 0:
        raise SeqsError("帧号不能为负")
    if end <= start:
        raise SeqsError(f"结束帧 {end} 必须大于起始帧 {start}")
    out = bytearray(data)
    struct.pack_into("<II", out, index * ENTRY_SIZE + OFF_INTERVAL, start, end)
    return bytes(out)


def set_non_looping(data: bytes, index: int, non_looping: bool) -> bytes:
    """只改第 index 条的循环标志（1 = 不循环）。"""
    n = _count(data)
    if not 0 <= index < n:
        raise SeqsError(f"索引 {index} 越界（共 {n} 条）")
    out = bytearray(data)
    struct.pack_into("<I", out, index * ENTRY_SIZE + OFF_NON_LOOPING, 1 if non_looping else 0)
    return bytes(out)


def interval_span(index: int) -> tuple[int, int]:
    start = index * ENTRY_SIZE + OFF_INTERVAL
    return start, start + 8


def non_looping_span(index: int) -> tuple[int, int]:
    start = index * ENTRY_SIZE + OFF_NON_LOOPING
    return start, start + 4
