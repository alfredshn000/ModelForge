"""中间插入 / 删除附着点的引擎验证包。

前面所有引擎验证用的都是"末尾追加"（重编号 0 处）。中间插删会重编号 20–30 处，
是唯一还没被引擎证实的一环。这里补上。

为了让"重编号有没有搞错"肉眼可判，两个文件都**同时挂了两把斧子**：

  · 一把挂在新插入的点上（编号在插入点）
  · 一把挂在原有的 `Weapon Ref` 上（编号在插入点之后，会被重编号）

如果 parentId 重映射错了，第二把斧子会跑到错误的位置（或者飞到原点）。
两把都在正确位置，才说明整条父子链在重排后依然成立。

  F 中间插入：在 `Hand Right Ref`(122) 之前插入新点并挂斧，同时 `Weapon Ref` 也挂斧
  G 中间删除：在 F 的基础上删掉 `Hand Left Ref`(121)，两把斧子应原封不动

跑法：
    python tools/make_mid_edit_test.py
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
from mdxcore.verify import check_chunk_rewrite           # noqa: E402

MODELS = HERE.parent.parent / "test-models"
OUT = HERE.parent / "out" / "mid-edit-test"
WEAPON = r"abilities\weapons\axe\axemissile.mdx"


def find(name: str) -> Path:
    hits = sorted(MODELS.rglob(name))
    if not hits:
        raise SystemExit(f"找不到 {name}")
    return hits[0]


def emit(name: str, mdx, replacements, note: str = "") -> bytes:
    after = rebuild(mdx, replacements)
    problems = check_chunk_rewrite(mdx.raw, after, mdx,
                                  {mdx.chunks[i].tag for i in replacements})
    if problems:
        raise SystemExit(f"{name} 校验失败：{problems}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(after)
    print(f"  {name:28s} {len(mdx.raw):>9,} → {len(after):>9,} B  {note}")
    return after


def armed(data: bytes) -> list[str]:
    m = parse(data)
    items = atchmod.parse(m.data(m.find("ATCH")))
    return [f"{a.name}#{a.object_id}(父{a.parent_id})" for a in items if a.path]


def main() -> int:
    base = load(find("David.mdx"))
    items = atchmod.parse(base.data(base.find("ATCH")))
    atch_idx = next(i for i, c in enumerate(base.chunks) if c.tag == "ATCH")

    # ---- 先给原有的 Weapon Ref 挂上斧子（定长覆写）----
    idx_w = next(i for i, a in enumerate(items) if a.name == "Weapon Ref")
    data = atchmod.set_path(base.data(base.find("ATCH")), items, idx_w, WEAPON)
    m1 = parse(rebuild(base, {atch_idx: data}))
    print(f"第一步：Weapon Ref 挂斧子（id {items[idx_w].object_id}，"
          f"父 {items[idx_w].parent_id}）\n")

    # ---- F 在 Hand Right Ref(122) 之前插入新点 ----
    right = next(a for a in atchmod.parse(m1.data(m1.find("ATCH")))
                 if a.name == "Hand Right Ref")
    f_repl, f_info = NE.insert_attachment(
        m1, name="MidTest Ref", parent_id=right.parent_id, path=WEAPON,
        at=right.object_id)
    f_bytes = emit("F-中间插入.mdx", m1, f_repl,
                   f"插入 id={f_info['object_id']}，重编号 {f_info['shifted']} 处")
    print(f"      挂载点：{armed(f_bytes)}")

    # ---- G 在 F 的基础上删掉 Hand Left Ref（中间位置）----
    mF = parse(f_bytes)
    itemsF = atchmod.parse(mF.data(mF.find("ATCH")))
    left = next(a for a in itemsF if a.name == "Hand Left Ref")
    idx_left = next(i for i, a in enumerate(itemsF) if a.name == "Hand Left Ref")
    g_repl, g_info = NE.remove_attachment(mF, idx_left)
    g_bytes = emit("G-中间删除.mdx", mF, g_repl,
                   f"删「{left.name}」id={left.object_id}，重编号 {g_info['shifted']} 处")
    print(f"      挂载点：{armed(g_bytes)}")

    after_f = atchmod.parse(parse(f_bytes).data(parse(f_bytes).find("ATCH")))
    after_g = atchmod.parse(parse(g_bytes).data(parse(g_bytes).find("ATCH")))
    print(f"\n  F：{len(after_f)} 个附着点；G：{len(after_g)} 个")

    OUT.joinpath("README.md").write_text(f"""# 中间插删验证包

模型：`David.mdx`　武器：`{WEAPON}`（游戏自带）

前面所有引擎验证用的都是"末尾追加"（重编号 0 处）。这两个文件专门验证
**中间插入 / 删除**——它会重编号 20–30 处，是目前唯一还没被引擎证实的一环。

## 关键设计：同时挂两把斧子

| 斧子 | 挂在 | 编号 | 作用 |
|---|---|---|---|
| 第一把 | 新插入的 `MidTest Ref` | 在插入点 | 证明新节点生效 |
| 第二把 | 原有的 `Weapon Ref` | 在插入点**之后**，被重编号 | 证明 parentId 重映射正确 |

`Weapon Ref` 的父节点是 `weapon_bind_jnt`（一个专门给武器用的骨骼），
位置和右手不同。所以两把斧子应该出现在**两个不同位置**。

如果重编号出错，第二把会跑位、缩到原点或者消失。

## 逐个看

| 文件 | 做了什么 | 应该看到 |
|---|---|---|
| F-中间插入.mdx | 在 `Hand Right Ref` 之前插一个新点并挂斧 | **两把斧子**，一把在右手、一把在武器骨骼位置；模型其余部分完全正常 |
| G-中间删除.mdx | 在 F 基础上删掉 `Hand Left Ref` | 仍然**两把斧子、位置不变**；模型正常 |

## 判断

- 两把斧子都在正确位置 → 中间插删的重编号是对的
- 只有一把（新点那把）→ parentId 重映射错了，第二把丢了
- 有部件错位/飞出 → 编号重排破坏了父子链
- 打不开 → 结构坏了

G 的编号会正好回到接近原始状态（删掉一个插入点之前的节点），
所以 G 和 A-原始 的差别应该只有"少一个空附着点 + 两把斧子"。
""", encoding="utf-8")

    print(f"\n输出：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
