"""节点块：把 BONE / LITE / HELP / ATCH / PREM / PRE2 / CORN / RIBB / EVTS / CAMS / CLID
   里的每条记录读出来，得到名字、objectId、parentId，以及**这两个字段的字节位置**。

为什么需要字节位置：删掉一个节点要把后面所有 objectId / parentId 减一，
这必须原地改写（长度不变），否则整块都要重建。

## 一个块到底是 96 还是 100 字节前缀

源码里两种写法都存在，差别就在**记录开头多不多一层 size**：

    单重：[inclusiveSize][name[80]][objectId][parentId][flags]           → 名字 +4、编号 +84
    双重：[外层size][inclusiveSize][name[80]][objectId][parentId][flags] → 名字 +8、编号 +88

双重来自「Chunk 里的记录类先自己读一个 size，再调用 Node.load() 又读一个」
（ATCH / PRE2 / CORN / RIBB 都是这样）；单重则是直接复用 Node 的 size
（BONE / EVTS / CLID / CAMS / HELP / LITE）。

实测 11 个模型：只有 ATCH 用错偏移时能"看起来成功"（名字非空即认），
其余都会读出 `0x00000060` 这类垃圾——这正是早先 PIVT 命名停在 BONE 段的原因。

## 尾部长度不写死

不同写入者对 `inclusiveSize` 含不含尾部固定字段不一致，所以尾部长度是**逐条探测**的：
试一个候选值，看下一条记录的名字是否可打印、编号是否严格递增，能对上就采信。
整块还必须恰好走到块尾。这些条件一起，比任何常量都可靠。

规范顺序取自 Retera `EditableModel.load`：
BONE → LITE → HELP → ATCH → PREM → PRE2 → CORN → RIBB → EVTS → CAMS → CLID
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

NODE_ORDER = ["BONE", "LITE", "HELP", "ATCH", "PREM", "PRE2", "CORN", "RIBB",
              "EVTS", "CLID"]

# CAMS 不在这里：相机**没有 objectId / parentId**（+84 处直接是坐标浮点），
# 也不占轴心点。把它算进节点表会让"节点数 = 轴心点数"这条恒等式永远不成立。
NON_NODE_TAGS = {"CAMS"}

TAG_LABELS = {
    "BONE": "骨骼", "LITE": "光源", "HELP": "辅助", "ATCH": "附着点",
    "PREM": "粒子1", "PRE2": "粒子2", "CORN": "Popcorn", "RIBB": "飘带",
    "EVTS": "事件", "CAMS": "相机", "CLID": "碰撞体",
}

# 双重 size 的块（名字 +8、编号 +88），其余是单重（+4 / +84）
DOUBLE_SIZE = frozenset({"ATCH", "PRE2", "CORN", "RIBB"})

# 尾部候选：记录里 inclusiveSize 之后可能还有多少字节。
# 前几个是实测最常见的值，放在前面能让绝大多数文件一次命中。
TAIL_CANDIDATES = [0, 8, 16, 52, 171, 20, 4, 12, 24, 28, 32, 36, 40, 44, 48,
                   56, 60, 64, 68, 72, 76, 80, 84, 88, 92, 96, 100, 104, 108,
                   112, 116, 120, 124, 128, 132, 136, 140, 260, 264]

MIN_NODE = 96


@dataclass(frozen=True)
class NodeRef:
    tag: str
    index: int              # 块内序号
    name: str
    object_id: int = -1     # 节点编号（非相机节点，这个就是 PIVT 下标）
    parent_id: int = -1
    offset: int = -1        # 记录起点（相对 chunk 数据）
    id_at: int = -1         # objectId 字段的字节位置
    par_at: int = -1        # parentId 字段的字节位置
    double_size: bool = False

    @property
    def label(self) -> str:
        return f"{TAG_LABELS.get(self.tag, self.tag)}#{self.index} {self.name}"


def _printable(raw: bytes) -> bool:
    return all(b == 0 or 32 <= b < 127 for b in raw)


def _walk(data: bytes, double_size: int, fixed_tail: int | None,
          limit: int | None) -> list[NodeRef] | None:
    """按固定布局走一遍。

    `fixed_tail` 给定时整块用同一个尾部长度（快，绝大多数文件属于这种）；
    给 None 时逐条探测（慢，只在整块猜不出来时用）。
    """
    name_at = 8 if double_size else 4
    id_at = 88 if double_size else 84
    out: list[NodeRef] = []
    pos = 0
    total = len(data)
    prev = -1
    while pos + MIN_NODE <= total:
        inc = int.from_bytes(data[pos:pos + 4], "little")
        if inc < MIN_NODE:
            return None

        if fixed_tail is None:
            tail = None
            for cand in TAIL_CANDIDATES:
                nxt = pos + inc + cand
                if nxt > total:
                    continue
                if nxt == total:
                    tail = cand
                    break
                if nxt + MIN_NODE > total:
                    continue
                ninc = int.from_bytes(data[nxt:nxt + 4], "little")
                if ninc < MIN_NODE or nxt + ninc > total:
                    continue
                if not _printable(data[nxt + name_at:nxt + name_at + 80]):
                    continue
                nid = struct.unpack_from("<i", data, nxt + id_at)[0]
                if nid < 0 or nid <= prev:
                    continue
                tail = cand
                break
            if tail is None:
                return None
        else:
            tail = fixed_tail

        raw = data[pos + name_at:pos + name_at + 80]
        if not _printable(raw):
            return None
        name = raw.split(b"\x00", 1)[0].decode("latin-1")
        obj, par = struct.unpack_from("<ii", data, pos + id_at)
        if obj < 0 or obj <= prev:
            return None
        if limit is not None and obj >= limit:
            return None
        prev = obj
        out.append(NodeRef("", len(out), name, obj, par, pos,
                           pos + id_at, pos + id_at + 4, bool(double_size)))
        pos += inc + tail

    return out if pos == total else None


def parse_records(data: bytes, tag: str, *, limit: int | None = None) -> list[NodeRef] | None:
    """读出一个节点块的所有记录；两种布局都试，结构无法自证时返回 None。

    先按「整块共用一个尾长」快速尝试（候选表按实测频率排序），
    全都不成再退到逐条自适应。
    """
    order = (1, 0) if tag in DOUBLE_SIZE else (0, 1)
    for double in order:
        for tail in TAIL_CANDIDATES:
            refs = _walk(data, double, tail, limit)
            if refs:
                return [NodeRef(tag, r.index, r.name, r.object_id, r.parent_id,
                                r.offset, r.id_at, r.par_at, r.double_size)
                        for r in refs]
        refs = _walk(data, double, None, limit)
        if refs:
            return [NodeRef(tag, r.index, r.name, r.object_id, r.parent_id,
                            r.offset, r.id_at, r.par_at, r.double_size)
                    for r in refs]
    return None


def parse_names(data: bytes, tag: str) -> list[NodeRef] | None:
    """兼容旧接口。"""
    return parse_records(data, tag)


# ---------- 全模型节点表 ----------

def scan_all(mdx, pivot_count: int | None = None) -> dict:
    """把模型里所有节点块读出来，返回可写视图 + 一致性报告。"""
    from . import pivt as pivtmod

    if pivot_count is None:
        pivot_count = (len(pivtmod.parse(mdx.data(mdx.find("PIVT"))))
                       if any(c.tag == "PIVT" for c in mdx.chunks) else 0)

    blocks: list[dict] = []
    unverified: list[str] = []
    # 编号上界：正常节点 < 轴心点数，相机等可以超出几个。
    # 给一个宽松余量就够——它的作用只是挡住"单条记录的块靠错误尾长走通"时
    # 读出来的天文数字（实测读到过 1137848869），不是用来精确分类。
    ceiling = pivot_count + 64
    for tag in NODE_ORDER:
        for ci, chunk in enumerate(mdx.chunks):
            if chunk.tag != tag:
                continue
            refs = parse_records(mdx.data(chunk), tag, limit=ceiling)
            if refs is None:
                unverified.append(tag)
                continue
            for r in refs:
                blocks.append({"tag": tag, "chunk": ci, "ref": r})

    by_id: dict[int, dict] = {}
    duplicates: list[tuple[str, int]] = []
    for b in blocks:
        oid = b["ref"].object_id
        if oid in by_id:
            duplicates.append((b["tag"], oid))
            continue
        by_id[oid] = b

    max_id = max(by_id) if by_id else -1
    missing = [i for i in range(pivot_count) if i not in by_id]

    return {
        "blocks": blocks,
        "by_id": by_id,
        "pivots": pivot_count,
        "max_id": max_id,
        "missing": missing,
        "duplicates": duplicates,
        "unverified": sorted(set(unverified)),
        "complete": not missing and not duplicates and not unverified,
    }


def analyze(mdx, pivot_count: int) -> tuple[list[NodeRef | None], str]:
    """把节点映射到轴心点下标上（按 objectId 对位，不按块顺序数）。"""
    info = scan_all(mdx, pivot_count)
    slots: list[NodeRef | None] = [None] * pivot_count
    for oid, b in info["by_id"].items():
        if 0 <= oid < pivot_count:
            slots[oid] = b["ref"]

    named = sum(1 for s in slots if s is not None)
    parts = [f"已命名 {named}/{pivot_count} 个轴心点"]
    if info["unverified"]:
        parts.append("结构无法自证：" + "、".join(info["unverified"]))
    if info["missing"]:
        parts.append(f"缺 {len(info['missing'])} 个编号")
    if info["duplicates"]:
        parts.append(f"{len(info['duplicates'])} 个编号重复")
    return slots, "；".join(parts)
