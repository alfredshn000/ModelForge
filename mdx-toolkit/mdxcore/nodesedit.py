"""节点增删：在节点表的任意位置插入 / 删除附着点。

## 为什么现在能做完整版了

早先只敢在末尾追加，是因为"认不全节点块就没法重编号"。现在节点解析层
（`chunks/nodes.py`）能保证：**节点数 == 轴心点数，编号严格 0..n-1，无缺失无重复**
（11 个真实模型全部成立）。有了这张完整的表，重编号就是逐个写 4 字节而已。

## 一个关键简化：不需要碰 GEOS

GEOS 里有两处引用节点编号：`MATS`（矩阵组 → 节点）和 `SKIN`（每顶点的骨骼）。
但**骨骼的编号永远小于附着点**（BONE 排在最前），而这里只增删附着点。
所以只要确认 `k > 最大骨骼编号`，那些被引用的编号一个都不会动。

这条不是假设，是每次操作前都校验的前置条件（`_guard_region`）。

## 两个必须做对的细节

1. **物理顺序**：引擎按声明顺序给轴心点编号，作者的实现
   `EditableModel.java:1969` 是 `obj.objectId = idObjects.indexOf(obj)`。
   所以插入记录必须落在 ATCH 块内"按编号排好序"的位置上，不能随手追加。
2. **parentId 也要跟着挪**：某个节点的父节点编号 >= k 时同样 +1/-1，
   否则父子关系会指错人。

验收标准：**插入一条再删掉，必须逐字节还原原文件**（与关键帧增删同一档）。
"""

from __future__ import annotations

import struct

from .container import MdxFile


class NodeEditError(ValueError):
    """前置条件不满足（宁可不动，也不改出个坏模型）。"""


def _pivots(mdx: MdxFile):
    from .chunks import pivt as pivtmod

    if not any(c.tag == "PIVT" for c in mdx.chunks):
        raise NodeEditError("模型没有 PIVT（轴心点）块")
    return pivtmod.parse(mdx.data(mdx.find("PIVT")))


def _attachments(mdx: MdxFile):
    from .chunks import atch as atchmod

    if not any(c.tag == "ATCH" for c in mdx.chunks):
        return [], None
    return atchmod.parse(mdx.data(mdx.find("ATCH"))), mdx.find("ATCH")


def _scan(mdx: MdxFile):
    from .chunks import nodes as nodesmod
    from .chunks import pivt as pivtmod

    count = (len(pivtmod.parse(mdx.data(mdx.find("PIVT"))))
             if any(c.tag == "PIVT" for c in mdx.chunks) else 0)
    return nodesmod.scan_all(mdx, count)


def _bone_ceiling(mdx: MdxFile) -> int:
    """最大骨骼编号。增删必须发生在它之后，GEOS 才完全不用动。"""
    from .chunks import bone as bonemod

    if not any(c.tag == "BONE" for c in mdx.chunks):
        return -1
    bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    return max((b.object_id for b in bones), default=-1)


def inspect(mdx: MdxFile) -> dict:
    """能不能增删、下一个编号是多少。界面先调它，避免按了才报错。"""
    try:
        points = _pivots(mdx)
    except NodeEditError as exc:
        return {"ok": False, "reason": str(exc), "pivots": 0, "attachments": 0}

    items, _chunk = _attachments(mdx)
    scan = _scan(mdx)
    n = len(points)
    has_atch = any(c.tag == "ATCH" for c in mdx.chunks)

    reason = ""
    if not has_atch:
        reason = "模型没有 ATCH 块；新建整块会改变 chunk 序列，暂不支持"
    elif not scan["complete"]:
        detail = []
        if scan["unverified"]:
            detail.append("无法自证：" + "、".join(scan["unverified"]))
        if scan["missing"]:
            detail.append(f"缺 {len(scan['missing'])} 个编号")
        if scan["duplicates"]:
            detail.append(f"{len(scan['duplicates'])} 个编号重复")
        reason = "节点表不完整（" + "；".join(detail) + "），重编号有风险，已拒绝"

    return {
        "ok": not reason,
        "reason": reason,
        "has_atch": has_atch,
        "pivots": n,
        "nodes": len(scan["by_id"]),
        "attachments": len(items),
        "max_node_id": scan["max_id"],
        "bone_ceiling": _bone_ceiling(mdx),
        "next_id": n,
        "complete": scan["complete"],
    }


# ---------- 内部：重编号 ----------

def _shift(mdx: MdxFile, editor, threshold: int, delta: int) -> int:
    """把所有 >= threshold 的 objectId 与 parentId 平移 delta，返回改了几处。"""
    scan = _scan(mdx)
    touched = 0
    for b in scan["blocks"]:
        r = b["ref"]
        chunk = mdx.chunks[b["chunk"]]
        if r.object_id >= threshold:
            editor.write(chunk.tag, r.id_at, "<i", r.object_id + delta)
            touched += 1
        if r.parent_id >= threshold:
            editor.write(chunk.tag, r.par_at, "<i", r.parent_id + delta)
            touched += 1
    return touched


def _pivt_insert(data: bytes, index: int, xyz) -> bytes:
    at = index * 12
    if not 0 <= at <= len(data):
        raise NodeEditError(f"轴心点插入位置 {index} 越界（共 {len(data) // 12} 个）")
    return data[:at] + struct.pack("<3f", *xyz) + data[at:]


def _pivt_remove(data: bytes, index: int) -> bytes:
    at = index * 12
    if not 0 <= at < len(data):
        raise NodeEditError(f"轴心点删除位置 {index} 越界（共 {len(data) // 12} 个）")
    return data[:at] + data[at + 12:]


def _guard_region(mdx: MdxFile, k: int) -> None:
    """只有落在骨骼之后的操作才安全（GEOS 的 MATS/SKIN 引用的都是骨骼）。"""
    ceiling = _bone_ceiling(mdx)
    if k <= ceiling:
        raise NodeEditError(
            f"插入/删除位置 {k} 落在骨骼区（最大骨骼编号 {ceiling}）之内，"
            "会牵动 GEOS 的矩阵组与蒙皮索引，暂不支持")


# ---------- 插入 ----------

def insert_attachment(mdx: MdxFile, *, name: str, parent_id: int,
                      path: str = "", attachment_id: int | None = None,
                      xyz=None, at: int | None = None) -> tuple[dict[int, bytes], dict]:
    """在节点表第 `at` 个位置插入一个附着点；`at=None` 表示追加到末尾。"""
    from .chunks import atch as atchmod
    from .writers import ChunkEditor

    name = (name or "").strip()
    if not name:
        raise NodeEditError("附着点名字不能为空")

    status = inspect(mdx)
    if not status["ok"]:
        raise NodeEditError(status["reason"])

    points = _pivots(mdx)
    items, atch_chunk = _attachments(mdx)
    n = len(points)

    if at is None:
        at = n
    if not 0 <= at <= n:
        raise NodeEditError(f"插入位置 {at} 超出范围（0–{n}）")

    k = at
    _guard_region(mdx, k)

    if not 0 <= parent_id < n:
        raise NodeEditError(f"父节点 {parent_id} 超出范围（0–{n - 1}）")

    existing_ids = [a.attachment_id for a in items]
    if attachment_id is None:
        attachment_id = (max(existing_ids) + 1) if existing_ids else 0
    if len(name.encode("latin-1", "replace")) > 79:
        raise NodeEditError(f"名字超过 79 字节：{name!r}")
    if len(path.encode("latin-1", "replace")) > 255:
        raise NodeEditError(f"路径超过 255 字节：{path!r}")
    if xyz is None:
        xyz = points[parent_id].xyz

    # 1) 先把 >= k 的编号整体 +1（新记录还没进文件，不会误伤自己）
    editor = ChunkEditor(mdx)
    shifted = _shift(mdx, editor, k, +1)
    replacements = editor.apply()

    # 2) 在平移后的 ATCH 数据上按编号顺序插入新记录
    atch_idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "ATCH")
    data = replacements.get(atch_idx, mdx.data(atch_chunk))
    offset = _insert_offset(items, data, k)
    record = atchmod.build_record(name=name, object_id=k, parent_id=parent_id,
                                 path=path, attachment_id=attachment_id)
    replacements[atch_idx] = data[:offset] + record + data[offset:]

    # 3) 轴心点插到第 k 个位置
    pivt_idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "PIVT")
    pdata = replacements.get(pivt_idx, mdx.data(mdx.chunks[pivt_idx]))
    replacements[pivt_idx] = _pivt_insert(pdata, k, xyz)

    return replacements, {
        "object_id": k, "pivot_index": k, "name": name,
        "parent_id": parent_id, "path": path, "attachment_id": attachment_id,
        "xyz": list(xyz), "shifted": shifted,
        "attachments_after": len(items) + 1,
        "position": "末尾" if at == n else f"第 {at} 条之前",
    }


def _insert_offset(items, data: bytes, k: int) -> int:
    """新记录该插到 ATCH 块里的哪个字节位置 —— 按编号排序。"""
    for a in items:
        if a.object_id >= k:
            return a.offset
    return len(data)


# ---------- 删除 ----------

def remove_attachment(mdx: MdxFile, index: int) -> tuple[dict[int, bytes], dict]:
    """删掉任意一个附着点。"""
    from .writers import ChunkEditor

    items, atch_chunk = _attachments(mdx)
    if not items:
        raise NodeEditError("模型没有附着点可删")
    if not 0 <= index < len(items):
        raise NodeEditError(f"附着点索引 {index} 越界（共 {len(items)} 条）")

    status = inspect(mdx)
    if not status["ok"]:
        raise NodeEditError(status["reason"])

    target = items[index]
    k = target.object_id
    _guard_region(mdx, k)

    if _referenced_by_skin(mdx, k):
        raise NodeEditError(
            f"节点 {k} 被蒙皮权重引用，删掉会让相关顶点失去骨骼，已拒绝")

    # 1) >= k+1 的编号整体 -1
    editor = ChunkEditor(mdx)
    shifted = _shift(mdx, editor, k + 1, -1)
    replacements = editor.apply()

    # 2) 从 ATCH 块里切掉这条记录
    atch_idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "ATCH")
    data = replacements.get(atch_idx, mdx.data(atch_chunk))
    replacements[atch_idx] = data[:target.offset] + data[target.offset + target.outer:]

    # 3) 删掉第 k 个轴心点
    pivt_idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "PIVT")
    pdata = replacements.get(pivt_idx, mdx.data(mdx.chunks[pivt_idx]))
    replacements[pivt_idx] = _pivt_remove(pdata, k)

    return replacements, {
        "removed": index, "object_id": k, "name": target.name,
        "shifted": shifted,
        "attachments_after": len(items) - 1,
        "bytes_removed": target.outer + 12,
    }


def _referenced_by_skin(mdx: MdxFile, node_id: int) -> bool:
    from . import skin
    from .chunks import geos as geosmod
    from .ops import format_version

    if not any(c.tag == "GEOS" for c in mdx.chunks):
        return False
    try:
        geosets = geosmod.parse(mdx.data(mdx.find("GEOS")), format_version(mdx))
    except Exception:                              # noqa: BLE001
        return True                                # 解析不了就当被引用，宁可拒绝
    for g in geosets:
        for infl in skin.influences(g):
            for one in infl:
                if one.node_index == node_id and one.weight > 0:
                    return True
    return False
