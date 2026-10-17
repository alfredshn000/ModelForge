"""ATCH（附着点）块：挂在骨骼上的子模型。

布局是**实测出来的**（7 个模型 / 71 条记录），跟其它节点块都不一样：

    +0   outerSize (u32)     整条记录长度
    +4   nodeSize  (u32)     Node 部分长度（含它自己）
    +8   name[80]            注意是 +8，不是 +4
    +88  objectId  (i32)     ← 就是 PIVT 下标
    +92  parentId  (i32)     ← 父节点（一般是骨骼）的 objectId
    +96  flags     (u32)     0x800 = ATTACHMENT
    +100 …                   Node 内可能还有 KGRT/KGTR/KGSC 轨道（到 +4+nodeSize 为止）
    +4+nodeSize              path[256]   挂载模型的游戏内路径
    +4+nodeSize+256          unknownNull (u32)  —— 源码里的字段，必须原样保留
    +4+nodeSize+260          attachmentId (u32)
    之后可能还有额外轨道字节（实测 kultiranbarracks 的末条把 KATV 放在 id 之后）

**两个坑**：
1. 其它节点块名字在 +4、objectId 在 +84；ATCH 各差 4 字节。按 +4 读会拿到
   `0x00000060` 这种垃圾，正是它一直无法自证、PIVT 命名在 120/144 断掉的原因。
2. 路径字段是 **256 字节 + 独立的 unknownNull(4)**，不是 260 字节的路径。
   按 260 整段写会把 unknownNull 抹成 0——所以这里必须分开对待。
3. `outerSize != 4 + nodeSize + 264` 是合法情况（多出来的就是记录尾部的轨道），
   所以**步进必须用 outerSize**，不能自己算。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

OUTER = 0
NODE_SIZE = 4
NAME_AT = 8
ID_AT = 88
PATH_HEAD = 4              # path 在 +4+nodeSize 处
PATH_LEN = 256             # 源码 AttachmentChunk：readCharsAsString(256)
NULL_AT = PATH_LEN         # unknownNull 紧跟路径
ID_LEN = 4
MIN_RECORD = PATH_HEAD + 96 + PATH_LEN + 4 + ID_LEN   # 364

ATTACH_FLAG = 0x800


class AttachError(ValueError):
    """ATCH 块结构不合法。"""


@dataclass
class Attachment:
    index: int
    offset: int             # 记录起点（相对 ATCH 块数据）
    outer: int
    node_size: int
    name: str
    object_id: int
    parent_id: int
    flags: int
    path: str
    attachment_id: int
    unknown_null: int = 0

    @property
    def size(self) -> int:
        return self.outer

    @property
    def path_offset(self) -> int:
        return self.offset + PATH_HEAD + self.node_size

    @property
    def null_offset(self) -> int:
        return self.path_offset + NULL_AT

    @property
    def id_offset(self) -> int:
        return self.path_offset + NULL_AT + 4

    @property
    def name_offset(self) -> int:
        return self.offset + NAME_AT

    @property
    def is_attachment(self) -> bool:
        return bool(self.flags & ATTACH_FLAG)


def parse(data: bytes) -> list[Attachment]:
    out: list[Attachment] = []
    pos = 0
    total = len(data)
    while pos + MIN_RECORD <= total:
        outer, node_size = struct.unpack_from("<II", data, pos)
        if node_size < 96 or outer < PATH_HEAD + node_size + PATH_LEN + ID_LEN:
            raise AttachError(
                f"附着点 @{pos}：outer={outer} nodeSize={node_size} 不自洽（块长 {total}）")
        if pos + outer > total:
            raise AttachError(f"附着点 @{pos} 声明 {outer} 字节，超出块长度 {total}")

        raw_name = data[pos + NAME_AT:pos + NAME_AT + 80]
        name = raw_name.split(b"\x00", 1)[0].decode("latin-1")
        obj, par, flags = struct.unpack_from("<3i", data, pos + ID_AT)
        path_at = pos + PATH_HEAD + node_size
        path = data[path_at:path_at + PATH_LEN].split(b"\x00", 1)[0].decode("latin-1")
        unknown_null = struct.unpack_from("<I", data, path_at + NULL_AT)[0]
        aid = struct.unpack_from("<I", data, path_at + NULL_AT + 4)[0]

        out.append(Attachment(len(out), pos, outer, node_size, name, obj, par,
                              flags, path, aid, unknown_null))
        pos += outer

    if pos != total:
        raise AttachError(f"ATCH 解析结束于 {pos}，块长度为 {total}")
    return out


def _check(items: list[Attachment], i: int) -> Attachment:
    if not 0 <= i < len(items):
        raise AttachError(f"附着点索引 {i} 越界（共 {len(items)} 条）")
    return items[i]


def _write_str(buf: bytearray, offset: int, width: int, text: str) -> None:
    raw = (text or "").encode("latin-1", "replace")
    if len(raw) > width - 1:
        raise AttachError(f"文本超过 {width - 1} 字节：{text!r}")
    buf[offset:offset + width] = raw.ljust(width, b"\x00")


def set_path(data: bytes, items: list[Attachment], i: int, path: str) -> bytes:
    """改挂载模型的路径（空串 = 不挂模型，只作为定位点）。"""
    a = _check(items, i)
    out = bytearray(data)
    _write_str(out, a.path_offset, PATH_LEN, path)
    return bytes(out)


def set_id(data: bytes, items: list[Attachment], i: int, aid: int) -> bytes:
    """改附着点编号（引擎按这个编号找挂点，不是随便排的）。"""
    if not 0 <= aid <= 0xFFFF:
        raise AttachError(f"附着点编号要在 0–65535，收到 {aid}")
    a = _check(items, i)
    out = bytearray(data)
    struct.pack_into("<I", out, a.id_offset, aid)
    return bytes(out)


def set_name(data: bytes, items: list[Attachment], i: int, name: str) -> bytes:
    a = _check(items, i)
    out = bytearray(data)
    _write_str(out, a.name_offset, 80, name)
    return bytes(out)


def path_span(items: list[Attachment], i: int) -> tuple[int, int]:
    a = _check(items, i)
    return a.path_offset, a.path_offset + PATH_LEN


def id_span(items: list[Attachment], i: int) -> tuple[int, int]:
    a = _check(items, i)
    return a.id_offset, a.id_offset + ID_LEN


def name_span(items: list[Attachment], i: int) -> tuple[int, int]:
    a = _check(items, i)
    return a.name_offset, a.name_offset + 80


# ---------- 新建（留给下一轮的增删功能用，先保证形状正确） ----------

def build_record(*, name: str, object_id: int, parent_id: int, path: str = "",
                 attachment_id: int = 0, flags: int = ATTACH_FLAG,
                 unknown_null: int = 0, tracks: bytes = b"") -> bytes:
    """造一条最小的附着点记录：outer(4) + Node(96) + path(256) + null(4) + id(4)。"""
    node_size = 96 + len(tracks)
    outer = PATH_HEAD + node_size + PATH_LEN + 4 + ID_LEN
    out = bytearray()
    out += struct.pack("<II", outer, node_size)
    out += (name or "").encode("latin-1", "replace")[:79].ljust(80, b"\x00")
    out += struct.pack("<3i", object_id, parent_id, flags)
    out += tracks
    out += (path or "").encode("latin-1", "replace")[:255].ljust(PATH_LEN, b"\x00")
    out += struct.pack("<II", unknown_null, attachment_id)
    return bytes(out)


def known_names(attachments: list[Attachment]) -> list[str]:
    """模型里已有的附着点名字，供界面做候选（不预设 "Hand Right Ref" 这类）。"""
    return [a.name for a in attachments if a.name]
