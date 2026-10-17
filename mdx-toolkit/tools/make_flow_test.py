"""完整流程测试包：挂武器 / 换动画 / 解绑 / 新增附着点，以及它们叠加。

一个文件只做一件事，互相做对照；最后一个是"换动画 + 挂武器"的叠加，
用来验证几个操作串起来不会互相破坏。

  A 原始                     对照：10 段动画，手上没有武器
  B 挂武器                   Weapon Ref → 斧子
  C 解绑动画                 关键帧清零，模型应完全静止
  D 新增附着点 + 挂武器       右手多一个挂点，与 B 互不影响
  E 换动画 + 挂武器           动画换成 Portrait 的 2 段，同时手上有斧子

武器统一用游戏自带的 `abilities\\weapons\\axe\\axemissile.mdx`，不经过 imports，
排除"文件没导入"这个变量。

跑法：
    python tools/make_flow_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mdxcore import load, rebuild                          # noqa: E402
from mdxcore import nodesedit as NE                        # noqa: E402
from mdxcore import transfer as TF                         # noqa: E402
from mdxcore.chunks import atch as atchmod                 # noqa: E402
from mdxcore.chunks import bone as bonemod                 # noqa: E402
from mdxcore.chunks import seqs as seqsmod                 # noqa: E402
from mdxcore.container import parse                        # noqa: E402
from mdxcore.verify import check_chunk_rewrite, check_delta  # noqa: E402

MODELS = HERE.parent.parent / "test-models"
OUT = HERE.parent / "out" / "flow-test"
WEAPON = r"abilities\weapons\axe\axemissile.mdx"


def find(name: str) -> Path:
    hits = sorted(MODELS.rglob(name))
    if not hits:
        raise SystemExit(f"找不到 {name}")
    return hits[0]


def delta_ok(mdx, replacements, allowed: set[str]) -> None:
    after = rebuild(mdx, replacements)
    spans = []
    for tag in allowed:
        idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == tag)
        base = mdx.chunks[idx].data_offset
        spans.append((base, base + mdx.chunks[idx].size))
    problems = check_delta(mdx.raw, after, mdx, spans)
    if problems:
        raise SystemExit(f"check_delta 失败：{problems}")


def chunk_ok(mdx, replacements) -> None:
    after = rebuild(mdx, replacements)
    problems = check_chunk_rewrite(mdx.raw, after, mdx,
                                  {mdx.chunks[i].tag for i in replacements})
    if problems:
        raise SystemExit(f"check_chunk_rewrite 失败：{problems}")


def emit(name: str, data: bytes) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(data)
    print(f"  {name:34s} {len(data):>9,} B")


def main() -> int:
    david = find("David.mdx")
    portrait = find("David_Portrait.mdx")

    print(f"目标模型 {david.name}，动画源 {portrait.name}，武器 {WEAPON}\n")

    # ---- A 原始 ----
    base = load(david)
    emit("A-原始.mdx", base.raw)
    seqs0 = seqsmod.parse(base.data(base.find("SEQS")))
    atts0 = atchmod.parse(base.data(base.find("ATCH")))
    print(f"      {len(atts0)} 个附着点，{len(seqs0)} 段动画\n")

    # ---- B 挂武器（定长覆写）----
    idx = next(i for i, a in enumerate(atts0) if a.name == "Weapon Ref")
    data = atchmod.set_path(base.data(base.find("ATCH")), atts0, idx, WEAPON)
    atch_idx = next(i for i, c in enumerate(base.chunks) if c.tag == "ATCH")
    b_repl = {atch_idx: data}
    delta_ok(base, b_repl, {"ATCH"})
    emit("B-挂武器.mdx", rebuild(base, b_repl))

    # ---- C 解绑动画（结构变更）----
    c_repl, c_stats = TF.clear_animation(base)
    chunk_ok(base, c_repl)
    emit("C-解绑动画.mdx", rebuild(base, c_repl))
    print(f"      清掉 {c_stats['cleared_bones']} 骨 / {c_stats['cleared_keys']:,} 键\n")

    # ---- D 新增附着点 + 挂武器（结构变更）----
    hand = next(b for b in bonemod.parse(base.data(base.find("BONE")))
                if b.name == "bone_hand_right")
    d_repl, d_info = NE.insert_attachment(
        base, name="FlowWeapon Ref", parent_id=hand.object_id, path=WEAPON)
    chunk_ok(base, d_repl)
    emit("D-新增附着点挂武器.mdx", rebuild(base, d_repl))
    print(f"      新点编号 {d_info['object_id']}（父 {hand.object_id} = {hand.name}），"
          f"重编号 {d_info['shifted']} 处\n")

    # ---- E 换动画 + 挂武器（两个操作串联）----
    src = load(portrait)
    e_repl, e_stats = TF.apply(base, src, replace_sequences=True)
    chunk_ok(base, e_repl)
    stage1 = rebuild(base, e_repl)
    m1 = parse(stage1)

    a1 = atchmod.parse(m1.data(m1.find("ATCH")))
    i1 = next(i for i, a in enumerate(a1) if a.name == "Weapon Ref")
    d1 = atchmod.set_path(m1.data(m1.find("ATCH")), a1, i1, WEAPON)
    c1 = next(i for i, c in enumerate(m1.chunks) if c.tag == "ATCH")
    delta_ok(m1, {c1: d1}, {"ATCH"})
    stage2 = rebuild(m1, {c1: d1})
    emit("E-换动画+挂武器.mdx", stage2)
    seqs1 = seqsmod.parse(parse(stage2).data(parse(stage2).find("SEQS")))
    print(f"      换 {e_stats['replaced_bones']} 骨 / {e_stats['keys']:,} 键，"
          f"序列 {len(seqs0)} → {len(seqs1)} 段\n")

    readme = OUT / "README.md"
    readme.write_text(f"""# 完整流程测试包

目标模型：`David.mdx`　动画源：`David_Portrait.mdx`　武器：`{WEAPON}`（游戏自带）

## 逐个看什么

| 文件 | 做了什么 | 应该看到 |
|---|---|---|
| A-原始.mdx | 无 | 10 段动画（Attack 1 / Stand 1 / Walk 1 / Death 1 …），手上没有武器 |
| B-挂武器.mdx | `Weapon Ref` 的挂载路径 | 手上出现一把斧子，切动画时跟着手走 |
| C-解绑动画.mdx | 清了 {c_stats['cleared_keys']:,} 个关键帧 | 模型**完全静止**（保持绑定姿势），切任何序列都不动 |
| D-新增附着点挂武器.mdx | 右手新加一个挂点并挂斧子 | 同样出现斧子；`Weapon Ref` 本身仍是空的 |
| E-换动画+挂武器.mdx | 动画换成 Portrait 的，同时挂斧子 | **动画只剩 2 段**（Portrait 1 / Portrait Talk 1），模型只会做肖像动作；同时手上有斧子 |

## 判断标准

- **B 出斧子** → 挂载路径生效（上一轮已验证过，这次是复现）
- **C 完全静止** → 解绑干净，骨骼与蒙皮没被破坏
- **D 出斧子、且 A/B 的模型打开也正常** → 新增节点没有破坏节点表
- **E 段数变成 2、且模型没塌陷** → 换动画与挂武器可以叠加，两个操作互不干扰

E 是关键：前面的操作都是单独做的，只有 E 把「结构变更（换动画）」和
「定长覆写（挂武器）」串在同一份文件上。如果叠加有 bug，通常表现是
模型打开后错位、部件飞出去或直接打不开。

## 如果某个不对

按这个顺序看：
1. 文件能不能打开（打不开 = 结构坏了）
2. 动画列表对不对（段数、名字）
3. 模型姿势对不对（对的姿势但少武器 = 挂载问题；姿势歪 = 动画/骨骼问题）
""", encoding="utf-8")

    print(f"输出：{OUT}")
    print("README.md 里有逐个文件的判断标准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
