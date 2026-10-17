"""AnimFlag：动画轨道（节点变换、材质参数等共用这一套布局）。

布局依据 Retera 的 `GeosetTranslation.java` 等（各 tag 结构一致，只有分量数不同）：

    tag[4]
    u32 count            轨道条数
    u32 interpolationType  0=DontInterp 1=Linear 2=Hermite 3=Bezier
    i32 globalSequenceId   -1 表示不使用全局序列
    count × {
        i32 time                 单位是毫秒
        f32 value[components]
        if interpolationType > 1:
            f32 inTan[components]
            f32 outTan[components]
    }

注意：**切线是逐条内联的**，不是先放完所有 value 再放切线数组。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

INTERP_NAMES = {0: "DontInterp", 1: "Linear", 2: "Hermite", 3: "Bezier"}

# 各 tag 的数值分量数
COMPONENTS = {
    "KGTR": 3,   # 平移
    "KGRT": 4,   # 旋转（四元数 x,y,z,w）
    "KGSC": 3,   # 缩放
    "KMTF": 1,   # 贴图序号
    "KMTA": 1,   # 透明度
    "KGAO": 1,   # geoset 透明度
    "KGAC": 3,   # geoset 颜色
    "KATV": 3,   # 颜色/向量
    "KPPA": 3,   # 粒子颜色
    "KPPL": 1,   # 生命值
    "KP2S": 1, "KP2R": 1, "KP2L": 1, "KP2G": 1, "KP2E": 1, "KP2W": 1, "KP2V": 1,
}

NODE_TAGS = ("KGTR", "KGRT", "KGSC")


class AnimFlagError(ValueError):
    """动画轨道结构不合法。"""


@dataclass
class Track:
    time: int
    value: tuple
    in_tan: tuple | None = None
    out_tan: tuple | None = None
    offset: int = 0          # 该条 track 的 time 字段在数据里的绝对偏移


@dataclass
class AnimFlag:
    tag: str
    interp: int
    global_seq_id: int
    tracks: list[Track] = field(default_factory=list)
    offset: int = 0
    size: int = 0

    @property
    def interp_name(self) -> str:
        return INTERP_NAMES.get(self.interp, f"?{self.interp}")

    @property
    def has_tangents(self) -> bool:
        return self.interp > 1


def parse(data: bytes, pos: int, tag: str) -> AnimFlag:
    """从 pos 处解析一个动画轨道块。"""
    comps = COMPONENTS.get(tag)
    if comps is None:
        raise AnimFlagError(f"未知的轨道 tag {tag!r}")

    if data[pos:pos + 4] != tag.encode():
        raise AnimFlagError(f"位置 {pos} 期望 {tag}，实际 {data[pos:pos + 4]!r}")

    p = pos + 4
    count, interp, global_seq = struct.unpack_from("<I I i", data, p)
    p += 12

    if count > 1_000_000:
        raise AnimFlagError(f"{tag} 轨道数异常：{count}")

    flags: list[Track] = []
    try:
        for _ in range(count):
            track_offset = p
            time = struct.unpack_from("<i", data, p)[0]
            p += 4
            value = struct.unpack_from(f"<{comps}f", data, p)
            p += comps * 4
            in_tan = out_tan = None
            if interp > 1:
                in_tan = struct.unpack_from(f"<{comps}f", data, p)
                p += comps * 4
                out_tan = struct.unpack_from(f"<{comps}f", data, p)
                p += comps * 4
            flags.append(Track(time, value, in_tan, out_tan, track_offset))
    except struct.error as exc:
        raise AnimFlagError(f"{tag} 数据越界：{exc}") from exc

    return AnimFlag(tag=tag, interp=interp, global_seq_id=global_seq,
                    tracks=flags, offset=pos, size=p - pos)


def peek_tag(data: bytes, pos: int) -> str:
    return data[pos:pos + 4].decode("latin-1", "replace")


def components(tag: str) -> int:
    try:
        return COMPONENTS[tag]
    except KeyError:
        raise AnimFlagError(f"未知的轨道 tag {tag!r}") from None


# ---------- 字段级写入 ----------
#
# 每条 track 的布局（相对 track.offset）：
#    +0                 time   i32
#    +4                 value  comps × f32
#    +4+comps*4         inTan  comps × f32   仅 interp > 1
#    +8+comps*4         outTan comps × f32   仅 interp > 1

def time_span(track: Track) -> tuple[int, int]:
    return track.offset, track.offset + 4


def value_span(track: Track, tag: str) -> tuple[int, int]:
    c = components(tag)
    lo = track.offset + 4
    return lo, lo + c * 4


def tan_span(track: Track, tag: str, which: str) -> tuple[int, int]:
    c = components(tag)
    base = track.offset + 4 + c * 4
    if which == "in":
        return base, base + c * 4
    if which == "out":
        return base + c * 4, base + c * 4 + c * 4
    raise AnimFlagError(f"切线方向只能是 in / out，收到 {which!r}")


def build(flag: AnimFlag) -> bytes:
    """把一个 AnimFlag 重新序列化（结构不变，仅内容）。"""
    c = components(flag.tag)
    out = bytearray(flag.tag.encode())
    out += struct.pack("<I I i", len(flag.tracks), flag.interp, flag.global_seq_id)
    for t in flag.tracks:
        out += struct.pack("<i", t.time)
        out += struct.pack(f"<{c}f", *t.value)
        if flag.interp > 1:
            out += struct.pack(f"<{c}f", *(t.in_tan or (0.0,) * c))
            out += struct.pack(f"<{c}f", *(t.out_tan or (0.0,) * c))
    return bytes(out)
