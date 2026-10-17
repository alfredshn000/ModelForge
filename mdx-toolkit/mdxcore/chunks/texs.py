"""TEXS 块：贴图表。

布局依据 Retera 的 `TextureChunk.java`（不是靠字节猜的）：

    TEXS 块 = 连续定长记录，**没有计数字段**，读到块尾为止
    每条 268 字节 = replaceableId(u32) + path[256] + unknownNull(u32) + flags(u32)

replaceableId != 0 表示可替换贴图（1 = 队伍色），此时 path 通常为空，贴图由引擎运行时生成。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ENTRY = struct.Struct("<I256sII")
ENTRY_SIZE = ENTRY.size           # 268
PATH_FIELD_OFFSET = 4             # 条目内路径字段的起始偏移
PATH_FIELD_SIZE = 256


class TexsError(ValueError):
    """TEXS 块结构不合法。"""


@dataclass
class Texture:
    replaceable_id: int = 0
    path: str = ""
    unknown_null: int = 0
    flags: int = 0

    @property
    def is_replaceable(self) -> bool:
        return self.replaceable_id != 0


def parse(data: bytes) -> list[Texture]:
    if len(data) % ENTRY_SIZE:
        raise TexsError(
            f"TEXS 长度 {len(data)} 不是 {ENTRY_SIZE} 的整数倍，"
            f"余数 {len(data) % ENTRY_SIZE}"
        )
    out: list[Texture] = []
    for i in range(len(data) // ENTRY_SIZE):
        rid, raw, unknown, flags = ENTRY.unpack_from(data, i * ENTRY_SIZE)
        path = raw.split(b"\x00", 1)[0].decode("latin-1")
        out.append(Texture(rid, path, unknown, flags))
    return out


def build(items: list[Texture]) -> bytes:
    out = bytearray()
    for t in items:
        raw = t.path.encode("latin-1")
        if len(raw) > PATH_FIELD_SIZE:
            raise TexsError(f"路径超过 {PATH_FIELD_SIZE} 字节：{t.path!r}")
        out += ENTRY.pack(t.replaceable_id, raw.ljust(PATH_FIELD_SIZE, b"\x00"),
                          t.unknown_null, t.flags)
    return bytes(out)


def set_path(data: bytes, index: int, new_path: str) -> bytes:
    """只改写第 index 条的路径字段，其余字节原样保留。

    这是 delta 写入的最小实现：不重新序列化整个块，只覆写 256 字节的路径区。
    """
    count = len(data) // ENTRY_SIZE
    if not 0 <= index < count:
        raise TexsError(f"索引 {index} 越界（共 {count} 条）")

    raw = new_path.encode("latin-1")
    if len(raw) > PATH_FIELD_SIZE:
        raise TexsError(f"路径超过 {PATH_FIELD_SIZE} 字节（{len(raw)}）：{new_path!r}")
    # C 字符串语义：字段必须 NUL 结尾，所以最长 255 字节
    if len(raw) > PATH_FIELD_SIZE - 1:
        raise TexsError(f"路径过长，C 字符串需要至少留 1 字节给 NUL：{new_path!r}")

    out = bytearray(data)
    start = index * ENTRY_SIZE + PATH_FIELD_OFFSET
    out[start:start + PATH_FIELD_SIZE] = raw.ljust(PATH_FIELD_SIZE, b"\x00")
    return bytes(out)


def path_field_span(index: int) -> tuple[int, int]:
    """返回第 index 条路径字段在 TEXS 块内的 [起, 止) 偏移。"""
    start = index * ENTRY_SIZE + PATH_FIELD_OFFSET
    return start, start + PATH_FIELD_SIZE
