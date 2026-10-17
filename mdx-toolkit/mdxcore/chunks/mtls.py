"""MTLS 块：材质与图层。

布局依据 Retera 的 `MaterialChunk.java` / `LayerChunk.java`，要点：

- MTLS **没有计数字段**，是一串材质记录，读到块尾为止
- 每个材质、每个图层都以 `inclusiveSize`(u32) 开头 → 可用它安全跳过变长内容
- 图层**固定前缀 28 字节**，与版本无关：
      +0  inclusiveSize
      +4  filterMode
      +8  shadingFlags
      +12 textureId
      +16 textureAnimationId
      +20 coordId
      +24 alpha (f32)
  之后是随版本变化的字段（emissive / fresnel / HD 贴图槽）以及变长的动画轨道，
  本模块**不解析**它们，只按 inclusiveSize 跳过。

版本门控（ModelUtils）：
    shader[80]        900–1000
    emissiveGain f32  900, 1000, 1100, 1200
    fresnel 3f+f+f    1000, 1100, 1200
    HD 贴图槽         1100, 1200
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

LAYER_PREFIX = 28
OFF_SIZE = 0
OFF_FILTER_MODE = 4
OFF_SHADING_FLAGS = 8
OFF_TEXTURE_ID = 12
OFF_TEXTURE_ANIM_ID = 16
OFF_COORD_ID = 20
OFF_ALPHA = 24

FILTER_MODES = ["None", "Transparent", "Blend", "Additive", "AddAlpha", "Modulate", "Modulate2x"]

# ShaderTextureTypeHD 的顺序（Retera 的 mdl/ShaderTextureTypeHD.java）
HD_TEXTURE_TYPES = ["Diffuse", "Normal", "ORM", "Emissive", "TeamColor", "Reflections"]


class MtlsError(ValueError):
    """MTLS 块结构不合法。"""


@dataclass
class HdSlot:
    """1100/1200 的合并 HD 贴图槽：一个 (textureId, textureTypeIndex) 对。"""
    offset: int                 # 相对 MTLS 块数据起点（textureId 字段位置）
    texture_id: int
    type_index: int

    @property
    def type_name(self) -> str:
        if 0 <= self.type_index < len(HD_TEXTURE_TYPES):
            return HD_TEXTURE_TYPES[self.type_index]
        return f"?{self.type_index}"


@dataclass
class Layer:
    record_offset: int          # 相对 MTLS 块数据起点
    size: int                   # inclusiveSize
    filter_mode: int = 0
    shading_flags: int = 0
    texture_id: int = -1
    texture_animation_id: int = -1
    coord_id: int = 0
    alpha: float = 1.0
    hd_slots: list[HdSlot] = field(default_factory=list)
    hd_slots_trusted: bool = True
    track_start: int = -1           # 动画轨道的起点（相对 MTLS 块起点），-1 表示无法确定

    @property
    def filter_name(self) -> str:
        if 0 <= self.filter_mode < len(FILTER_MODES):
            return FILTER_MODES[self.filter_mode]
        return f"?{self.filter_mode}"

    def slot_of_type(self, type_index: int) -> HdSlot | None:
        for s in self.hd_slots:
            if s.type_index == type_index:
                return s
        return None


@dataclass
class Material:
    record_offset: int
    size: int
    priority_plane: int = 0
    flags: int = 0
    layers: list[Layer] = field(default_factory=list)


def _parse_hd_slots(data: bytes, layer: Layer, base: int, size: int, version: int) -> None:
    """解析 1100 及之后的合并 HD 贴图槽。

    曾经这里写的是 `version not in (1100, 1200)`，于是 **1800 的模型全部解析不到槽位**，
    `textureId` 又是个全 0 的历史字段，结果所有材质都指向第 0 张贴图
    （HeroHalahk 19 张贴图只命中 1 张）。

    实测 1800 的图层布局与 1200 完全一致：
    `+52 shaderType=1`、`+56 槽数=6`、`+60` 起 6 个 `(textureId, typeIndex)`，
    槽表结束于 +108，紧接着就是 `KMTA` 轨道标签。
    HeroHalahk 的六个槽指向 2/3/4/5/6/7，正好是 Diffuse/Normal/ORM/Emissive/TeamColor/Reflections。

    只有「槽表恰好铺满图层剩余长度」时才认为坐标可信；一旦后面还跟着
    变长的动画子块，槽位仍可读但标记为不可写，避免给出错误坐标。
    """
    if version < 1100:
        return

    p = base + LAYER_PREFIX + 4 + 20          # 跳过 emissive(4) 与 fresnel(20)
    end = base + size
    if p + 8 > end:
        layer.hd_slots_trusted = False
        return

    _shader_type = _u32(data, p)
    count = _u32(data, p + 4)
    p += 8
    if count > 16 or p + count * 8 > end:
        layer.hd_slots_trusted = False
        return

    slots: list[HdSlot] = []
    for _ in range(count):
        slots.append(HdSlot(
            offset=p,
            texture_id=struct.unpack_from("<i", data, p)[0],
            type_index=_u32(data, p + 4),
        ))
        p += 8

    if p != end:
        # 后面还有动画子块，槽位仍可读但写入不安全
        layer.hd_slots_trusted = False
    layer.hd_slots = slots


def _fixed_prefix_end(base: int, version: int) -> int:
    """固定字段结束位置：之后才是动画轨道。"""
    p = base + LAYER_PREFIX
    if 900 <= version <= 1000:
        p += 80                      # shader 字符串
    if version in (900, 1000, 1100, 1200):
        p += 4                       # emissiveGain
    if version in (1000, 1100, 1200):
        p += 20                      # fresnel
    return p


def _u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


def _f32(data: bytes, pos: int) -> float:
    return struct.unpack_from("<f", data, pos)[0]


def _layer_track_start(layer: Layer, base: int, size: int, version: int) -> int:
    """图层里动画轨道的起点。

    1100/1200 的 HD 槽排在轨道之前，用最后一个槽的末尾作为轨道起点；
    槽位不可信（后面还跟着别的子块）时返回 -1，表示「不知道，别碰」。
    """
    if version in (1100, 1200):
        if not layer.hd_slots or not layer.hd_slots_trusted:
            return -1
        return layer.hd_slots[-1].offset + 8
    return _fixed_prefix_end(base, version)


def parse(data: bytes, version: int) -> list[Material]:
    materials: list[Material] = []
    pos = 0
    total = len(data)

    while pos + 12 <= total:
        size = _u32(data, pos)
        if size < 12 or pos + size > total:
            raise MtlsError(
                f"材质记录 @{pos} 声明 {size} 字节，超出块长度 {total}"
            )

        mat = Material(record_offset=pos, size=size,
                       priority_plane=_u32(data, pos + 4),
                       flags=_u32(data, pos + 8))

        inner = pos + 12
        if 900 <= version <= 1000:
            inner += 80                      # shader 字符串

        if data[inner:inner + 4] == b"LAYS":
            layer_count = _u32(data, inner + 4)
            p = inner + 8
            for _ in range(layer_count):
                if p + LAYER_PREFIX > pos + size:
                    raise MtlsError(f"图层越界 @{p}")
                lsize = _u32(data, p)
                if lsize < LAYER_PREFIX or p + lsize > pos + size:
                    raise MtlsError(f"图层记录 @{p} 声明 {lsize} 字节，超出材质范围")
                layer = Layer(
                    record_offset=p, size=lsize,
                    filter_mode=_u32(data, p + OFF_FILTER_MODE),
                    shading_flags=_u32(data, p + OFF_SHADING_FLAGS),
                    texture_id=struct.unpack_from("<i", data, p + OFF_TEXTURE_ID)[0],
                    texture_animation_id=struct.unpack_from("<i", data, p + OFF_TEXTURE_ANIM_ID)[0],
                    coord_id=_u32(data, p + OFF_COORD_ID),
                    alpha=_f32(data, p + OFF_ALPHA),
                )
                _parse_hd_slots(data, layer, p, lsize, version)
                layer.track_start = _layer_track_start(layer, p, lsize, version)
                mat.layers.append(layer)
                p += lsize
            if p != pos + size:
                raise MtlsError(
                    f"材质 @{pos} 的图层总长 {p - pos} 与 inclusiveSize {size} 不符"
                )

        materials.append(mat)
        pos += size

    if pos != total:
        raise MtlsError(f"MTLS 解析结束于 {pos}，块长度为 {total}")
    return materials


def _check(materials: list[Material], mi: int, li: int) -> Layer:
    if not 0 <= mi < len(materials):
        raise MtlsError(f"材质索引 {mi} 越界（共 {len(materials)} 个）")
    layers = materials[mi].layers
    if not 0 <= li < len(layers):
        raise MtlsError(f"图层索引 {li} 越界（材质 {mi} 有 {len(layers)} 层）")
    return layers[li]


def set_layer_alpha(data: bytes, materials: list[Material],
                    mi: int, li: int, alpha: float) -> bytes:
    """只改图层 alpha（图层记录 +24 处的 4 字节）。"""
    if not 0.0 <= alpha <= 1.0:
        raise MtlsError(f"alpha 必须在 0–1 之间，收到 {alpha}")
    layer = _check(materials, mi, li)
    out = bytearray(data)
    struct.pack_into("<f", out, layer.record_offset + OFF_ALPHA, alpha)
    return bytes(out)


def set_layer_filter(data: bytes, materials: list[Material],
                     mi: int, li: int, mode: int | str) -> bytes:
    if isinstance(mode, str):
        key = mode.lower()
        names = [m.lower() for m in FILTER_MODES]
        if key not in names:
            raise MtlsError(f"未知 FilterMode {mode!r}，可选：{', '.join(FILTER_MODES)}")
        mode = names.index(key)
    if not 0 <= mode < len(FILTER_MODES):
        raise MtlsError(f"FilterMode {mode} 超出范围 0–{len(FILTER_MODES) - 1}")
    layer = _check(materials, mi, li)
    out = bytearray(data)
    struct.pack_into("<I", out, layer.record_offset + OFF_FILTER_MODE, mode)
    return bytes(out)


def set_layer_texture(data: bytes, materials: list[Material],
                      mi: int, li: int, texture_index: int) -> bytes:
    """只改图层引用的贴图序号（-1 表示不引用）。"""
    layer = _check(materials, mi, li)
    out = bytearray(data)
    struct.pack_into("<i", out, layer.record_offset + OFF_TEXTURE_ID, texture_index)
    return bytes(out)


def alpha_span(materials: list[Material], mi: int, li: int) -> tuple[int, int]:
    layer = _check(materials, mi, li)
    return layer.record_offset + OFF_ALPHA, layer.record_offset + OFF_ALPHA + 4


def filter_span(materials: list[Material], mi: int, li: int) -> tuple[int, int]:
    layer = _check(materials, mi, li)
    return layer.record_offset + OFF_FILTER_MODE, layer.record_offset + OFF_FILTER_MODE + 4


def texture_span(materials: list[Material], mi: int, li: int) -> tuple[int, int]:
    layer = _check(materials, mi, li)
    return layer.record_offset + OFF_TEXTURE_ID, layer.record_offset + OFF_TEXTURE_ID + 4


def set_hd_texture(data: bytes, materials: list[Material], mi: int, li: int,
                   type_index: int, texture_id: int) -> bytes:
    """改 1100/1200 HD 图层里某个贴图槽的 textureId。"""
    layer = _check(materials, mi, li)
    if not layer.hd_slots:
        raise MtlsError(f"材质 {mi} 图层 {li} 没有 HD 贴图槽（可能是 1000 及以下版本）")
    if not layer.hd_slots_trusted:
        raise MtlsError("该图层的 HD 槽后面还有动画子块，拒绝写入以免破坏数据")
    slot = layer.slot_of_type(type_index)
    if slot is None:
        have = ", ".join(s.type_name for s in layer.hd_slots) or "无"
        raise MtlsError(f"该图层没有类型 {type_index} 的槽；现有：{have}")
    out = bytearray(data)
    struct.pack_into("<i", out, slot.offset, texture_id)
    return bytes(out)


def hd_texture_span(materials: list[Material], mi: int, li: int,
                    type_index: int) -> tuple[int, int]:
    layer = _check(materials, mi, li)
    slot = layer.slot_of_type(type_index)
    if slot is None:
        raise MtlsError(f"该图层没有类型 {type_index} 的槽")
    return slot.offset, slot.offset + 4


def resolve_hd_type(name: str) -> int:
    key = name.strip().lower()
    names = [t.lower() for t in HD_TEXTURE_TYPES]
    if key not in names:
        raise MtlsError(f"未知贴图槽 {name!r}，可选：{', '.join(HD_TEXTURE_TYPES)}")
    return names.index(key)


def layer_tracks(data: bytes, layer: Layer) -> list | None:
    """解析图层里的动画轨道（KMTF / KMTA …）。

    不依赖 `track_start`：固定字段在不同版本下长度不一（1000 版实测就比公式短），
    直接用「最早能完整吃到图层末尾」的链作为轨道。定位不到就返回空列表。
    """
    from . import animscan

    _start, blocks = animscan.find_chain(
        data, layer.record_offset + LAYER_PREFIX, layer.record_offset + layer.size)
    return blocks
