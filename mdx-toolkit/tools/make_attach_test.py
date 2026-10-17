"""生成 ATCH 挂载机制的验证包。

要回答的问题只有一个：**ATCH 的 path 字段到底能不能让引擎把子模型画出来。**
这是"挂武器"整条路线的前提。如果它不生效，那么无论怎么改 path 都没用，
必须改走单位数据（Art - Attachment Point + 物品技能）。

四个文件，两两成对：

  A 原始                       对照
  B 现有 Weapon Ref 挂斧子     父节点 weapon_bind_jnt
  C 现有 Hand Right Ref 挂斧子 父节点 bone_hand_right
  D 新增附着点挂斧子           父节点 bone_hand_right（验证"末尾追加"是否生效）

B / C 用同一个武器模型、不同的附着点：
  - 只有 B 出、C 不出 → 说明位置对了，机制有效
  - 两个都出        → 机制有效，挂哪都行
  - 两个都不出      → 机制不生效，路线要换

C / D 位置相同：
  - C 出、D 不出 → 新增节点的编号假设有问题，需要重编号引擎
  - 都出        → 新增节点可用

跑法：
    python tools/make_attach_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mdxcore import load, rebuild                        # noqa: E402
from mdxcore import nodesedit as NE                      # noqa: E402
from mdxcore.chunks import atch as atchmod               # noqa: E402
from mdxcore.container import parse                      # noqa: E402
from mdxcore.verify import check_chunk_rewrite, check_delta  # noqa: E402

MODEL = HERE.parent.parent / "test-models" / "03-animated" / "David" / "David.mdx"
OUT = HERE.parent / "out" / "attach-test"

# 游戏数据里真实存在的模型（CASC 里查到的），不带进 imports 也能显示
WEAPON = r"abilities\weapons\axe\axemissile.mdx"


def emit(name: str, mdx, replacements, allowed, mode: str) -> Path:
    after = rebuild(mdx, replacements)
    if mode == "delta":
        spans = []
        for tag in allowed:
            idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == tag)
            base = mdx.chunks[idx].data_offset
            spans.append((base, base + mdx.chunks[idx].size))
        problems = check_delta(mdx.raw, after, mdx, spans)
    else:
        problems = check_chunk_rewrite(mdx.raw, after, mdx, allowed)
    if problems:
        raise SystemExit(f"{name} 校验失败：{problems}")
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / name
    dst.write_bytes(after)
    print(f"  {name:38s} {len(mdx.raw):>9,} → {len(after):>9,} B")
    return dst


def main() -> int:
    mdx = load(MODEL)
    items = atchmod.parse(mdx.data(mdx.find("ATCH")))
    by_name = {a.name: a for a in items}
    print(f"模型 {MODEL.name}：附着点 {len(items)} 个，轴心点 {len(mdx.data(mdx.find('PIVT'))) // 12} 个")
    print(f"武器：{WEAPON}\n")

    # A 原始
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "A-原始.mdx").write_bytes(mdx.raw)
    print(f"  {'A-原始.mdx':38s} {len(mdx.raw):>9,} B")

    # B / C 改现有附着点的挂载路径（定长覆写）
    for tag, name in (("B", "Weapon Ref"), ("C", "Hand Right Ref")):
        a = by_name[name]
        data = atchmod.set_path(mdx.data(mdx.find("ATCH")), items, a.index, WEAPON)
        idx = next(i for i, c in enumerate(mdx.chunks) if c.tag == "ATCH")
        emit(f"{tag}-{name}挂斧子.mdx", mdx, {idx: data}, {"ATCH"}, "delta")

    # D 新增附着点（结构变更）
    bones = __import__("mdxcore.chunks.bone", fromlist=["x"]).parse(
        mdx.data(mdx.find("BONE")))
    hand = next((b for b in bones if b.name == "bone_hand_right"), bones[0])
    repl, info = NE.append_attachment(
        mdx, name="TestWeapon Ref", parent_id=hand.object_id, path=WEAPON)
    emit("D-新增附着点挂斧子.mdx", mdx, repl, {"ATCH", "PIVT"}, "chunk")
    print(f"\n  新增节点编号 {info['object_id']}（轴心点由 "
          f"{info['object_id']} 个变 {info['object_id'] + 1} 个），父节点 "
          f"{hand.object_id} = {hand.name}")

    readme = OUT / "README.md"
    readme.write_text(f"""# ATCH 挂载验证包

模型：David.mdx　武器：`{WEAPON}`（游戏自带，无需 imports）

## 怎么看

放进编辑器打开，切到 **Attack** 或 **Stand** 动画，找右手附近有没有出现一把斧子。

| 文件 | 改了什么 | 看出斧子 = 说明 |
|---|---|---|
| A-原始.mdx | 无 | 对照，应该没有斧子 |
| B-Weapon Ref挂斧子.mdx | 现有附着点 `Weapon Ref`（父节点 `weapon_bind_jnt`）的 path | ATCH 的 path 生效 |
| C-Hand Right Ref挂斧子.mdx | 现有附着点 `Hand Right Ref`（父节点 `bone_hand_right`）的 path | 同上，且位置正确 |
| D-新增附着点挂斧子.mdx | 新加一个附着点（父节点 `bone_hand_right`，编号 {info['object_id']}） | 新增节点生效 |

## 怎么判断

- **B 或 C 任意一个出斧子** → 机制有效，挂武器这条路走得通
- **B、C 都不出** → ATCH 的 path 在 Reforged 不生效，挂武器要改走单位数据
  （Art - Attachment Point + 物品技能），模型层面改 path 没意义
- **C 出、D 不出** → 现有附着点能用，但"末尾追加"的编号假设不成立，
  需要补节点重编号引擎
- **C 和 D 都出** → 全通

## 附带确认

斧子如果出现，注意两点：
1. 它是否**跟着手臂动**（切到 Attack 看）—— 这是 parentId 生效的证明
2. 它在手上的**朝向**是否可接受。ATCH 只给位置和父骨骼的坐标系，
   朝向由父骨骼决定；如果歪了，说明需要调父节点或改用 `weapon_bind_jnt`
""", encoding="utf-8")

    print(f"\n输出：{OUT}")
    print("把整个 attach-test 目录放进编辑器逐一看即可，README.md 里有判断表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
