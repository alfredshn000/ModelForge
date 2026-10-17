"""GEOS 块：几何体（只读解析 + 字节级可逆重建）。

布局依据 Retera 的 `GeosetChunk.java`（标签化版本）：

    inclusiveSize(u32)
    "VRTX" count  positions     (3×f32)
    "NRMS" count  normals       (3×f32)
    "PTYP" count  faceTypes     (u32)
    "PCNT" count  faceGroups    (u32)
    "PVTX" count  faces         (u16，每三角形 3 个索引)
    "GNDX" count  vertexGroups  (u8)
    "MTGC" count  matrixGroups  (u32)
    "MATS" count  matrixIndices (u32)
    materialId / selectionGroup / selectionType           (3×u32)
    [version >= 900]  lod(i32) + lodName[80]
    boundsRadius(f32) + minExtent(3f) + maxExtent(3f)
    nrOfExtents(u32) + nrOfExtents × Extent(28B)
    [version >= 1100] 可选 "TANG"(count×4f) / "SKIN"(count×8B)
    "UVAS" nrOfUVSets(u32)
       每套: "UVBS" count + count×2f

`build()` 逐字节重建原数据，用来证明解析没有遗漏字段。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

LOD_NAME_LEN = 80

# LOD（i32 + name[80]）从 900 起一直存在，1800 实测仍在。
LOD_MIN_VERSION = 900

# TANG / SKIN 是**可选子块**，不能按版本判断。
#
# 最初以为「1800 里没有 TANG/SKIN」，因为 footman 确实没有。
# 但全量体检 3,458 个模型后发现：113 个 1800 的模型**有** TANG
# （cinematics 下的过场动画模型居多）。
# 所以正确的做法是探测标签：读到就解析，读不到就跳过。
TANG_SKIN_VERSIONS = (900, 1000, 1100, 1200)      # 仅供 build 的旧路径参考


class GeosError(ValueError):
    """GEOS 块结构不合法。"""


@dataclass
class Extent:
    bounds_radius: float = 0.0
    min_extent: tuple = (0.0, 0.0, 0.0)
    max_extent: tuple = (0.0, 0.0, 0.0)


@dataclass
class Geoset:
    offset: int = 0
    size: int = 0
    vertices: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    face_types: list = field(default_factory=list)
    face_groups: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    vertex_groups: bytes = b""
    matrix_groups: list = field(default_factory=list)
    matrix_indices: list = field(default_factory=list)
    material_id: int = 0
    selection_group: int = 0
    selection_type: int = 0
    has_lod: bool = False
    lod: int = 0
    lod_name: str = ""
    bounds_radius: float = 0.0
    min_extent: tuple = (0.0, 0.0, 0.0)
    max_extent: tuple = (0.0, 0.0, 0.0)
    extents: list = field(default_factory=list)
    tangents: list | None = None
    skin: bytes | None = None
    uv_sets: list = field(default_factory=list)
    vertices_offset: int = -1      # vertices 数组在 GEOS 块内的起始偏移
    normals_offset: int = -1

    def vertex_span(self, index: int) -> tuple[int, int]:
        """第 index 个顶点在 GEOS 块内的 [起, 止) 偏移。"""
        if self.vertices_offset < 0:
            raise GeosError("该 geoset 没有记录顶点数组偏移")
        if not 0 <= index < self.vertex_count:
            raise GeosError(f"顶点索引 {index} 越界（共 {self.vertex_count} 个）")
        start = self.vertices_offset + index * 12
        return start, start + 12

    def normal_span(self, index: int) -> tuple[int, int]:
        """第 index 个法线在 GEOS 块内的 [起, 止) 偏移。"""
        if self.normals_offset < 0:
            raise GeosError("该 geoset 没有记录法线数组偏移")
        if not 0 <= index < len(self.normals):
            raise GeosError(f"法线索引 {index} 越界（共 {len(self.normals)} 个）")
        start = self.normals_offset + index * 12
        return start, start + 12

    @property
    def triangle_count(self) -> int:
        return len(self.faces) // 3

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)


class _Cur:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def tag(self, expect: str) -> None:
        got = self.d[self.p:self.p + 4]
        if got != expect.encode():
            raise GeosError(f"位置 {self.p} 期望标签 {expect}，实际 {got!r}")
        self.p += 4

    def peek_tag(self) -> str:
        return self.d[self.p:self.p + 4].decode("latin-1", "replace")

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def vec3(self) -> tuple:
        v = struct.unpack_from("<3f", self.d, self.p)
        self.p += 12
        return v

    def vec3s(self, n: int) -> list:
        return [self.vec3() for _ in range(n)]

    def ints(self, n: int) -> list:
        v = list(struct.unpack_from(f"<{n}i", self.d, self.p))
        self.p += n * 4
        return v

    def shorts(self, n: int) -> list:
        v = list(struct.unpack_from(f"<{n}H", self.d, self.p))
        self.p += n * 2
        return v

    def floats(self, n: int) -> list:
        v = list(struct.unpack_from(f"<{n}f", self.d, self.p))
        self.p += n * 4
        return v

    def raw(self, n: int) -> bytes:
        v = self.d[self.p:self.p + n]
        self.p += n
        return v


def parse(data: bytes, version: int) -> list[Geoset]:
    out: list[Geoset] = []
    pos = 0
    total = len(data)

    while pos + 8 <= total:
        size = struct.unpack_from("<I", data, pos)[0]
        if size < 8 or pos + size > total:
            raise GeosError(f"geoset @{pos} 声明 {size} 字节，超出块长度 {total}")
        end = pos + size
        g = Geoset(offset=pos, size=size)
        c = _Cur(data, pos + 4)

        c.tag("VRTX")
        n = c.u32()
        g.vertices_offset = c.p
        g.vertices = c.vec3s(n)

        c.tag("NRMS")
        n = c.u32()
        g.normals_offset = c.p
        g.normals = c.vec3s(n)

        c.tag("PTYP")
        g.face_types = c.ints(c.u32())

        c.tag("PCNT")
        g.face_groups = c.ints(c.u32())

        c.tag("PVTX")
        g.faces = c.shorts(c.u32())

        c.tag("GNDX")
        g.vertex_groups = c.raw(c.u32())

        c.tag("MTGC")
        g.matrix_groups = c.ints(c.u32())

        c.tag("MATS")
        g.matrix_indices = c.ints(c.u32())

        g.material_id = c.i32()
        g.selection_group = c.u32()
        g.selection_type = c.u32()

        if version >= LOD_MIN_VERSION:
            g.has_lod = True
            g.lod = c.i32()
            g.lod_name = c.raw(LOD_NAME_LEN).split(b"\x00", 1)[0].decode("latin-1")

        g.bounds_radius = c.f32()
        g.min_extent = c.vec3()
        g.max_extent = c.vec3()

        for _ in range(c.u32()):
            ext = Extent()
            ext.bounds_radius = c.f32()
            ext.min_extent = c.vec3()
            ext.max_extent = c.vec3()
            g.extents.append(ext)

        # 无条件探测：TANG / SKIN 在同一个版本里也是可选的
        if version >= LOD_MIN_VERSION:
            if c.peek_tag() == "TANG":
                c.tag("TANG")
                # TANG 的计数是「切线条数」，每条 4 个 float
                g.tangents = c.floats(c.u32() * 4)
            if c.peek_tag() == "SKIN":
                c.tag("SKIN")
                count = c.u32()
                # SKIN 有两种布局，靠「后面是否紧接 UVAS」区分：
                #   旧（1000/1100/1200）：count = 总字节数，每顶点 8 字节
                #                       （4×u8 索引 + 4×u8 权重，权重 /255）
                #   新（1800，带 TANG 的模型）：count = 索引区字节数，
                #                       紧跟等长的权重区，每顶点共 16 字节
                #                       （4×u16 索引 + 4×u16 权重，权重 /65535）
                # 实测：新格式 4×u16 权重之和正好 255（如 190+61+4），
                # 但用 u16 存储所以是 65535 的刻度 —— 换算时除以 65535 再乘 255。
                tail = c.d[c.p + count:c.p + count + 4]
                g.skin = c.raw(count * 2 if tail != b"UVAS" else count)

        c.tag("UVAS")
        uv_count = c.u32()
        for _ in range(uv_count):
            c.tag("UVBS")
            n = c.u32()
            g.uv_sets.append([(c.f32(), c.f32()) for _ in range(n)])

        if c.p != end:
            raise GeosError(
                f"geoset @{pos} 解析到 {c.p}，与 inclusiveSize 声明的 {end} 不符"
            )

        out.append(g)
        pos = end

    if pos != total:
        raise GeosError(f"GEOS 解析结束于 {pos}，块长度为 {total}")
    return out


def build(geosets: list[Geoset], version: int) -> bytes:
    """按原字段逐字节重建，用于验证解析完整性。"""
    out = bytearray()
    for g in geosets:
        body = bytearray()

        body += b"VRTX" + struct.pack("<I", len(g.vertices))
        for v in g.vertices:
            body += struct.pack("<3f", *v)

        body += b"NRMS" + struct.pack("<I", len(g.normals))
        for v in g.normals:
            body += struct.pack("<3f", *v)

        body += b"PTYP" + struct.pack("<I", len(g.face_types))
        body += struct.pack(f"<{len(g.face_types)}i", *g.face_types)

        body += b"PCNT" + struct.pack("<I", len(g.face_groups))
        body += struct.pack(f"<{len(g.face_groups)}i", *g.face_groups)

        body += b"PVTX" + struct.pack("<I", len(g.faces))
        body += struct.pack(f"<{len(g.faces)}H", *g.faces)

        body += b"GNDX" + struct.pack("<I", len(g.vertex_groups))
        body += g.vertex_groups

        body += b"MTGC" + struct.pack("<I", len(g.matrix_groups))
        body += struct.pack(f"<{len(g.matrix_groups)}i", *g.matrix_groups)

        body += b"MATS" + struct.pack("<I", len(g.matrix_indices))
        body += struct.pack(f"<{len(g.matrix_indices)}i", *g.matrix_indices)

        body += struct.pack("<iII", g.material_id, g.selection_group, g.selection_type)

        if version >= LOD_MIN_VERSION:
            body += struct.pack("<i", g.lod)
            body += g.lod_name.encode("latin-1").ljust(LOD_NAME_LEN, b"\x00")

        body += struct.pack("<f", g.bounds_radius)
        body += struct.pack("<3f", *g.min_extent)
        body += struct.pack("<3f", *g.max_extent)

        body += struct.pack("<I", len(g.extents))
        for e in g.extents:
            body += struct.pack("<f", e.bounds_radius)
            body += struct.pack("<3f", *e.min_extent)
            body += struct.pack("<3f", *e.max_extent)

        # 与 parse 对称：有就写，没有就不写，不看版本
        if version >= LOD_MIN_VERSION:
            if g.tangents is not None:
                body += b"TANG" + struct.pack("<I", len(g.tangents) // 4)
                body += struct.pack(f"<{len(g.tangents)}f", *g.tangents)
            if g.skin is not None:
                wide = len(g.skin) == len(g.vertices) * 16
                body += b"SKIN" + struct.pack("<I", len(g.skin) // 2 if wide else len(g.skin))
                body += g.skin

        body += b"UVAS" + struct.pack("<I", len(g.uv_sets))
        for uv in g.uv_sets:
            body += b"UVBS" + struct.pack("<I", len(uv))
            for u, v in uv:
                body += struct.pack("<2f", u, v)

        out += struct.pack("<I", len(body) + 4)
        out += body
    return bytes(out)


# ---------- 结构校验 ----------
#
# 这些不变量是从真实资产里实测出来的（132 个 geoset 全部满足）：
#   - NRMS / GNDX / 每套 UV / TANG / SKIN 都是「每顶点一份」
#   - GNDX 的长度要么是 0（不使用顶点组），要么正好等于顶点数
#   - PCNT 记的是**索引数**（每三角形 3 个），不是三角形数
#   - MTGC 之和 == MATS 长度（每个矩阵组的节点索引数）
#   - 所有面索引 < 顶点数

def validate(g: Geoset) -> None:
    nv = g.vertex_count
    if len(g.normals) != nv:
        raise GeosError(f"法线数 {len(g.normals)} ≠ 顶点数 {nv}")
    if len(g.vertex_groups) not in (0, nv):
        raise GeosError(f"顶点组数 {len(g.vertex_groups)} 既非 0 也非顶点数 {nv}")
    for i, uv in enumerate(g.uv_sets):
        if len(uv) != nv:
            raise GeosError(f"UV 组 {i} 有 {len(uv)} 项，应为 {nv}")
    if g.tangents is not None and len(g.tangents) != nv * 4:
        raise GeosError(f"切线数 {len(g.tangents)} ≠ 顶点数×4 {nv * 4}")
    if g.skin is not None and len(g.skin) not in (nv * 8, nv * 16):
        raise GeosError(
            f"蒙皮字节数 {len(g.skin)} 既不是 顶点×8（{nv * 8}）也不是 顶点×16（{nv * 16}）")
    if sum(g.matrix_groups) != len(g.matrix_indices):
        raise GeosError(
            f"MTGC 之和 {sum(g.matrix_groups)} ≠ MATS 长度 {len(g.matrix_indices)}")
    if sum(g.face_groups) != len(g.faces):
        raise GeosError(f"PCNT 之和 {sum(g.face_groups)} ≠ 面索引数 {len(g.faces)}")
    if g.faces and max(g.faces) >= nv:
        raise GeosError(f"面索引 {max(g.faces)} ≥ 顶点数 {nv}")
    if len(g.faces) % 3:
        raise GeosError(f"面索引数 {len(g.faces)} 不是 3 的倍数")


def recompute_bounds(g: Geoset) -> None:
    """按 Retera 的口径重算包围盒：半径 = 到原点的最大距离。"""
    if not g.vertices:
        return
    xs = [v[0] for v in g.vertices]
    ys = [v[1] for v in g.vertices]
    zs = [v[2] for v in g.vertices]
    g.min_extent = (min(xs), min(ys), min(zs))
    g.max_extent = (max(xs), max(ys), max(zs))
    g.bounds_radius = max((x * x + y * y + z * z) ** 0.5 for x, y, z in g.vertices)
    for ext in g.extents:
        ext.bounds_radius = g.bounds_radius
        ext.min_extent = g.min_extent
        ext.max_extent = g.max_extent


# ---------- 顶点增删（结构变更） ----------

def add_vertex(g: Geoset, position, *, source: int | None = None,
               normal=None, uv=None, group: int | None = None) -> int:
    """追加一个顶点，返回新顶点索引。

    新增顶点**不会**被任何三角形引用（悬空顶点是合法的）。要让它是可见的面，
    再调 add_triangle()。

    `source` 指定克隆哪个已有顶点的属性（法线 / UV / 顶点组 / 切线 / 蒙皮）。
    WC3 的顶点各自带一份属性，凭空造出来的顶点没有蒙皮权重就不会跟随骨骼，
    所以默认克隆最后一个顶点。
    """
    if source is None:
        if not g.vertices:
            raise GeosError("空 geoset 无法推断克隆源，请显式指定 source")
        source = len(g.vertices) - 1
    if not 0 <= source < len(g.vertices):
        raise GeosError(f"克隆源 {source} 越界（共 {len(g.vertices)} 个顶点）")

    # 记下加之前的顶点数：SKIN 有 8 / 16 字节两种步长，要靠它推断
    old_nv = len(g.vertices)

    g.vertices.append((float(position[0]), float(position[1]), float(position[2])))
    g.normals.append(tuple(normal) if normal is not None else g.normals[source])

    if len(g.vertex_groups) == 0:
        pass                                    # 原模型不用顶点组，保持 0 长度
    else:
        byte = g.vertex_groups[source] if group is None else group & 0xFF
        g.vertex_groups = g.vertex_groups + bytes([byte])

    for i, uv_set in enumerate(g.uv_sets):
        g.uv_sets[i].append(tuple(uv[i]) if uv is not None else uv_set[source])

    if g.tangents is not None:
        g.tangents += list(g.tangents[source * 4:source * 4 + 4])

    if g.skin is not None:
        stride = 16 if len(g.skin) % 16 == 0 and len(g.skin) // 16 == old_nv else 8
        g.skin += g.skin[source * stride:(source + 1) * stride]

    return len(g.vertices) - 1


def add_triangle(g: Geoset, a: int, b: int, c: int) -> None:
    """追加一个三角形，挂在最后一个面组上。"""
    nv = len(g.vertices)
    for v in (a, b, c):
        if not 0 <= v < nv:
            raise GeosError(f"三角形顶点 {v} 越界（共 {nv} 个）")
    if not g.face_groups:
        g.face_types = [4]
        g.face_groups = [0]
    g.faces.extend((a, b, c))
    g.face_groups[-1] += 3


def delete_vertices(g: Geoset, indices) -> dict:
    """删除若干顶点，并丢弃所有引用它们的三角形。

    返回统计信息（删了多少顶点 / 多少个三角形）。

    注意：不重映射 MTGC/MATS —— 它们是「矩阵组 → 节点索引」的映射，
    按三角形/顶点组引用，与顶点序号无关；删顶点后某个矩阵组可能不再被任何
    顶点使用，这是合法的（引擎按顶点走 GNDX 找矩阵组）。
    """
    drop = sorted({int(i) for i in indices})
    if not drop:
        raise GeosError("没有指定要删除的顶点")
    nv = len(g.vertices)
    for v in drop:
        if not 0 <= v < nv:
            raise GeosError(f"顶点索引 {v} 越界（共 {nv} 个）")

    drop_set = set(drop)
    tri_count = len(g.faces) // 3

    # 每个三角形属于哪个面组（面组按索引数切分缓冲区）
    group_of = []
    gi = 0
    acc = 0
    for t in range(tri_count):
        while gi < len(g.face_groups) - 1 and t * 3 >= acc + g.face_groups[gi]:
            acc += g.face_groups[gi]
            gi += 1
        group_of.append(gi)

    keep_tri = []
    dropped_per_group = {}
    for t in range(tri_count):
        trio = g.faces[t * 3:t * 3 + 3]
        if any(v in drop_set for v in trio):
            gname = group_of[t]
            dropped_per_group[gname] = dropped_per_group.get(gname, 0) + 1
        else:
            keep_tri.append(t)

    remap = {}
    new_index = 0
    for old in range(nv):
        if old not in drop_set:
            remap[old] = new_index
            new_index += 1

    g.faces = [remap[g.faces[t * 3 + k]] for t in keep_tri for k in range(3)]
    for gname, dec in dropped_per_group.items():
        g.face_groups[gname] -= dec * 3

    keep = [i for i in range(nv) if i not in drop_set]
    g.vertices = [g.vertices[i] for i in keep]
    g.normals = [g.normals[i] for i in keep]
    if g.vertex_groups:
        g.vertex_groups = bytes(g.vertex_groups[i] for i in keep)
    for i, uv_set in enumerate(g.uv_sets):
        g.uv_sets[i] = [uv_set[k] for k in keep]
    if g.tangents is not None:
        g.tangents = [g.tangents[i * 4 + k] for i in keep for k in range(4)]
    if g.skin is not None:
        stride = 16 if len(g.skin) == nv * 16 else 8
        g.skin = b"".join(g.skin[i * stride:(i + 1) * stride] for i in keep)

    return {"vertices_removed": len(drop),
            "triangles_removed": tri_count - len(keep_tri),
            "vertices_left": len(g.vertices),
            "triangles_left": len(keep_tri)}
