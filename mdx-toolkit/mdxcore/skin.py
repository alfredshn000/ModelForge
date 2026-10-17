"""蒙皮权重：从 GEOS 推出「每个顶点受哪些节点影响、权重多少」。

两条路径：

1. **Reforged SKIN 子块**（优先），有两种布局：

   - 旧（1000/1100/1200）：每顶点 8 字节 = 4×u8 索引 + 4×u8 权重，权重 /255
   - 新（1800，带 TANG 的模型）：索引区 nv×8（4×u16）+ 权重区 nv×8（4×u16），
     权重同样是 0–255 的刻度（实测 4 个权重之和正好 255），只是用 u16 存

   两者靠长度区分：`len(skin) == nv*16` 就是新格式。

2. **Legacy 矩阵组**（没有 SKIN 时）
   - `GNDX` 每顶点 1 字节 → 顶点属于第 g 个矩阵组
   - `MTGC` 每组有多少个矩阵索引
   - `MATS` 扁平的节点索引数组
   顶点受该组内所有节点影响，权重均分（绝大多数情况组内只有 1 个节点，即权重 1.0）。

节点索引即轴心点索引（骨骼排在最前，与 PIVT 对齐）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Influence:
    node_index: int
    weight: float


def _from_skin(geoset) -> list[list[Influence]] | None:
    if not geoset.skin:
        return None
    n = geoset.vertex_count
    if len(geoset.skin) < n * 8:
        return None

    wide = len(geoset.skin) >= n * 16
    out: list[list[Influence]] = []
    for v in range(n):
        if wide:
            # 每顶点 16 字节连续：4×u16 索引 + 4×u16 权重（都是小端）
            # 实测 4 个权重之和正好 255，如 190+61+4
            base = v * 16
            bones = [int.from_bytes(geoset.skin[base + k * 2:base + k * 2 + 2], "little")
                     for k in range(4)]
            weights = [int.from_bytes(geoset.skin[base + 8 + k * 2:base + 8 + k * 2 + 2], "little")
                       for k in range(4)]
        else:
            base = v * 8
            bones = list(geoset.skin[base:base + 4])
            weights = list(geoset.skin[base + 4:base + 8])
        # 保留原始 w/255，不做二次归一化：归一化后再量化回 255 会引入误差，
        # 而 WC3 的 4 个权重本就应该相加为 255；Blender 变形时会自行归一化。
        #
        # 注意：同一根骨骼可能占多个槽位（实测见过 132 + 6 分两槽），
        # 必须按 node_index 合并，否则写进 Blender 顶点组时会互相覆盖。
        # 两种布局的权重刻度都是 255（u16 只是存储更宽），所以统一 /255
        merged: dict[int, float] = {}
        for k in range(4):
            if weights[k] > 0:
                merged[bones[k]] = merged.get(bones[k], 0.0) + weights[k] / 255.0
        out.append([Influence(n, w) for n, w in merged.items()])
    return out


def _from_matrix_groups(geoset) -> list[list[Influence]]:
    counts = geoset.matrix_groups
    flat = geoset.matrix_indices
    starts = []
    acc = 0
    for c in counts:
        starts.append(acc)
        acc += c

    out: list[list[Influence]] = []
    for v in range(geoset.vertex_count):
        g = geoset.vertex_groups[v] if v < len(geoset.vertex_groups) else 0
        if 0 <= g < len(counts):
            lo = starts[g]
            hi = lo + counts[g]
            nodes = [n for n in flat[lo:hi] if n >= 0]
        else:
            nodes = []
        if not nodes:
            out.append([])
            continue
        w = 1.0 / len(nodes)
        out.append([Influence(n, w) for n in nodes])
    return out


def influences(geoset) -> list[list[Influence]]:
    """返回每个顶点的骨骼影响列表（权重已归一化）。"""
    skin = _from_skin(geoset)
    if skin is not None:
        return skin
    return _from_matrix_groups(geoset)


def summary(geoset) -> dict:
    inf = influences(geoset)
    counts = [len(i) for i in inf]
    unweighted = sum(1 for c in counts if c == 0)
    return {
        "vertices": len(inf),
        "max_influences": max(counts) if counts else 0,
        "unweighted": unweighted,
        "source": "SKIN" if geoset.skin else "matrixGroups",
    }
