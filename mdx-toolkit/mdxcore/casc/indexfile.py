"""本地索引文件（Data/data/XXXX.idx）解析。

格式（全部小端）：

    u32 length
    u32 hash                       —— 作者实测从不校验，这里也跳过
    length 字节 header:
        u16 version                —— 固定 7
        u8  bucketIndex            —— 与文件名里的编号对应
        u8  zero
        u8  fileSizeLength         —— 条目里"文件大小"占几字节
        u8  dataOffsetLength       —— 条目里"数据偏移"占几字节
        u8  encodingKeyLength      —— 条目里 ekey 占几字节（WC3 是 9，不是 16）
        u8  dataFileSizeBits       —— dataOffset 的高位是文件号，这里是低位位数
        u64 dataSizeMaximum
    对齐到 16 字节
    u32 length
    u32 hash
    length 字节条目区:
        count × {
            ekey[encodingKeyLength]
            dataOffset[dataOffsetLength]   LE
            fileSize[fileSizeLength]       LE
        }
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class IndexError_(ValueError):
    """索引文件结构不合法。"""


@dataclass(frozen=True)
class IndexEntry:
    key: bytes
    data_offset: int
    file_size: int

    def store_index(self, bits: int) -> int:
        return self.data_offset >> bits

    def store_offset(self, bits: int) -> int:
        return self.data_offset & ((1 << bits) - 1)


@dataclass
class IndexFile:
    bucket: int
    data_file_size_bits: int
    key_length: int
    entries: dict[bytes, IndexEntry]

    @property
    def store_index(self) -> int:
        return self._store_index

    def get(self, key: bytes) -> IndexEntry | None:
        """按 key 查找。比较时用最短长度做前缀匹配（WC3 的 ekey 只有 9 字节）。"""
        if len(key) == self.key_length:
            return self.entries.get(key)
        prefix = key[:self.key_length]
        return self.entries.get(prefix)


def _block(data: bytes, pos: int) -> tuple[bytes, int]:
    """读一个带哈希前缀的块，返回 (内容, 下一个位置)。"""
    if pos + 8 > len(data):
        raise IndexError_(f"块头越界 @{pos}")
    length, _hash = struct.unpack_from("<II", data, pos)
    start = pos + 8
    if start + length > len(data):
        raise IndexError_(f"块声明 {length} 字节，超出文件范围 @{pos}")
    return data[start:start + length], start + length


def parse(path: Path) -> IndexFile:
    data = path.read_bytes()
    header, pos = _block(data, 0)

    if len(header) < 16:
        raise IndexError_(f"header 只有 {len(header)} 字节")

    version, bucket, zero, fsl, dol, ekl, bits = struct.unpack_from("<HBBBBBB", header, 0)
    if version != 7:
        raise IndexError_(f"不支持的索引版本 {version}")
    if fsl == 0 or dol == 0 or ekl == 0:
        raise IndexError_(f"字段长度为零：fsl={fsl} dol={dol} ekl={ekl}")

    pos = (pos + 15) & ~15
    body, _ = _block(data, pos)

    entry_len = fsl + dol + ekl
    if len(body) % entry_len:
        raise IndexError_(f"条目区 {len(body)} 字节不能被 {entry_len} 整除")

    entries: dict[bytes, IndexEntry] = {}
    for i in range(len(body) // entry_len):
        base = i * entry_len
        key = body[base:base + ekl]
        # 两个字段的字节序不同，这是照着 Retera 的实现来的：
        #   dataOffset 在大端 buffer 上读 → 大端
        #   fileSize   显式设了 ByteOrder.LITTLE_ENDIAN → 小端
        # 用同一种字节序会把 offset 解成不可能的几百 GB。
        offset = int.from_bytes(body[base + ekl:base + ekl + dol], "big")
        size = int.from_bytes(body[base + ekl + dol:base + ekl + dol + fsl], "little")
        entries[key] = IndexEntry(key, offset, size)

    obj = IndexFile(bucket=bucket, data_file_size_bits=bits,
                    key_length=ekl, entries=entries)
    obj._store_index = bucket
    return obj
