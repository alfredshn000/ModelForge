"""GEOA 块：几何动画（整块 geoset 的透明度 / 颜色）。

布局依据 Retera 的 `GeosetAnimationChunk.java`：

    无计数字段，是一串记录，读到块尾为止
    每条以 inclusiveSize(u32) 开头，固定前缀 28 字节（版本无关）：
        +0  inclusiveSize
        +4  alpha (f32)
        +8  flags (u32)
        +12 color (3×f32)
        +24 geosetId (i32，-1 表示不绑定)
    之后是可选动画子块（KGAO 透明度、KGAC 颜色），本模块不解析，按 inclusiveSize 跳过。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

PREFIX = 28
OFF_ALPHA = 4
OFF_FLAGS = 8
OFF_COLOR = 12
OFF_GEOSET_ID = 24


class GeoaError(ValueError):
    """GEOA 块结构不合法。"""


@dataclass
class GeosetAnimation:
    record_offset: int
    size: int
    alpha: float = 1.0
    flags: int = 0
    color: tuple[float, float, float] = field(default_factory=lambda: (1.0, 1.0, 1.0))
    geoset_id: int = -1


def parse(data: bytes) -> list[GeosetAnimation]:
    out: list[GeosetAnimation] = []
    pos = 0
    total = len(data)
    while pos + PREFIX <= total:
        size = struct.unpack_from("<I", data, pos)[0]
        if size < PREFIX or pos + size > total:
            raise GeoaError(f"GEOA 记录 @{pos} 声明 {size} 字节，超出块长度 {total}")
        alpha = struct.unpack_from("<f", data, pos + OFF_ALPHA)[0]
        flags = struct.unpack_from("<I", data, pos + OFF_FLAGS)[0]
        color = struct.unpack_from("<3f", data, pos + OFF_COLOR)
        geoset_id = struct.unpack_from("<i", data, pos + OFF_GEOSET_ID)[0]
        out.append(GeosetAnimation(pos, size, alpha, flags, tuple(color), geoset_id))
        pos += size
    if pos != total:
        raise GeoaError(f"GEOA 解析结束于 {pos}，块长度为 {total}")
    return out


def _rec(items: list[GeosetAnimation], i: int) -> GeosetAnimation:
    if not 0 <= i < len(items):
        raise GeoaError(f"索引 {i} 越界（共 {len(items)} 条）")
    return items[i]


def set_alpha(data: bytes, items: list[GeosetAnimation], i: int, alpha: float) -> bytes:
    if not 0.0 <= alpha <= 1.0:
        raise GeoaError(f"alpha 必须在 0–1 之间，收到 {alpha}")
    r = _rec(items, i)
    out = bytearray(data)
    struct.pack_into("<f", out, r.record_offset + OFF_ALPHA, alpha)
    return bytes(out)


def set_color(data: bytes, items: list[GeosetAnimation], i: int,
              color: tuple[float, float, float]) -> bytes:
    for c in color:
        if not 0.0 <= c <= 1.0:
            raise GeoaError(f"颜色分量必须在 0–1 之间，收到 {c}")
    r = _rec(items, i)
    out = bytearray(data)
    struct.pack_into("<3f", out, r.record_offset + OFF_COLOR, *color)
    return bytes(out)


def set_flags(data: bytes, items: list[GeosetAnimation], i: int, flags: int) -> bytes:
    r = _rec(items, i)
    out = bytearray(data)
    struct.pack_into("<I", out, r.record_offset + OFF_FLAGS, flags)
    return bytes(out)


def alpha_span(items: list[GeosetAnimation], i: int) -> tuple[int, int]:
    r = _rec(items, i)
    return r.record_offset + OFF_ALPHA, r.record_offset + OFF_ALPHA + 4


def color_span(items: list[GeosetAnimation], i: int) -> tuple[int, int]:
    r = _rec(items, i)
    return r.record_offset + OFF_COLOR, r.record_offset + OFF_COLOR + 12


def flags_span(items: list[GeosetAnimation], i: int) -> tuple[int, int]:
    r = _rec(items, i)
    return r.record_offset + OFF_FLAGS, r.record_offset + OFF_FLAGS + 4


def record_tracks(data: bytes, item: GeosetAnimation) -> list | None:
    """解析几何动画记录里的 KGAO / KGAC；结构无法自证时返回 None。"""
    from . import animscan

    _start, blocks = animscan.find_chain(
        data, item.record_offset + PREFIX, item.record_offset + item.size)
    return blocks
