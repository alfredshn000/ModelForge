"""BONE 块：骨骼。

布局依据 Retera 的 `BoneChunk.java` + `Node.java`：

    每条记录 = Node 的 inclusiveSize 字节 + 尾部 8 字节
      Node 内部：inclusiveSize(u32) + name[80] + objectId(i32) + parentId(i32)
                 + flags(u32) + 各类型动画子块（不解析，靠 inclusiveSize 跳过）
      尾部：geosetId(i32) + geosetAnimationId(i32)

注意：**inclusiveSize 不包含尾部那 8 字节**，所以步长 = inclusiveSize + 8。

父子关系：objectId 是 0..n-1 的顺序编号，parentId 指向 objectId，-1 表示根。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

NODE_PREFIX = 96          # inclusiveSize(4) + name(80) + objectId + parentId + flags

BONE_FLAG = 0x100         # Node 的 ObjectType 位：BONE


class BoneError(ValueError):
    """BONE 块结构不合法。"""


@dataclass
class Bone:
    index: int                # BONE 块内序号，同时也是轴心点索引（骨骼排在最前）
    name: str
    object_id: int
    parent_id: int
    flags: int
    geoset_id: int
    geoset_animation_id: int
    record_offset: int
    size: int                 # inclusiveSize + 8

    @property
    def is_root(self) -> bool:
        return self.parent_id < 0

    @property
    def flagged_as_bone(self) -> bool:
        return bool(self.flags & BONE_FLAG)


def parse(data: bytes) -> list[Bone]:
    out: list[Bone] = []
    pos = 0
    total = len(data)

    while pos + NODE_PREFIX <= total:
        inc = struct.unpack_from("<I", data, pos)[0]
        if inc < NODE_PREFIX or pos + inc + 8 > total:
            raise BoneError(f"骨骼 @{pos} 声明尺寸 {inc} 不合法（块长 {total}）")

        name = data[pos + 4:pos + 84].split(b"\x00", 1)[0].decode("latin-1")
        obj, par, flags = struct.unpack_from("<3i", data, pos + 84)
        geoset_id, geoset_anim_id = struct.unpack_from("<2i", data, pos + inc)

        out.append(Bone(
            index=len(out), name=name, object_id=obj, parent_id=par, flags=flags,
            geoset_id=geoset_id, geoset_animation_id=geoset_anim_id,
            record_offset=pos, size=inc + 8,
        ))
        pos += inc + 8

    if pos != total:
        raise BoneError(f"BONE 解析结束于 {pos}，块长度 {total}")
    return out


def parent_of(bones: list[Bone], bone: Bone) -> Bone | None:
    """按 parentId → objectId 找父骨骼。"""
    if bone.parent_id < 0:
        return None
    for b in bones:
        if b.object_id == bone.parent_id:
            return b
    return None


def parse_tracks(data: bytes, bone: Bone) -> dict[str, object]:
    """解析骨骼记录里的动画轨道（KGTR / KGRT / KGSC）。

    轨道位于 Node 前缀（96 字节）之后、inclusiveSize 结束之前，最多 3 个。
    返回 {tag: AnimFlag}。
    """
    from .animflag import NODE_TAGS, AnimFlagError, parse as parse_flag, peek_tag

    out: dict[str, object] = {}
    p = bone.record_offset + NODE_PREFIX
    end = bone.record_offset + (bone.size - 8)   # 回到 inclusiveSize 的末尾

    while p + 4 <= end:
        tag = peek_tag(data, p)
        if tag not in NODE_TAGS:
            break
        try:
            flag = parse_flag(data, p, tag)
        except AnimFlagError:
            break
        out[tag] = flag
        p += flag.size

    return out


# ---------- 记录重建（关键帧增删需要） ----------
#
# 一条记录的字节构成：
#     [0,96)                前缀：inclusiveSize(会被改写) + name + objectId + parentId + flags
#     [96, inc)             动画轨道（0–3 个，顺序无关但习惯是 KGTR→KGRT→KGSC）
#     [inc, inc+8)          尾部：geosetId + geosetAnimationId
# 记录总长 = inc + 8。

def build_record(data: bytes, bone: Bone, tracks: dict) -> bytes:
    """用新的轨道集合重建一条骨骼记录。

    **按传入顺序**写轨道，不强制重排：文件里的顺序可能不是 KGTR→KGRT→KGSC，
    重排会让本来没改动的骨骼字节也变。
    """
    from .animflag import build as build_flag

    off = bone.record_offset
    inc = bone.size - 8
    prefix = bytearray(data[off:off + NODE_PREFIX])
    tail = data[off + inc:off + inc + 8]

    body = b"".join(build_flag(flag) for flag in tracks.values())
    new_inc = NODE_PREFIX + len(body)
    struct.pack_into("<I", prefix, 0, new_inc)
    return bytes(prefix) + body + tail


def rebuild_block(data: bytes, bones: list[Bone], replacements: dict[int, bytes]) -> bytes:
    """按替换表重拼整个 BONE 块；未替换的记录原样拷贝。"""
    out = bytearray()
    for b in bones:
        piece = replacements.get(b.index)
        out += piece if piece is not None else data[b.record_offset:b.record_offset + b.size]
    return bytes(out)


def record_span(bone: Bone) -> tuple[int, int]:
    return bone.record_offset, bone.record_offset + bone.size
