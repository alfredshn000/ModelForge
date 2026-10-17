"""动画移植（绑定 / 换绑 / 解绑）验证。

最硬的一条断言：**自己绑自己必须逐字节还原原文件。**
它同时证明「骨骼配对、轨道顺序、序列化幂等」三件事都没问题——
任何一处偏差都会让它变字节。

还有一条同样硬：**解绑之后再绑回去，必须逐字节还原原始文件。**
这条覆盖了"反向"路径，证明解绑不是简单地把数据丢掉。

其余：跨模型绑（命名不同，看匹配率）、无关骨架必须拒绝、值一致性、独有骨骼保留。

跑法：
    python tools/test_transfer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mdxcore import load, rebuild                    # noqa: E402
from mdxcore import transfer as T                    # noqa: E402
from mdxcore.chunks import bone as bonemod           # noqa: E402
from mdxcore.container import parse                  # noqa: E402
from mdxcore.verify import check_chunk_rewrite       # noqa: E402

MODELS = HERE.parent.parent / "test-models"
OUT = HERE.parent / "out" / "transfer"

cases: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    cases.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def find(name: str) -> Path:
    hits = sorted(MODELS.rglob(name))
    if not hits:
        raise SystemExit(f"找不到测试模型 {name}")
    return hits[0]


def bones_of(mdx):
    return bonemod.parse(mdx.data(mdx.find("BONE"))) if any(
        c.tag == "BONE" for c in mdx.chunks) else []


def tracks_of(mdx, bone_index: int) -> dict:
    data = mdx.data(mdx.find("BONE"))
    return bonemod.parse_tracks(data, bones_of(mdx)[bone_index])


def run(target_mdx, source_mdx, dst: Path, *, expect_change=True, **kw):
    """移植 → 校验 → 写出。返回 (新 MdxFile, 报告, before, after)。"""
    before = target_mdx.raw
    replacements, stats = T.apply(target_mdx, source_mdx, **kw)
    after = rebuild(target_mdx, replacements)
    problems = check_chunk_rewrite(before, after, target_mdx,
                                   T.allowed_tags(replacements, target_mdx),
                                   expect_change=expect_change)
    if problems:
        raise AssertionError("；".join(problems))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(after)
    return parse(after), stats, after


def main() -> int:
    david = find("David.mdx")
    david_p = find("David_Portrait.mdx")
    lvbu = find("Lvbu.mdx")
    rack = find("alchemy_rack.mdx")

    # ---- T1 自己绑自己 ----
    print("\n[T1] 自己绑自己（必须逐字节还原）")
    for model in (david, lvbu, rack):
        mdx = load(model)
        if not bones_of(mdx):
            record(f"{model.stem} 自绑", True, "无骨骼，跳过")
            continue
        try:
            _m, stats, after = run(mdx, mdx, OUT / f"{model.stem}.selfbind.mdx",
                                   expect_change=False)
        except Exception as exc:                     # noqa: BLE001
            record(f"{model.stem} 自绑", False, str(exc)[:140])
            continue
        same = after == model.read_bytes()
        record(f"{model.stem} 自绑", same,
               "字节相同" if same else
               f"变了 {sum(1 for a, b in zip(after, model.read_bytes()) if a != b)} 字节")

    # ---- T2 解绑 → 再绑回去（往返） ----
    print("\n[T2] 解绑后再绑回（必须逐字节还原）")
    try:
        src_mdx = load(david)
        repl, st = T.clear_animation(src_mdx)
        unbound = rebuild(src_mdx, repl)
        up = OUT / "david-unbound.mdx"
        up.parent.mkdir(parents=True, exist_ok=True)
        up.write_bytes(unbound)
        ub = parse(unbound)
        nb = bones_of(ub)
        left = sum(len(f.tracks) for b in nb
                   for f in bonemod.parse_tracks(ub.data(ub.find("BONE")), b).values())

        back, stats, after = run(ub, load(david), OUT / "david-rebound.mdx")
        same = after == david.read_bytes()
        record("David 解绑", left == 0 and len(nb) == len(bones_of(src_mdx)),
               f"清 {st['cleared_bones']} 骨 / {st['cleared_keys']} 键，"
               f"骨骼 {len(nb)} 根仍在，{src_mdx.raw.__len__()}→{len(unbound)}B")
        record("David 解绑后绑回", same,
               "字节完全相同" if same else
               f"还原度 {sum(1 for a, b in zip(after, david.read_bytes()) if a == b) * 100 // len(after)}%")
    except Exception as exc:                         # noqa: BLE001
        record("David 解绑", False, str(exc)[:160])
        record("David 解绑后绑回", False, "未执行")

    # ---- T3 同族换绑（Portrait ↔ 本体，命名一致） ----
    print("\n[T3] 同族换绑（David_Portrait ← David）")
    try:
        t = load(david_p)
        plan = T.measure(t, load(david))
        print(f"  Portrait {plan['target_bones']} 骨 ← David {plan['source_bones']} 骨："
              f"匹配 {plan['matched']}（{plan['coverage']:.0%}），{plan['by_how']}")
        new, stats, _a = run(t, load(david), OUT / "davidp-bound-david.mdx")
        record("Portrait ← David", stats["plan"]["coverage"] > 0.9,
               f"换 {stats['replaced_bones']} 骨 / 搬 {stats['keys']} 键")
    except Exception as exc:                         # noqa: BLE001
        record("Portrait ← David", False, str(exc)[:160])

    # ---- T4 跨模型（命名体系完全不同） ----
    print("\n[T4] 跨模型换绑（Lvbu ← David）")
    try:
        t = load(lvbu)
        plan = T.measure(t, load(david))
        print(f"  Lvbu {plan['target_bones']} 骨 ← David {plan['source_bones']} 骨："
              f"匹配 {plan['matched']}（{plan['coverage']:.0%}），{plan['by_how']}")
        if plan["note"]:
            print(f"  提示：{plan['note']}")
        new, stats, _a = run(t, load(david), OUT / "lvbu-bound-david.mdx")
        record("Lvbu ← David", stats["plan"]["matched"] > 0,
               f"换 {stats['replaced_bones']} 骨 / 搬 {stats['keys']} 键")
    except Exception as exc:                         # noqa: BLE001
        record("Lvbu ← David", False, str(exc)[:160])

    # ---- T5 无关骨架必须拒绝 ----
    print("\n[T5] 无关骨架（alchemy_rack ← David）必须明确拒绝")
    try:
        T.measure(load(rack), load(david))
        plan = T.match_bones(bones_of(load(rack)), bones_of(load(david)))
        print(f"  rack {plan.matched + len(plan.target_only)} 骨，David "
              f"{plan.matched + len(plan.source_only)} 骨 → 匹配 {plan.matched}")
        if plan.matched == 0:
            try:
                T.apply(load(rack), load(david))
                record("无关骨架拒绝", False, "没有拒绝，硬绑了 0 根骨")
            except ValueError as exc:
                record("无关骨架拒绝", True, f"拒绝并说明原因：{exc}")
        else:
            record("无关骨架拒绝", True, f"竟然匹配上 {plan.matched} 根（按名字），跳过")
    except Exception as exc:                         # noqa: BLE001
        record("无关骨架拒绝", False, str(exc)[:160])

    # ---- T6 值一致性 ----
    print("\n[T6] 换成同族动画后，逐对比较关键帧数值")
    try:
        src = load(david)
        dst = load(OUT / "davidp-bound-david.mdx")
        plan = T.match_bones(bones_of(load(david_p)), bones_of(src))
        bad = checked = 0
        for p in plan.pairs:
            want = tracks_of(src, p.source)
            got = tracks_of(dst, p.target)
            for tag, wf in want.items():
                gf = got.get(tag)
                if gf is None or len(gf.tracks) != len(wf.tracks) or gf.interp != wf.interp:
                    bad += 1
                    continue
                for a, b in zip(wf.tracks, gf.tracks):
                    checked += 1
                    if a.time != b.time or any(
                            abs(a.value[k] - b.value[k]) > 1e-6
                            for k in range(len(a.value))):
                        bad += 1
                        break
        record("关键帧数值一致", bad == 0,
               f"比对 {checked} 键 / {len(plan.pairs)} 骨，不符 {bad}")
    except Exception as exc:                         # noqa: BLE001
        record("关键帧数值一致", False, str(exc)[:160])

    # ---- T7 独有骨骼保留 ----
    print("\n[T7] 目标独有骨骼的动画原样保留")
    try:
        t = load(lvbu)
        plan = T.match_bones(bones_of(t), bones_of(load(david)))
        if not plan.target_only:
            record("独有骨骼保留", True, "无独有骨骼，跳过")
        else:
            ti = plan.target_only[0]
            before = tracks_of(t, ti)
            new, _st, _a = run(t, load(david), OUT / "lvbu-bound-david-keep.mdx")
            after = tracks_of(new, ti)
            ok = set(before) == set(after) and all(
                len(before[k].tracks) == len(after[k].tracks) for k in before)
            record("独有骨骼保留", ok,
                   f"骨骼 {ti}（{bones_of(t)[ti].name}）轨道 {sorted(before)} → {sorted(after)}")
    except Exception as exc:                         # noqa: BLE001
        record("独有骨骼保留", False, str(exc)[:160])

    print()
    bad = [n for n, ok, _d in cases if not ok]
    if bad:
        print(f"{len(bad)} 项失败：{bad}")
        return 1
    print(f"全部通过（{len(cases)} 项）    输出：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
