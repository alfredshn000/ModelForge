"""TVFS（目录清单）解析。

**所有整数字段都是大端**（和 CASC 索引的小端相反，这一点极易搞混）。

TVFS 是 CASC 里"路径 → 文件内容"的映射表，本身也是一个 BLTE 文件。

    "TVFS"
    u8  version                 —— 固定 1
    u8  headerSize
    u8  encodingKeySize
    u8  patchKeySize
    u32 flags
    u32 pathOffset / pathSize           —— 前缀树
    u32 fileRefOffset / fileRefSize     —— 逻辑文件表
    u32 cascRefOffset / cascRefSize     —— 存储引用表
    u16 maximumPathDepth

路径前缀树是递归结构，每个节点：

    重复 {
        u8 len
        len == 0xFF → 片段结束
        len 字节文本
    }
    u32 value
        value & 0x80000000 → 前缀节点，value & 0x7FFFFFFF 是子容器大小（含这 4 字节）
        否则               → 文件，value 是逻辑文件表里的偏移

逻辑文件表：

    u8 count
    count × {
        u32 offset                          —— 该段在文件内的位置
        u32 size                            —— 该段长度
        变长(contentsOffsetSize)             —— 存储引用表里的偏移
    }

存储引用表（每个偏移处）：

    encodingKeySize 字节 ekey
    u32 physicalSize
    u8  （新格式多一个字节）
    u32 actualSize
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"TVFS"
CONTAINER_FLAG = 0x80000000
PATH_END = 0xFF


class TvfsError(ValueError):
    """TVFS 结构不合法。"""


@dataclass
class StorageRef:
    offset: int
    size: int
    ekey: bytes
    physical_size: int
    actual_size: int


@dataclass
class FileEntry:
    path: str
    refs: list[StorageRef] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(r.actual_size for r in self.refs)


class Tvfs:
    def __init__(self, data: bytes, legacy: bool = False):
        if len(data) < 4 or data[:4] != MAGIC:
            raise TvfsError("缺少 TVFS 标识")

        self.data = data
        version = data[4]
        if version != 1:
            raise TvfsError(f"不支持的 TVFS 版本 {version}")

        header_size = data[5]
        if header_size > len(data):
            raise TvfsError("TVFS 头超出文件范围")

        (self.ekey_size, self.patch_key_size) = data[6], data[7]
        (self.flags, self.path_offset, self.path_size,
         self.file_ref_offset, self.file_ref_size,
         self.casc_ref_offset, self.casc_ref_size) = struct.unpack_from(">IIIIIII", data, 8)

        for name, off, size in (("路径", self.path_offset, self.path_size),
                                ("逻辑文件", self.file_ref_offset, self.file_ref_size),
                                ("存储引用", self.casc_ref_offset, self.casc_ref_size)):
            if off + size > len(data):
                raise TvfsError(f"{name}区越界：{off}+{size} > {len(data)}")

        # 存储引用表的偏移用多少字节表示，取决于该区大小
        self.contents_offset_size = max(1, (self.casc_ref_size.bit_length() + 7) // 8)
        self.legacy = legacy
        self.files: dict[str, FileEntry] = {}
        self._parse_paths()

    # ---------- 路径前缀树 ----------

    def _parse_paths(self) -> None:
        start = self.path_offset
        end = start + self.path_size
        self._parse_container(start, end, "")

    def _parse_container(self, start: int, end: int, prefix: str) -> None:
        pos = start
        while pos < end:
            pos = self._parse_node(pos, end, prefix)

    def _parse_node(self, pos: int, end: int, prefix: str) -> int:
        parts: list[str] = []
        while True:
            if pos >= end:
                raise TvfsError("路径树在读到 value 前结束")
            length = self.data[pos]
            pos += 1
            if length == PATH_END:
                break
            if pos + length > end:
                raise TvfsError("路径片段越界")
            parts.append(self.data[pos:pos + length].decode("latin-1"))
            pos += length

        if pos + 4 > end:
            raise TvfsError("路径节点缺少 value")
        value = struct.unpack_from(">I", self.data, pos)[0]
        pos += 4

        # 片段是正序的：fragments[0] 是第一段，依次用反斜杠连接
        name = prefix + "\\".join(parts)

        if value & CONTAINER_FLAG:
            size = value & ~CONTAINER_FLAG
            if size < 4 or pos - 4 + size > end:
                raise TvfsError(f"子容器大小 {size} 越界")
            # 容器开头 4 字节就是 size 本身
            # 节点之间**不加**分隔符：分隔符由片段内容自己带
            # （空片段就是一个 \），只有同一节点的多个片段之间才用 \ 连接。
            self._parse_container(pos, pos - 4 + size, name)
            return pos - 4 + size

        self.files[name] = FileEntry(path=name, refs=self._read_file_refs(value))
        return pos

    # ---------- 逻辑文件表 ----------

    def _read_file_refs(self, offset: int) -> list[StorageRef]:
        base = self.file_ref_offset
        limit = base + self.file_ref_size
        pos = base + offset
        if pos >= limit:
            raise TvfsError(f"逻辑文件偏移 {offset} 越界")

        count = self.data[pos]
        pos += 1
        refs: list[StorageRef] = []
        for _ in range(count):
            if pos + 8 + self.contents_offset_size > limit:
                raise TvfsError("逻辑文件表越界")
            seg_offset, seg_size = struct.unpack_from(">II", self.data, pos)
            pos += 8
            raw = self.data[pos:pos + self.contents_offset_size]
            pos += self.contents_offset_size
            casc_off = int.from_bytes(raw, "big")
            refs.append(self._read_storage_ref(casc_off, seg_offset, seg_size))
        return refs

    def _read_storage_ref(self, offset: int, seg_offset: int, seg_size: int) -> StorageRef:
        base = self.casc_ref_offset
        pos = base + offset
        limit = base + self.casc_ref_size
        need = self.ekey_size + 4 + (0 if self.legacy else 1) + 4
        if pos + need > limit:
            raise TvfsError(f"存储引用偏移 {offset} 越界")

        ekey = self.data[pos:pos + self.ekey_size]
        pos += self.ekey_size
        physical = struct.unpack_from(">I", self.data, pos)[0]
        pos += 4
        if not self.legacy:
            pos += 1
        actual = struct.unpack_from(">I", self.data, pos)[0]
        return StorageRef(seg_offset, seg_size, ekey, physical, actual)
