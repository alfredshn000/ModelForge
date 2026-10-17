"""MODL 块：模型头。

布局依据 Retera 的 `ModelChunk.java`：

    name[336] + unknownNull(u32) + boundsRadius(f32)
    + minExtent(3×f32) + maxExtent(3×f32) + blendTime(u32)   = 372 字节
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ENTRY = struct.Struct("<336s If 3f 3f I".replace(" ", ""))
ENTRY_SIZE = ENTRY.size  # 372

OFF_NAME = 0
OFF_BLEND_TIME = 368
OFF_BOUNDS_RADIUS = 340


class ModlError(ValueError):
    """MODL 块结构不合法。"""


@dataclass
class ModelHeader:
    name: str = ""
    unknown_null: int = 0
    bounds_radius: float = 0.0
    min_extent: tuple[float, float, float] = field(default_factory=lambda: (0.0, 0.0, 0.0))
    max_extent: tuple[float, float, float] = field(default_factory=lambda: (0.0, 0.0, 0.0))
    blend_time: int = 0


def parse(data: bytes) -> ModelHeader:
    if len(data) != ENTRY_SIZE:
        raise ModlError(f"MODL 长度应为 {ENTRY_SIZE}，实际 {len(data)}")
    fields = ENTRY.unpack(data)
    name, unknown, radius = fields[:3]
    extents = fields[3:]
    return ModelHeader(
        name=name.split(b"\x00", 1)[0].decode("latin-1"),
        unknown_null=unknown, bounds_radius=radius,
        min_extent=tuple(extents[0:3]), max_extent=tuple(extents[3:6]),
        blend_time=extents[6],
    )


def build(h: ModelHeader) -> bytes:
    raw = h.name.encode("latin-1")[:335]
    return ENTRY.pack(raw.ljust(336, b"\x00"), h.unknown_null, h.bounds_radius,
                      *h.min_extent, *h.max_extent, h.blend_time)


def set_name(data: bytes, name: str) -> bytes:
    """只改 name 字段；字段用 NUL 补齐，尾部残留会被清掉。"""
    if len(data) != ENTRY_SIZE:
        raise ModlError(f"MODL 长度应为 {ENTRY_SIZE}，实际 {len(data)}")
    raw = name.encode("latin-1")
    if len(raw) > 335:
        raise ModlError(f"模型名超过 335 字节（{len(raw)}）")
    out = bytearray(data)
    out[OFF_NAME:OFF_NAME + 336] = raw.ljust(336, b"\x00")
    return bytes(out)


def name_span() -> tuple[int, int]:
    return OFF_NAME, OFF_NAME + 336
