"""MDX 容器层。

只负责 chunk 边界、校验与透传拼接；不解释任何 chunk 语义。

三条铁律：
1. 未修改的 chunk 原样贴回（直接复用原始 bytes，绝不重新编码）
2. 不认识 / 不支持的 tag 不报错，原样保留
3. 同名 chunk 多次出现时，用「第 N 次」寻址
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"MDLX"
CHUNK_HEAD = struct.Struct("<4sI")


class MdxFormatError(ValueError):
    """结构不合法：不是 MDX、chunk 越界等。"""


@dataclass(frozen=True)
class Chunk:
    tag: str
    header_offset: int
    size: int

    @property
    def data_offset(self) -> int:
        return self.header_offset + CHUNK_HEAD.size

    @property
    def end(self) -> int:
        return self.data_offset + self.size

    def __str__(self) -> str:
        return f"{self.tag}@{self.header_offset}({self.size}B)"


@dataclass
class MdxFile:
    raw: bytes
    chunks: list[Chunk] = field(default_factory=list)
    tail: bytes = b""

    def data(self, chunk: Chunk) -> bytes:
        return self.raw[chunk.data_offset:chunk.end]

    def find(self, tag: str, index: int = 0) -> Chunk:
        hits = [c for c in self.chunks if c.tag == tag]
        try:
            return hits[index]
        except IndexError:
            raise KeyError(f"找不到第 {index} 个 {tag} chunk") from None

    @property
    def tag_count(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.chunks:
            out[c.tag] = out.get(c.tag, 0) + 1
        return out


def parse(data: bytes) -> MdxFile:
    """解析容器结构。只切分，不解析内容。"""
    if len(data) < 4 or data[:4] != MAGIC:
        raise MdxFormatError(f"不是 MDX 文件（前 4 字节应为 MDLX，实际 {data[:4]!r}）")

    pos = 4
    total = len(data)
    chunks: list[Chunk] = []

    while pos + CHUNK_HEAD.size <= total:
        tag_raw = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)

        # tag 必须是可打印 ASCII，否则说明已经走错位置，立刻停下而不是乱切
        if not all(0x20 <= b <= 0x7E for b in tag_raw):
            raise MdxFormatError(
                f"偏移 {pos} 处不是合法 chunk tag：{tag_raw!r}"
            )
        if pos + CHUNK_HEAD.size + size > total:
            raise MdxFormatError(
                f"偏移 {pos} 的 chunk {tag_raw!r} 声明 {size} 字节，"
                f"超出文件长度 {total}"
            )

        chunks.append(Chunk(tag_raw.decode("latin-1"), pos, size))
        pos += CHUNK_HEAD.size + size

    return MdxFile(raw=data, chunks=chunks, tail=data[pos:])


def rebuild(mdx: MdxFile, replacements: dict[int, bytes] | None = None) -> bytes:
    """按 chunk 序号替换数据后重新拼接；未指定的 chunk 原样透传。

    replacements 的 key 是 mdx.chunks 的下标。
    """
    replacements = replacements or {}
    out = bytearray(mdx.raw[:4])

    for i, chunk in enumerate(mdx.chunks):
        payload = replacements.get(i)
        if payload is None:
            out += mdx.raw[chunk.header_offset:chunk.end]
        else:
            out += struct.pack("<4sI", chunk.tag.encode("latin-1"), len(payload))
            out += payload

    out += mdx.tail
    return bytes(out)


def load(path) -> MdxFile:
    with open(path, "rb") as fh:
        return parse(fh.read())
