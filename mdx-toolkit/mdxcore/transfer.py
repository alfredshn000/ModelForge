"""动画移植：把一个模型的动画套到另一个模型的骨骼上。

这就是用户说的「某个模型绑定了 footman 的动画，改成绑定 militia 的动画」。

**先说清 WC3 的事实**：MDX 里没有"引用外部动画"这种东西，动画是**内嵌在
每条骨骼记录里**的（KGTR/KGRT/KGSC 轨道），序列段（SEQS）只是给这些轨道
划分时间段。所以"绑定到 militia 的动画"在文件层面 = 把 militia.mdx 的
轨道数据与序列表整体搬过来。Retera 没有这个能力，是因为它把动画当作
模型的一部分，只能整模型替换。

**四个必须先想清楚的问题**，这里全部显式处理：

1. **骨骼怎么对齐**：按名字 → 规范化名字 → 层级路径 → 序号，逐级兜底。
   对上了没对上都要报，不允许静默丢掉一半骨骼。
2. **位移轨道（KGTR）跨模型不能直接用**：它是相对父节点的绝对偏移，
   两个模型比例不同就会错位。默认照搬（保证"自己绑自己"逐字节还原），
   但报告里会统计"有多少条 KGTR 是非零的"——那才是真会错位的。
3. **全局序列（globalSequenceId）**：轨道可以绑到某一段序列上。整体替换
   序列表时序号一致，不用改；不替换序列表就必须置 -1，否则会指向错误的段。
4. **目标多出来的骨骼**：默认保留原动画（可能动起来很怪，但至少不丢数据），
   也可以选择清空。

校验：改 BONE 与 SEQS 一定会改变块长度，所以走 `check_chunk_rewrite`
（允许目标 chunk 变，其余一个字节不许动）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .container import MdxFile

NODE_TAGS = ("KGTR", "KGRT", "KGSC")

_NORMALIZE = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """骨骼名规范化：小写、去掉所有非字母数字。`Bone_R_Arm` 与 `bonerarm` 视为同名。"""
    return _NORMALIZE.sub("", (name or "").lower())


def _strip_digits(name: str) -> str:
    return re.sub(r"\d+$", "", normalize(name))


@dataclass
class Pair:
    target: int                  # 目标骨骼序号
    source: int                  # 源骨骼序号
    how: str                     # name / normalized / path / index

    @property
    def label(self) -> str:
        return {"name": "同名", "normalized": "规范化同名",
                "path": "层级路径", "index": "按序号"}.get(self.how, self.how)


@dataclass
class Plan:
    pairs: list[Pair] = field(default_factory=list)
    target_only: list[int] = field(default_factory=list)     # 目标有、源没有
    source_only: list[int] = field(default_factory=list)     # 源有、目标没有
    by_how: dict[str, int] = field(default_factory=dict)

    @property
    def matched(self) -> int:
        return len(self.pairs)

    @property
    def coverage(self) -> float:
        total = self.matched + len(self.target_only)
        return self.matched / total if total else 0.0

    def describe(self) -> str:
        parts = [f"匹配 {self.matched} 根"]
        for how in ("name", "normalized", "path", "index"):
            n = self.by_how.get(how, 0)
            if n:
                parts.append(f"{ {'name':'同名','normalized':'规范化','path':'层级','index':'序号'}[how] } {n}")
        if self.target_only:
            parts.append(f"目标独有 {len(self.target_only)} 根")
        if self.source_only:
            parts.append(f"源独有 {len(self.source_only)} 根")
        return "，".join(parts)


def _paths(bones) -> dict[int, str]:
    """每根骨骼的层级路径（父链名字规范化后拼接），用于名字对不上时兜底。"""
    by_obj = {b.object_id: b for b in bones}
    cache: dict[int, str] = {}

    def walk(b, depth=0):
        if b.index in cache:
            return cache[b.index]
        if depth > 64:
            return normalize(b.name)
        parent = by_obj.get(b.parent_id)
        head = walk(parent, depth + 1) if parent is not None else ""
        cache[b.index] = (head + "/" + normalize(b.name)).strip("/")
        return cache[b.index]

    for b in bones:
        walk(b)
    return cache


def match_bones(tbones, sbones, *, allow_index: bool = True) -> Plan:
    """把目标骨骼与源骨骼配对。已配对的两边都不会再参与后续轮次。"""
    plan = Plan()
    used_t: set[int] = set()
    used_s: set[int] = set()

    def take(ti, si, how):
        plan.pairs.append(Pair(ti, si, how))
        used_t.add(ti)
        used_s.add(si)
        plan.by_how[how] = plan.by_how.get(how, 0) + 1

    # L1 原名（忽略大小写与首尾空白）
    index_s: dict[str, list[int]] = {}
    for b in sbones:
        index_s.setdefault((b.name or "").strip().lower(), []).append(b.index)
    for b in tbones:
        key = (b.name or "").strip().lower()
        cand = [i for i in index_s.get(key, []) if i not in used_s]
        if cand:
            take(b.index, cand[0], "name")

    # L2 规范化名字（去空格/下划线/点，忽略尾部数字）
    def build_norm(source: bool):
        out: dict[str, list[int]] = {}
        for b in (sbones if source else tbones):
            if (b.index in used_s) if source else (b.index in used_t):
                continue
            for key in {normalize(b.name), _strip_digits(b.name)}:
                if key:
                    out.setdefault(key, []).append(b.index)
        return out

    norm_s = build_norm(True)
    for b in tbones:
        if b.index in used_t:
            continue
        for key in (normalize(b.name), _strip_digits(b.name)):
            cand = [i for i in norm_s.get(key, []) if i not in used_s]
            if cand:
                take(b.index, cand[0], "normalized")
                break

    # L3 层级路径
    path_t = _paths(tbones)
    path_s = _paths(sbones)
    by_path_s: dict[str, list[int]] = {}
    for si, p in path_s.items():
        if si not in used_s:
            by_path_s.setdefault(p, []).append(si)
    for b in tbones:
        if b.index in used_t:
            continue
        cand = [i for i in by_path_s.get(path_t.get(b.index, ""), []) if i not in used_s]
        if cand:
            take(b.index, cand[0], "path")

    # L4 同序号（只在两侧骨骼数相差不大时启用，否则纯属乱点鸳鸯）
    if allow_index and sbones and abs(len(tbones) - len(sbones)) <= max(2, len(tbones) // 10):
        for b in tbones:
            if b.index in used_t:
                continue
            if b.index < len(sbones) and b.index not in used_s:
                take(b.index, b.index, "index")

    plan.target_only = [b.index for b in tbones if b.index not in used_t]
    plan.source_only = [b.index for b in sbones if b.index not in used_s]
    plan.pairs.sort(key=lambda p: p.target)
    return plan


# ---------- 轨道搬运 ----------

def _clone(flag, *, global_seq=None, scale_translation=None):
    """复制一条轨道。不做变换时直接返回原对象（序列化幂等，保证字节不变）。"""
    from .chunks.animflag import AnimFlag, Track

    if global_seq is None and scale_translation is None:
        return flag
    tracks = []
    for t in flag.tracks:
        value = t.value
        if scale_translation is not None and flag.tag == "KGTR":
            value = tuple(v * scale_translation for v in value)
        tracks.append(Track(time=t.time, value=value,
                            in_tan=t.in_tan, out_tan=t.out_tan))
    return AnimFlag(tag=flag.tag, interp=flag.interp,
                    global_seq_id=flag.global_seq_id if global_seq is None else global_seq,
                    tracks=tracks)


def measure(tmdx: MdxFile, smdx: MdxFile, *, translation: str = "copy",
            replace_sequences: bool = True, scale_translation: float = 1.0,
            keep_target_tracks: bool = True, allow_index: bool = True) -> dict:
    """只算不写：返回配对结果与即将发生的变化，供界面预览。"""
    from .chunks import bone as bonemod
    from .chunks import seqs as seqsmod

    tbones = _bones(tmdx)
    sbones = _bones(smdx)
    plan = match_bones(tbones, sbones, allow_index=allow_index)

    tdata = tmdx.data(tmdx.find("BONE")) if tbones else b""
    sdata = smdx.data(smdx.find("BONE")) if sbones else b""

    keys = 0
    nonzero_tr = 0
    tr_value = 0.0
    for p in plan.pairs:
        sflag = bonemod.parse_tracks(sdata, sbones[p.source])
        for tag, flag in sflag.items():
            keys += len(flag.tracks)
            if tag == "KGTR":
                for t in flag.tracks:
                    if any(abs(v) > 1e-6 for v in t.value):
                        nonzero_tr += 1

    tseq = _sequences(tmdx)
    sseq = _sequences(smdx)
    seq_same = (seqsmod.build(tseq) == seqsmod.build(sseq)) if (tseq or sseq) else True

    return {
        "pairs": [{"target": p.target, "source": p.source, "how": p.how,
                   "label": p.label,
                   "target_name": tbones[p.target].name,
                   "source_name": sbones[p.source].name} for p in plan.pairs],
        "matched": plan.matched,
        "coverage": round(plan.coverage, 4),
        "by_how": dict(plan.by_how),
        "target_only": [tbones[i].name for i in plan.target_only][:40],
        "target_only_count": len(plan.target_only),
        "source_only_count": len(plan.source_only),
        "source_bones": len(sbones),
        "target_bones": len(tbones),
        "incoming_keys": keys,
        "nonzero_translation_keys": nonzero_tr,
        "target_sequences": len(tseq),
        "source_sequences": len(sseq),
        "sequences_identical": seq_same,
        "translation": translation,
        "replace_sequences": replace_sequences,
        "note": _note(plan, translation, nonzero_tr, seq_same, replace_sequences),
    }


def _note(plan, translation, nonzero_tr, seq_same, replace_sequences) -> str:
    parts = []
    if plan.coverage < 0.5:
        parts.append("匹配率不到一半，绑上去动起来会很怪，建议先核对骨骼命名")
    if translation == "copy" and nonzero_tr:
        parts.append(f"有 {nonzero_tr} 条非零位移关键帧，两模型比例不同的话会错位"
                     f"（可改用「跳过位移」或「按比例缩放」）")
    if not replace_sequences and not seq_same:
        parts.append("未替换动画序列：段名与区间沿用目标模型，动画可能与段错位")
    return "；".join(parts)


def _bones(mdx: MdxFile):
    from .chunks import bone as bonemod

    if not any(c.tag == "BONE" for c in mdx.chunks):
        return []
    return bonemod.parse(mdx.data(mdx.find("BONE")))


def _sequences(mdx: MdxFile):
    from .chunks import seqs as seqsmod

    if not any(c.tag == "SEQS" for c in mdx.chunks):
        return []
    return seqsmod.parse(mdx.data(mdx.find("SEQS")))


def apply(tmdx: MdxFile, smdx: MdxFile, *, translation: str = "copy",
          replace_sequences: bool = True, scale_translation: float = 1.0,
          keep_target_tracks: bool = True, allow_index: bool = True,
          ) -> tuple[dict[int, bytes], dict]:
    """执行移植，返回 ({chunk 序号: 新 bytes}, 报告)。"""
    from .chunks import bone as bonemod
    from .chunks import seqs as seqsmod

    if translation not in ("copy", "skip", "scale"):
        raise ValueError(f"未知的位移处理方式 {translation!r}，可选 copy / skip / scale")

    tbones = _bones(tmdx)
    sbones = _bones(smdx)
    if not tbones:
        raise ValueError("目标模型没有骨骼，无法绑定动画")
    if not sbones:
        raise ValueError("源模型没有骨骼，没有动画可绑")

    plan = match_bones(tbones, sbones, allow_index=allow_index)
    if not plan.pairs:
        raise ValueError("两边的骨骼一根都没对上，请检查命名")

    tdata = tmdx.data(tmdx.find("BONE"))
    sdata = smdx.data(smdx.find("BONE"))
    bone_idx = next(i for i, c in enumerate(tmdx.chunks) if c.tag == "BONE")

    rebuilt: dict[int, bytes] = {}
    stats = {"replaced_bones": 0, "cleared_bones": 0, "tracks": 0, "keys": 0,
             "dropped_translation": 0}

    for p in plan.pairs:
        tbone = tbones[p.target]
        sflag = bonemod.parse_tracks(sdata, sbones[p.source])
        old = bonemod.parse_tracks(tdata, tbone)

        # 保持目标原有的轨道顺序，源多出来的追加在后面 —— 顺序一变字节就全变，
        # 「自己绑自己」就再也无法逐字节还原，那种回归测试就废了。
        new: dict[str, object] = {}
        for tag in old:
            if tag in sflag:
                new[tag] = _clone(
                    sflag[tag],
                    global_seq=(sflag[tag].global_seq_id if replace_sequences else -1),
                    scale_translation=(scale_translation if (
                        translation == "scale" and tag == "KGTR") else None))
            elif translation == "skip" and tag == "KGTR":
                stats["dropped_translation"] += 1          # 位移不搬，目标原有的也丢掉
            elif keep_target_tracks:
                new[tag] = old[tag]                        # 源没有这条轨道，保留原样
        for tag in sflag:
            if tag in new:
                continue
            if translation == "skip" and tag == "KGTR":
                continue
            new[tag] = _clone(
                sflag[tag],
                global_seq=(sflag[tag].global_seq_id if replace_sequences else -1),
                scale_translation=(scale_translation if (
                    translation == "scale" and tag == "KGTR") else None))

        for flag in new.values():
            stats["tracks"] += 1
            stats["keys"] += len(flag.tracks)
        rebuilt[p.target] = bonemod.build_record(tdata, tbone, new)
        stats["replaced_bones"] += 1

    # 目标独有、且明确要求清空的骨骼
    if not keep_target_tracks:
        for ti in plan.target_only:
            tbone = tbones[ti]
            if bonemod.parse_tracks(tdata, tbone):
                rebuilt[ti] = bonemod.build_record(tdata, tbone, {})
                stats["cleared_bones"] += 1

    replacements = {bone_idx: bonemod.rebuild_block(tdata, tbones, rebuilt)}

    # ---- 序列表 ----
    seq_replaced = False
    if replace_sequences and any(c.tag == "SEQS" for c in smdx.chunks):
        sseq = _sequences(smdx)
        tseq = _sequences(tmdx)
        if not tseq or seqsmod.build(tseq) != seqsmod.build(sseq):
            if any(c.tag == "SEQS" for c in tmdx.chunks):
                sidx = next(i for i, c in enumerate(tmdx.chunks) if c.tag == "SEQS")
                replacements[sidx] = seqsmod.build(sseq)
            else:
                seq_replaced = True                     # 目标没有 SEQS，这里只报告
                stats["no_target_seqs"] = True
            seq_replaced = True
            stats["sequences"] = len(sseq)

    stats["plan"] = {
        "matched": plan.matched,
        "coverage": round(plan.coverage, 4),
        "by_how": dict(plan.by_how),
        "target_only": len(plan.target_only),
        "source_only": len(plan.source_only),
    }
    stats["sequences_replaced"] = seq_replaced
    return replacements, stats


def clear_animation(mdx: MdxFile, *, drop_sequences: bool = False
                    ) -> tuple[dict[int, bytes], dict]:
    """解绑：把模型自己内嵌的动画全部清掉。

    只清轨道，不删骨骼 —— 骨骼还承载蒙皮权重和层级，删了模型就散架了。
    """
    from .chunks import bone as bonemod
    from .chunks import seqs as seqsmod

    bones = _bones(mdx)
    if not bones:
        raise ValueError("模型没有骨骼")
    data = mdx.data(mdx.find("BONE"))
    bone_idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "BONE")

    rebuilt: dict[int, bytes] = {}
    keys = 0
    for b in bones:
        tracks = bonemod.parse_tracks(data, b)
        if not tracks:
            continue
        keys += sum(len(f.tracks) for f in tracks.values())
        rebuilt[b.index] = bonemod.build_record(data, b, {})

    if not rebuilt:
        raise ValueError("模型本来就没有动画轨道")

    replacements = {bone_idx: bonemod.rebuild_block(data, bones, rebuilt)}
    stats = {"cleared_bones": len(rebuilt), "cleared_keys": keys}

    if drop_sequences and any(c.tag == "SEQS" for c in mdx.chunks):
        sidx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "SEQS")
        replacements[sidx] = seqsmod.build([])
        stats["sequences_dropped"] = True
    return replacements, stats


def allowed_tags(replacements: dict[int, bytes], mdx: MdxFile) -> set[str]:
    """结构变更校验用：哪些 chunk 允许变。"""
    return {mdx.chunks[i].tag for i in replacements}
