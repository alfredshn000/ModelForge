"""语义操作：每个操作只返回「需要重写的 chunk」，其余一律透传。

约定：
- 操作函数不修改传入的 MdxFile
- 返回值 `{chunk 序号: 新数据}`，交给 container.rebuild() 拼接
- 操作必须保证不触碰任何未涉及的 chunk
- 配套的 `*_span()` 给出该操作允许变动的字节范围，供 verify 断言
"""

from __future__ import annotations

from .container import MdxFile
from .chunks import animflag, bone, bpos, geoa, geos, modl, mtls, nodes, pivt, seqs, texs


class OpError(ValueError):
    """操作前置条件不满足。"""


def _single_chunk_index(mdx: MdxFile, tag: str) -> int:
    hits = [i for i, c in enumerate(mdx.chunks) if c.tag == tag]
    if not hits:
        raise OpError(f"模型里没有 {tag} 块")
    if len(hits) > 1:
        raise OpError(f"模型里有 {len(hits)} 个 {tag} 块，暂不支持")
    return hits[0]


def _optional_chunk_index(mdx: MdxFile, tag: str) -> int | None:
    """读操作专用：没有这个块就返回 None。

    游戏里有大量纯特效模型（血迹、法阵、投射物），它们只有 GEOS + 一条材质都没有，
    甚至没有 TEXS。打开这类模型不该报错——按"没有"处理即可。
    写操作仍然走 _single_chunk_index，缺块时必须拒绝。
    """
    hits = [i for i, c in enumerate(mdx.chunks) if c.tag == tag]
    if len(hits) > 1:
        raise OpError(f"模型里有 {len(hits)} 个 {tag} 块，暂不支持")
    return hits[0] if hits else None


def format_version(mdx: MdxFile) -> int:
    idx = _single_chunk_index(mdx, "VERS")
    return int.from_bytes(mdx.data(mdx.chunks[idx])[:4], "little")


# ---------- 读 ----------

def list_textures(mdx: MdxFile) -> list[texs.Texture]:
    idx = _optional_chunk_index(mdx, "TEXS")
    return texs.parse(mdx.data(mdx.chunks[idx])) if idx is not None else []


def list_sequences(mdx: MdxFile) -> list[seqs.Sequence]:
    idx = _optional_chunk_index(mdx, "SEQS")
    return seqs.parse(mdx.data(mdx.chunks[idx])) if idx is not None else []


def model_header(mdx: MdxFile) -> modl.ModelHeader:
    idx = _single_chunk_index(mdx, "MODL")
    return modl.parse(mdx.data(mdx.chunks[idx]))


def list_materials(mdx: MdxFile) -> list[mtls.Material]:
    idx = _optional_chunk_index(mdx, "MTLS")
    if idx is None:
        return []
    return mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))


# ---------- 写 ----------

def rename_texture(mdx: MdxFile, index: int, new_path: str) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "TEXS")
    return {idx: texs.set_path(mdx.data(mdx.chunks[idx]), index, new_path)}


def texture_span(mdx: MdxFile, index: int) -> tuple[int, tuple[int, int]]:
    return _single_chunk_index(mdx, "TEXS"), texs.path_field_span(index)


def set_sequence_interval(mdx: MdxFile, index: int, start: int, end: int) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "SEQS")
    return {idx: seqs.set_interval(mdx.data(mdx.chunks[idx]), index, start, end)}


def sequence_interval_span(mdx: MdxFile, index: int) -> tuple[int, tuple[int, int]]:
    return _single_chunk_index(mdx, "SEQS"), seqs.interval_span(index)


def set_sequence_looping(mdx: MdxFile, index: int, non_looping: bool) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "SEQS")
    return {idx: seqs.set_non_looping(mdx.data(mdx.chunks[idx]), index, non_looping)}


def sequence_looping_span(mdx: MdxFile, index: int) -> tuple[int, tuple[int, int]]:
    return _single_chunk_index(mdx, "SEQS"), seqs.non_looping_span(index)


def rename_model(mdx: MdxFile, new_name: str) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "MODL")
    return {idx: modl.set_name(mdx.data(mdx.chunks[idx]), new_name)}


def model_name_span(mdx: MdxFile) -> tuple[int, tuple[int, int]]:
    return _single_chunk_index(mdx, "MODL"), modl.name_span()


# ---------- 材质 / 图层写操作 ----------

def set_layer_alpha(mdx: MdxFile, mi: int, li: int, alpha: float) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return {idx: mtls.set_layer_alpha(mdx.data(mdx.chunks[idx]), mats, mi, li, alpha)}


def layer_alpha_span(mdx: MdxFile, mi: int, li: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return idx, mtls.alpha_span(mats, mi, li)


def set_layer_filter(mdx: MdxFile, mi: int, li: int, mode) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return {idx: mtls.set_layer_filter(mdx.data(mdx.chunks[idx]), mats, mi, li, mode)}


def layer_filter_span(mdx: MdxFile, mi: int, li: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return idx, mtls.filter_span(mats, mi, li)


def set_layer_texture(mdx: MdxFile, mi: int, li: int, texture_index: int) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return {idx: mtls.set_layer_texture(mdx.data(mdx.chunks[idx]), mats, mi, li, texture_index)}


def layer_texture_span(mdx: MdxFile, mi: int, li: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return idx, mtls.texture_span(mats, mi, li)


def set_layer_hd_texture(mdx: MdxFile, mi: int, li: int,
                         type_index: int, texture_id: int) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return {idx: mtls.set_hd_texture(mdx.data(mdx.chunks[idx]), mats,
                                     mi, li, type_index, texture_id)}


def layer_hd_texture_span(mdx: MdxFile, mi: int, li: int,
                          type_index: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "MTLS")
    mats = mtls.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    return idx, mtls.hd_texture_span(mats, mi, li, type_index)


# ---------- 几何动画 ----------

def list_geoset_animations(mdx: MdxFile) -> list[geoa.GeosetAnimation]:
    idx = _single_chunk_index(mdx, "GEOA")
    return geoa.parse(mdx.data(mdx.chunks[idx]))


def set_geoset_alpha(mdx: MdxFile, i: int, alpha: float) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "GEOA")
    items = geoa.parse(mdx.data(mdx.chunks[idx]))
    return {idx: geoa.set_alpha(mdx.data(mdx.chunks[idx]), items, i, alpha)}


def geoset_alpha_span(mdx: MdxFile, i: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "GEOA")
    items = geoa.parse(mdx.data(mdx.chunks[idx]))
    return idx, geoa.alpha_span(items, i)


def set_geoset_color(mdx: MdxFile, i: int, color) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "GEOA")
    items = geoa.parse(mdx.data(mdx.chunks[idx]))
    return {idx: geoa.set_color(mdx.data(mdx.chunks[idx]), items, i, color)}


def geoset_color_span(mdx: MdxFile, i: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "GEOA")
    items = geoa.parse(mdx.data(mdx.chunks[idx]))
    return idx, geoa.color_span(items, i)


# ---------- 批量 ----------

def rename_textures_bulk(mdx: MdxFile, mapping: dict[int, str]) -> dict[int, bytes]:
    """一次改多条贴图路径；仍只重写 TEXS 一个 chunk。"""
    idx = _single_chunk_index(mdx, "TEXS")
    data = mdx.data(mdx.chunks[idx])
    for i, new_path in mapping.items():
        data = texs.set_path(data, i, new_path)
    return {idx: data}


def texture_spans_bulk(mdx: MdxFile, indices) -> list[tuple[int, int]]:
    idx = _single_chunk_index(mdx, "TEXS")
    base = mdx.chunks[idx].data_offset
    out = []
    for i in indices:
        lo, hi = texs.path_field_span(i)
        out.append((base + lo, base + hi))
    return out


# ---------- 轴心点 ----------

def list_pivots(mdx: MdxFile):
    """返回 (轴心点列表, 名称槽位, 说明)。名称槽位中 None 表示该索引无法确认对应节点。"""
    idx = _single_chunk_index(mdx, "PIVT")
    points = pivt.parse(mdx.data(mdx.chunks[idx]))
    slots, note = nodes.analyze(mdx, len(points))
    return points, slots, note


def set_pivot(mdx: MdxFile, index: int, xyz: tuple[float, float, float]) -> dict[int, bytes]:
    idx = _single_chunk_index(mdx, "PIVT")
    return {idx: pivt.set_pivot(mdx.data(mdx.chunks[idx]), index, xyz)}


def pivot_span(mdx: MdxFile, index: int) -> tuple[int, tuple[int, int]]:
    return _single_chunk_index(mdx, "PIVT"), pivt.pivot_span(index)


# ---------- 绑定姿势（只读） ----------

def list_bind_pose(mdx: MdxFile):
    """返回绑定姿势矩阵列表；模型没有 BPOS 时抛 OpError。"""
    idx = _single_chunk_index(mdx, "BPOS")
    return bpos.parse(mdx.data(mdx.chunks[idx]))


# ---------- 顶点（定点写回） ----------

def list_geosets(mdx: MdxFile):
    idx = _single_chunk_index(mdx, "GEOS")
    return geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))


def set_vertex(mdx: MdxFile, gi: int, vi: int,
               xyz: tuple[float, float, float]) -> dict[int, bytes]:
    """只改第 gi 个 geoset 第 vi 个顶点的 12 字节，其余一律不动。"""
    import struct as _struct

    idx = _single_chunk_index(mdx, "GEOS")
    data = mdx.data(mdx.chunks[idx])
    gs = geos.parse(data, format_version(mdx))
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    lo, hi = gs[gi].vertex_span(vi)
    out = bytearray(data)
    _struct.pack_into("<3f", out, lo, *xyz)
    return {idx: bytes(out)}


def set_vertices(mdx: MdxFile, gi: int,
                 changes: dict[int, tuple[float, float, float]]) -> dict[int, bytes]:
    """批量改同一 geoset 的多个顶点。"""
    import struct as _struct

    idx = _single_chunk_index(mdx, "GEOS")
    data = mdx.data(mdx.chunks[idx])
    gs = geos.parse(data, format_version(mdx))
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    out = bytearray(data)
    for vi, xyz in changes.items():
        lo, _ = gs[gi].vertex_span(vi)
        _struct.pack_into("<3f", out, lo, *xyz)
    return {idx: bytes(out)}


def vertex_span(mdx: MdxFile, gi: int, vi: int) -> tuple[int, tuple[int, int]]:
    idx = _single_chunk_index(mdx, "GEOS")
    gs = geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    return idx, gs[gi].vertex_span(vi)


def vertex_spans(mdx: MdxFile, gi: int, indices) -> list[tuple[int, int]]:
    idx = _single_chunk_index(mdx, "GEOS")
    base = mdx.chunks[idx].data_offset
    gs = geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    out = []
    for vi in indices:
        lo, hi = gs[gi].vertex_span(vi)
        out.append((base + lo, base + hi))
    return out


def apply_vertex_patches(mdx: MdxFile,
                         patches: dict[int, dict[int, tuple[float, float, float]]]
                         ) -> dict[int, bytes]:
    """一次写入多个 geoset 的多个顶点；仍只重写 GEOS 一个 chunk。"""
    import struct as _struct

    idx = _single_chunk_index(mdx, "GEOS")
    data = mdx.data(mdx.chunks[idx])
    gs = geos.parse(data, format_version(mdx))
    out = bytearray(data)
    for gi, changes in patches.items():
        if not 0 <= gi < len(gs):
            raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
        for vi, xyz in changes.items():
            lo, _ = gs[gi].vertex_span(vi)
            _struct.pack_into("<3f", out, lo, *xyz)
    return {idx: bytes(out)}


def all_vertex_spans(mdx: MdxFile,
                     patches: dict[int, dict[int, tuple]]) -> list[tuple[int, int]]:
    """把 patches 涉及的所有顶点换算成文件绝对偏移范围。"""
    idx = _single_chunk_index(mdx, "GEOS")
    base = mdx.chunks[idx].data_offset
    gs = geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    spans: list[tuple[int, int]] = []
    for gi, changes in patches.items():
        for vi in changes:
            lo, hi = gs[gi].vertex_span(vi)
            spans.append((base + lo, base + hi))
    return spans


# ---------- 骨骼动画（字段级写入） ----------

def bone_list(mdx: MdxFile):
    idx = _single_chunk_index(mdx, "BONE")
    return bone.parse(mdx.data(mdx.chunks[idx]))


def animation_tracks(mdx: MdxFile, bone_index: int) -> dict:
    """返回 {tag: AnimFlag}；model 没有该骨骼时抛 OpError。"""
    idx = _single_chunk_index(mdx, "BONE")
    data = mdx.data(mdx.chunks[idx])
    bones = bone.parse(data)
    if not 0 <= bone_index < len(bones):
        raise OpError(f"骨骼索引 {bone_index} 越界（共 {len(bones)} 根）")
    return bone.parse_tracks(data, bones[bone_index])


Patch = dict          # {"tag","index","time","value","in_tan","out_tan"}


def _resolve_track(mdx: MdxFile, data: bytes, bones, item: Patch):
    bi = item["bone"]
    if not 0 <= bi < len(bones):
        raise OpError(f"骨骼索引 {bi} 越界（共 {len(bones)} 根）")
    tag = item["tag"]
    flags = bone.parse_tracks(data, bones[bi])
    if tag not in flags:
        raise OpError(f"骨骼 {bi} 没有 {tag} 轨道")
    flag = flags[tag]
    ti = item["index"]
    if not 0 <= ti < len(flag.tracks):
        raise OpError(f"骨骼 {bi} 的 {tag} 只有 {len(flag.tracks)} 条轨道，索引 {ti} 越界")
    return flag, flag.tracks[ti]


def patch_tracks(mdx: MdxFile, items: list[Patch]) -> dict[int, bytes]:
    """只改每条轨道里被点名的字段，其余字节原样保留。"""
    import struct as _struct

    idx = _single_chunk_index(mdx, "BONE")
    data = bytearray(mdx.data(mdx.chunks[idx]))
    bones = bone.parse(bytes(data))

    for item in items:
        flag, track = _resolve_track(mdx, bytes(data), bones, item)
        comps = animflag.components(item["tag"])
        if "time" in item:
            _struct.pack_into("<i", data, track.offset, int(item["time"]))
        if "value" in item:
            value = item["value"]
            if len(value) != comps:
                raise OpError(f"{item['tag']} 需要 {comps} 个分量，收到 {len(value)}")
            _struct.pack_into(f"<{comps}f", data, track.offset + 4, *value)
        for key, which in (("in_tan", "in"), ("out_tan", "out")):
            if key in item:
                value = item[key]
                if len(value) != comps:
                    raise OpError(f"{item['tag']} 切线需要 {comps} 个分量")
                lo, _ = animflag.tan_span(track, item["tag"], which)
                _struct.pack_into(f"<{comps}f", data, lo, *value)

    return {idx: bytes(data)}


def track_spans(mdx: MdxFile, items: list[Patch]) -> list[tuple[int, int]]:
    """把每条被改字段换算成文件绝对偏移范围（供 check_delta 断言）。"""
    idx = _single_chunk_index(mdx, "BONE")
    base = mdx.chunks[idx].data_offset
    data = mdx.data(mdx.chunks[idx])
    bones = bone.parse(data)
    spans: list[tuple[int, int]] = []
    for item in items:
        _flag, track = _resolve_track(mdx, data, bones, item)
        tag = item["tag"]
        if "time" in item:
            lo, hi = animflag.time_span(track)
            spans.append((base + lo, base + hi))
        if "value" in item:
            lo, hi = animflag.value_span(track, tag)
            spans.append((base + lo, base + hi))
        for key, which in (("in_tan", "in"), ("out_tan", "out")):
            if key in item:
                lo, hi = animflag.tan_span(track, tag, which)
                spans.append((base + lo, base + hi))
    return spans


# ---------- 顶点增删（结构变更） ----------
#
# 默认**不**重算包围盒：实测 11 个模型里有 10 个把 boundsRadius / min / max
# 全写成 0，说明 Reforged 根本不读这几个字段（它用 MODL 与 SEQS 的 extents）。
# 既然不用，就别动 —— 少改一个字节就少一份风险。

def _geosets(mdx: MdxFile):
    idx = _single_chunk_index(mdx, "GEOS")
    return idx, geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))


def geoset_summary(mdx: MdxFile) -> list[dict]:
    _idx, gs = _geosets(mdx)
    return [{"geoset": i,
             "vertices": g.vertex_count,
             "triangles": g.triangle_count,
             "material": g.material_id,
             "uv_sets": len(g.uv_sets),
             "has_tangent": g.tangents is not None,
             "has_skin": g.skin is not None,
             "bounds_radius": g.bounds_radius}
            for i, g in enumerate(gs)]


def add_vertex(mdx: MdxFile, gi: int, position, *, source: int | None = None,
               recompute: bool = False) -> dict[int, bytes]:
    idx, gs = _geosets(mdx)
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    geos.add_vertex(gs[gi], position, source=source)
    if recompute:
        geos.recompute_bounds(gs[gi])
    for g in gs:
        geos.validate(g)
    return {idx: geos.build(gs, format_version(mdx))}


def delete_vertex(mdx: MdxFile, gi: int, index: int,
                  recompute: bool = False) -> dict[int, bytes]:
    idx, gs = _geosets(mdx)
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    geos.delete_vertices(gs[gi], [index])
    if recompute:
        geos.recompute_bounds(gs[gi])
    for g in gs:
        geos.validate(g)
    return {idx: geos.build(gs, format_version(mdx))}


def add_triangle(mdx: MdxFile, gi: int, a: int, b: int, c: int,
                 recompute: bool = False) -> dict[int, bytes]:
    idx, gs = _geosets(mdx)
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    geos.add_triangle(gs[gi], a, b, c)
    if recompute:
        geos.recompute_bounds(gs[gi])
    for g in gs:
        geos.validate(g)
    return {idx: geos.build(gs, format_version(mdx))}


def extrude_triangle(mdx: MdxFile, gi: int, tri_index: int,
                     offset: tuple, recompute: bool = False) -> dict[int, bytes]:
    """把一个三角形整体复制出来、平移一段距离，再连成新三角形。

    属性（法线 / UV / 顶点组 / 切线 / 蒙皮）全部克隆自原顶点，
    所以新片会跟着原来的骨骼一起动。
    """
    idx, gs = _geosets(mdx)
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    g = gs[gi]
    if not 0 <= tri_index < g.triangle_count:
        raise OpError(f"三角形索引 {tri_index} 越界（共 {g.triangle_count} 个）")
    src = [g.faces[tri_index * 3 + k] for k in range(3)]
    fresh = []
    for vi in src:
        v = g.vertices[vi]
        pos = (v[0] + offset[0], v[1] + offset[1], v[2] + offset[2])
        fresh.append(geos.add_vertex(g, pos, source=vi))
    geos.add_triangle(g, *fresh)
    if recompute:
        geos.recompute_bounds(g)
    for one in gs:
        geos.validate(one)
    return {idx: geos.build(gs, format_version(mdx))}


def add_geometry(mdx: MdxFile, gi: int, positions, triangles, *,
                 source: int = 0, normals=None,
                 recompute: bool = False) -> dict[int, bytes]:
    """一次加入一组顶点 + 三角形（只重建一次 GEOS）。

    `triangles` 里的下标是 **positions 列表内的相对下标**，不是文件里的绝对索引。
    法线不传就克隆 source 顶点的法线；传了则按顶点逐个用。
    """
    idx, gs = _geosets(mdx)
    if not 0 <= gi < len(gs):
        raise OpError(f"geoset 索引 {gi} 越界（共 {len(gs)} 个）")
    g = gs[gi]
    if not g.vertices:
        raise OpError("空 geoset 无法克隆属性，请先手动建顶点")

    fresh = []
    for k, pos in enumerate(positions):
        normal = normals[k] if normals is not None else None
        fresh.append(geos.add_vertex(g, pos, source=source, normal=normal))
    for tri in triangles:
        if len(tri) != 3:
            raise OpError("每个三角形需要 3 个索引")
        geos.add_triangle(g, fresh[tri[0]], fresh[tri[1]], fresh[tri[2]])
    if recompute:
        geos.recompute_bounds(g)
    for one in gs:
        geos.validate(one)
    return {idx: geos.build(gs, format_version(mdx))}


# ---------- 关键帧增删（轨道块长度会变） ----------

def _default_tangents(tracks, time, value, comps):
    """新关键帧没有指定切线时，用左右邻点的割线斜率，避免出现折角。

    单位与文件一致：数值 / 毫秒。
    """
    prev = max((t for t in tracks if t.time < time), key=lambda t: t.time, default=None)
    nxt = min((t for t in tracks if t.time > time), key=lambda t: t.time, default=None)
    if prev is None or nxt is None:
        return (0.0,) * comps, (0.0,) * comps
    span = nxt.time - prev.time
    if span <= 0:
        return (0.0,) * comps, (0.0,) * comps
    slope = tuple((nxt.value[k] - prev.value[k]) / span for k in range(comps))
    return slope, slope


def add_keyframe(mdx: MdxFile, bone_index: int, tag: str, time: int,
                 value, in_tan=None, out_tan=None) -> dict[int, bytes]:
    """在指定骨骼的轨道里插入一个关键帧（轨道块会变长，记录整体重建）。"""
    idx = _single_chunk_index(mdx, "BONE")
    data = mdx.data(mdx.chunks[idx])
    bones = bone.parse(data)
    if not 0 <= bone_index < len(bones):
        raise OpError(f"骨骼索引 {bone_index} 越界（共 {len(bones)} 根）")

    comps = animflag.components(tag)
    if len(value) != comps:
        raise OpError(f"{tag} 需要 {comps} 个分量，收到 {len(value)}")

    tracks = bone.parse_tracks(data, bones[bone_index])
    flag = tracks.get(tag)
    if flag is None:
        flag = animflag.AnimFlag(tag=tag, interp=1, global_seq_id=-1, tracks=[])
        tracks[tag] = flag
    elif any(t.time == time for t in flag.tracks):
        raise OpError(f"{tag} 在 {time} ms 已经有键了")

    if flag.interp > 1 and (in_tan is None or out_tan is None):
        auto_in, auto_out = _default_tangents(flag.tracks, time, value, comps)
        in_tan = in_tan or auto_in
        out_tan = out_tan or auto_out

    track = animflag.Track(time=int(time), value=tuple(value),
                           in_tan=tuple(in_tan) if in_tan else None,
                           out_tan=tuple(out_tan) if out_tan else None)
    flag.tracks.append(track)
    flag.tracks.sort(key=lambda t: t.time)

    record = bone.build_record(data, bones[bone_index], tracks)
    return {idx: bone.rebuild_block(data, bones, {bone_index: record})}


def delete_keyframe(mdx: MdxFile, bone_index: int, tag: str,
                    index: int) -> dict[int, bytes]:
    """删掉一条关键帧；删空则整条轨道块一起移除。"""
    idx = _single_chunk_index(mdx, "BONE")
    data = mdx.data(mdx.chunks[idx])
    bones = bone.parse(data)
    if not 0 <= bone_index < len(bones):
        raise OpError(f"骨骼索引 {bone_index} 越界（共 {len(bones)} 根）")

    tracks = bone.parse_tracks(data, bones[bone_index])
    if tag not in tracks:
        raise OpError(f"骨骼 {bone_index} 没有 {tag} 轨道")
    flag = tracks[tag]
    if not 0 <= index < len(flag.tracks):
        raise OpError(f"{tag} 只有 {len(flag.tracks)} 条关键帧，索引 {index} 越界")
    flag.tracks.pop(index)
    if not flag.tracks:
        del tracks[tag]

    record = bone.build_record(data, bones[bone_index], tracks)
    return {idx: bone.rebuild_block(data, bones, {bone_index: record})}


# ---------- 法线（定点写回） ----------

def apply_mesh_patches(mdx: MdxFile, vertex_patches: dict, normal_patches: dict
                       ) -> dict[int, bytes]:
    """一次写回顶点坐标与法线，仍只重写 GEOS 一个 chunk。"""
    import struct as _struct

    idx = _single_chunk_index(mdx, "GEOS")
    data = bytearray(mdx.data(mdx.chunks[idx]))
    gs = geos.parse(bytes(data), format_version(mdx))

    for gi, changes in (vertex_patches or {}).items():
        for vi, xyz in changes.items():
            lo, _ = gs[gi].vertex_span(vi)
            _struct.pack_into("<3f", data, lo, *xyz)
    for gi, changes in (normal_patches or {}).items():
        for vi, xyz in changes.items():
            lo, _ = gs[gi].normal_span(vi)
            _struct.pack_into("<3f", data, lo, *xyz)

    return {idx: bytes(data)}


def mesh_spans(mdx: MdxFile, vertex_patches: dict, normal_patches: dict
               ) -> list[tuple[int, int]]:
    idx = _single_chunk_index(mdx, "GEOS")
    base = mdx.chunks[idx].data_offset
    gs = geos.parse(mdx.data(mdx.chunks[idx]), format_version(mdx))
    spans: list[tuple[int, int]] = []
    for gi, changes in (vertex_patches or {}).items():
        for vi in changes:
            lo, hi = gs[gi].vertex_span(vi)
            spans.append((base + lo, base + hi))
    for gi, changes in (normal_patches or {}).items():
        for vi in changes:
            lo, hi = gs[gi].normal_span(vi)
            spans.append((base + lo, base + hi))
    return spans


# ---------- 非节点轨道（材质图层 / 几何动画） ----------
#
# 这些记录的固定字段长度随版本变化，轨道位置靠「能完整吃到记录末尾」来定位，
# 见 chunks/animscan.find_chain。

def _soft_context(mdx: MdxFile, kind: str, mat: int | None = None,
                  layer: int | None = None, record: int | None = None):
    """返回 (chunk 序号, chunk 数据, 轨道块列表)。"""
    if kind == "layer":
        if mat is None or layer is None:
            raise OpError("材质图层需要 mat 与 layer 索引")
        idx = _single_chunk_index(mdx, "MTLS")
        data = mdx.data(mdx.chunks[idx])
        mats = mtls.parse(data, format_version(mdx))
        lay = mtls._check(mats, mat, layer)
        blocks = mtls.layer_tracks(data, lay)
        if blocks is None:
            raise OpError(f"材质 {mat} 图层 {layer} 的轨道结构无法自证，拒绝操作")
        return idx, data, blocks
    if kind == "geoa":
        if record is None:
            raise OpError("几何动画需要 record 索引")
        idx = _single_chunk_index(mdx, "GEOA")
        data = mdx.data(mdx.chunks[idx])
        items = geoa.parse(data)
        if not 0 <= record < len(items):
            raise OpError(f"几何动画索引 {record} 越界（共 {len(items)} 条）")
        blocks = geoa.record_tracks(data, items[record])
        if blocks is None:
            raise OpError(f"几何动画 {record} 的轨道结构无法自证，拒绝操作")
        return idx, data, blocks
    raise OpError(f"未知的轨道位置类型 {kind!r}，可选：layer / geoa")


def soft_tracks(mdx: MdxFile, kind: str, mat: int | None = None,
                layer: int | None = None, record: int | None = None) -> dict:
    """返回 {tag: AnimFlag}。"""
    _idx, _data, blocks = _soft_context(mdx, kind, mat, layer, record)
    return {b.tag: b for b in blocks}


def list_soft_tracks(mdx: MdxFile) -> list[dict]:
    """列全模型里所有非节点轨道，供 CLI 展示。"""
    out: list[dict] = []
    if any(c.tag == "MTLS" for c in mdx.chunks):
        idx = _single_chunk_index(mdx, "MTLS")
        data = mdx.data(mdx.chunks[idx])
        for mi, mat in enumerate(mtls.parse(data, format_version(mdx))):
            for li, lay in enumerate(mat.layers):
                for flag in (mtls.layer_tracks(data, lay) or []):
                    out.append({"kind": "layer", "mat": mi, "layer": li,
                                "tag": flag.tag, "count": len(flag.tracks),
                                "interp": flag.interp_name})
    if any(c.tag == "GEOA" for c in mdx.chunks):
        idx = _single_chunk_index(mdx, "GEOA")
        data = mdx.data(mdx.chunks[idx])
        for ri, item in enumerate(geoa.parse(data)):
            for flag in (geoa.record_tracks(data, item) or []):
                out.append({"kind": "geoa", "record": ri, "geoset": item.geoset_id,
                            "tag": flag.tag, "count": len(flag.tracks),
                            "interp": flag.interp_name})
    return out


def _resolve_soft(mdx: MdxFile, item: dict):
    _idx, _data, blocks = _soft_context(mdx, item["kind"], item.get("mat"),
                                        item.get("layer"), item.get("record"))
    tag = item["tag"]
    flag = next((b for b in blocks if b.tag == tag), None)
    if flag is None:
        have = ", ".join(b.tag for b in blocks) or "无"
        raise OpError(f"{item['kind']} 没有 {tag} 轨道；现有：{have}")
    i = item["index"]
    if not 0 <= i < len(flag.tracks):
        raise OpError(f"{tag} 只有 {len(flag.tracks)} 条关键帧，索引 {i} 越界")
    return _idx, flag, flag.tracks[i]


def patch_soft_tracks(mdx: MdxFile, items: list[dict]) -> dict[int, bytes]:
    """字段级改写材质/几何动画轨道（不改变块长度）。"""
    import struct as _struct

    by_chunk: dict[int, bytearray] = {}
    for item in items:
        idx, flag, track = _resolve_soft(mdx, item)
        buf = by_chunk.setdefault(idx, bytearray(mdx.data(mdx.chunks[idx])))
        comps = animflag.components(item["tag"])
        if "time" in item:
            _struct.pack_into("<i", buf, track.offset, int(item["time"]))
        if "value" in item:
            value = item["value"]
            if len(value) != comps:
                raise OpError(f"{item['tag']} 需要 {comps} 个分量，收到 {len(value)}")
            _struct.pack_into(f"<{comps}f", buf, track.offset + 4, *value)
        for key, which in (("in_tan", "in"), ("out_tan", "out")):
            if key in item:
                value = item[key]
                if len(value) != comps:
                    raise OpError(f"{item['tag']} 切线需要 {comps} 个分量")
                lo, _ = animflag.tan_span(track, item["tag"], which)
                _struct.pack_into(f"<{comps}f", buf, lo, *value)
    return {k: bytes(v) for k, v in by_chunk.items()}


def soft_spans(mdx: MdxFile, items: list[dict]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for item in items:
        idx, _flag, track = _resolve_soft(mdx, item)
        base = mdx.chunks[idx].data_offset
        tag = item["tag"]
        if "time" in item:
            lo, hi = animflag.time_span(track)
            spans.append((base + lo, base + hi))
        if "value" in item:
            lo, hi = animflag.value_span(track, tag)
            spans.append((base + lo, base + hi))
        for key, which in (("in_tan", "in"), ("out_tan", "out")):
            if key in item:
                lo, hi = animflag.tan_span(track, tag, which)
                spans.append((base + lo, base + hi))
    return spans
