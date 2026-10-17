"""PRE2 块：ParticleEmitter2（粒子发射器 2）。

**布局和别的节点块都不同，是「双重 size」再套 track**：

    outerSize  (u32)     整条记录长度
    nodeSize   (u32)     含它自己的 4 字节；Node + 它自己的动画轨道
    name[80]             在 +8
    objectId   (i32)     +88
    parentId   (i32)     +92
    flags      (u32)     +96
    tracks               从 +100 起，长度 = nodeSize - 96
    固定字段 171 字节     在 +4 + nodeSize

固定字段布局（连续，无标签）：

    speed, variation, latitude, gravity, lifespan, emissionRate, length, width   (8f)
    filterMode, rows, columns, headOrTail                                        (4i)
    tailLength, time                                                             (2f)
    segmentColor[3][3]                                                            (9f)
    segmentAlpha[3]                                                               (3B)
    segmentScaling[3]                                                             (3f)
    headIntervalStart/End/Repeat, headDecay..., tail..., tailDecay...             (12i)
    textureId, squirt, priorityPlane, replaceableId                              (4i)

自证方式：`outerSize == 4 + nodeSize + 171`。实测 13 个模型全部成立，
包括 David（687 = 4+512+171）、DustEmitter（271 = 4+96+171）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

FIXED_SIZE = 171
HEAD = 100          # name/obj/par/flags 到 tracks 起点


class Pre2Error(ValueError):
    """PRE2 结构不合法。"""


@dataclass
class Emitter2:
    index: int
    offset: int
    name: str
    object_id: int
    parent_id: int
    flags: int
    speed: float = 0.0
    variation: float = 0.0
    latitude: float = 0.0
    gravity: float = 0.0
    lifespan: float = 0.0
    emission_rate: float = 0.0
    length: float = 0.0
    width: float = 0.0
    filter_mode: int = 0
    rows: int = 1
    columns: int = 1
    head_or_tail: int = 0
    tail_length: float = 0.0
    time: float = 0.0
    colors: list = field(default_factory=list)     # 3 × [r,g,b] 0-1
    alphas: list = field(default_factory=list)     # 3 × 0-1
    scaling: list = field(default_factory=list)    # 3 × float
    texture_id: int = -1
    squirt: int = 0
    priority_plane: int = 0
    replaceable_id: int = 0

    @property
    def active(self) -> bool:
        """参数全 0 的发射器不会产生任何粒子（David 里就是这种空壳）。"""
        return self.emission_rate > 0 and self.lifespan > 0


def parse(data: bytes) -> list[Emitter2]:
    out: list[Emitter2] = []
    pos = 0
    total = len(data)
    while pos + 8 <= total:
        outer, node_size = struct.unpack_from("<II", data, pos)
        if outer < HEAD + FIXED_SIZE or pos + outer > total:
            raise Pre2Error(f"记录长度异常：offset {pos}, outer={outer}")
        # 不强制 outer == 4+nodeSize+171：少数模型（InfernalMagicFire）尾部
        # 还有 24 字节额外数据。固定字段仍从 +4+nodeSize 读，多出来的跳过。
        extra = outer - (4 + node_size + FIXED_SIZE)
        if extra < 0:
            raise Pre2Error(
                f"offset {pos}: outer={outer} 装不下 4+{node_size}+{FIXED_SIZE}")

        name = data[pos + 8:pos + 88].split(b"\x00", 1)[0].decode("latin-1")
        obj, par = struct.unpack_from("<ii", data, pos + 88)
        flags = struct.unpack_from("<I", data, pos + 96)[0]

        f = pos + 4 + node_size
        v = struct.unpack_from("<8f", data, f)
        filt, rows, cols, head_tail = struct.unpack_from("<4i", data, f + 32)
        tail_len, t = struct.unpack_from("<2f", data, f + 48)
        colors = [list(struct.unpack_from("<3f", data, f + 56 + i * 12)) for i in range(3)]
        alphas = [data[f + 92 + i] / 255.0 for i in range(3)]
        scaling = list(struct.unpack_from("<3f", data, f + 95))
        tex_id, squirt, prio, repl = struct.unpack_from("<4i", data, f + 155)

        out.append(Emitter2(
            index=len(out), offset=pos, name=name,
            object_id=obj, parent_id=par, flags=flags,
            speed=v[0], variation=v[1], latitude=v[2], gravity=v[3],
            lifespan=v[4], emission_rate=v[5], length=v[6], width=v[7],
            filter_mode=filt, rows=max(1, rows), columns=max(1, cols),
            head_or_tail=head_tail, tail_length=tail_len, time=t,
            colors=colors, alphas=alphas, scaling=scaling,
            texture_id=tex_id, squirt=squirt,
            priority_plane=prio, replaceable_id=repl,
        ))
        pos += outer

    if pos != total:
        raise Pre2Error(f"走完 {pos} ≠ 块长 {total}")
    return out
