"""在任意记录区间里找出连续的动画轨道块。

节点（BONE/HELP/…）的轨道位置是固定的（紧跟 96 字节前缀），但材质图层、
几何动画、粒子发射器这些记录的「固定字段」长度随版本变化，没法写死偏移。

做法是**不猜**：从候选起点开始按已知 tag 连续解析，只有**正好走到记录末尾**才采信。
走不通就整段放弃，绝不返回半对的结果。
"""

from __future__ import annotations

from . import animflag


class AnimScanError(ValueError):
    """区间内不是一串完整的动画轨道。"""


def walk(data: bytes, lo: int, hi: int) -> list[animflag.AnimFlag]:
    """解析 [lo, hi) 里的连续动画块；必须正好用到 hi。"""
    out: list[animflag.AnimFlag] = []
    p = lo
    while p + 4 <= hi:
        tag = animflag.peek_tag(data, p)
        if tag not in animflag.COMPONENTS:
            raise AnimScanError(f"位置 {p} 的 tag {tag!r} 不是已知动画块")
        try:
            flag = animflag.parse(data, p, tag)
        except animflag.AnimFlagError as exc:
            raise AnimScanError(str(exc)) from exc
        if p + flag.size > hi:
            raise AnimScanError(f"{tag}@{p} 长度 {flag.size} 超出区间末尾 {hi}")
        out.append(flag)
        p += flag.size
    if p != hi:
        raise AnimScanError(f"解析到 {p}，区间末尾是 {hi}（剩 {hi - p} 字节未识别）")
    return out


def try_walk(data: bytes, lo: int, hi: int) -> list[animflag.AnimFlag] | None:
    """同 walk，但失败返回 None（用于只读展示：宁可少显示，也不显示错的）。"""
    if hi <= lo:
        return []
    try:
        return walk(data, lo, hi)
    except AnimScanError:
        return None


def find_chain(data: bytes, lo: int, hi: int):
    """在 [lo, hi) 里找「最早能完整吃到末尾」的动画块链。

    固定字段的长度随版本变化，写死偏移必然出错（实测 1000 版材质图层就比
    公式短）。这里改成搜索：从 lo 起逐字节找已知 tag，尝试一路解析到 hi，
    第一个成功的起点就是轨道起点。

    返回 (起点, [AnimFlag…])；一个都找不到时返回 (None, [])（说明这段是纯字段）。
    """
    p = lo
    while p + 4 <= hi:
        tag = animflag.peek_tag(data, p)
        if tag in animflag.COMPONENTS:
            try:
                blocks = walk(data, p, hi)
            except AnimScanError:
                blocks = None
            if blocks is not None:
                return p, blocks
        p += 1
    return None, []
