"""完整流程：从游戏数据 CASC 取出真实模型 → 修改 → 写回工作目录。

**游戏文件全程只读，一个字节都不写。** 脚本前后各拍一次游戏 Data 目录的
快照（大小 + 修改时间），必须完全一致，否则直接失败。

跑法：

    python tools/test_flow_casc.py

流程里特意覆盖两件容易出事的事：

1. **路径分隔符**：Reforged 里 `\\` 与 `/` 等价，而且**正斜杠反而是主流**
   （实测 11 个测试模型 357 条贴图：正斜杠 271 / 反斜杠 80 / 混用 6）。
   这里验证同一张贴图的三种写法在 CASC 里解析到同一个文件、读出同样的字节，
   并验证改写后统一成反斜杠仍然可用。
2. **取出后修改不碰原件**：改写在内存里的副本上进行，结果写到 `work/flow-casc/`。

产出：

    work/flow-casc/00-original.mdx        原样取出（应与 CASC 里的字节一致）
    work/flow-casc/01-sep-swap.mdx        把一条贴图路径改成混用写法（定长覆写）
    work/flow-casc/02-attachment.mdx      新增一个附着点挂武器（结构变更）
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mdxcore import parse, rebuild                      # noqa: E402
from mdxcore import nodesedit, ops                      # noqa: E402
from mdxcore import paths as P                          # noqa: E402
from mdxcore.casc import deepest_install, open_casc     # noqa: E402
from mdxcore.chunks import nodes                        # noqa: E402
from mdxcore.verify import check_chunk_rewrite, check_delta  # noqa: E402

GAME = pathlib.Path(r"D:/Games/War3 2.0")
MODEL_PATH = r"Units\Human\HeroHalahk\HeroHalahk.mdx"
OUT = pathlib.Path(__file__).resolve().parent.parent / "work" / "flow-casc"

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def snapshot(root: pathlib.Path) -> dict[str, tuple[int, int]]:
    """记录目录下每个文件的大小与修改时间，用来证明"没动过"。"""
    out: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def main() -> None:
    if not GAME.exists():
        print(f"跳过：找不到游戏目录 {GAME}")
        return
    OUT.mkdir(parents=True, exist_ok=True)

    install = deepest_install(GAME)
    data_dir = install / "Data"
    print(f"游戏数据：{install}")
    print(f"Data 目录：{data_dir}\n")

    print("步骤 0：游戏目录只读基线")
    before_game = snapshot(data_dir)
    check("记录到 Data 目录快照", len(before_game) > 0, f"{len(before_game)} 个文件")

    casc = open_casc(install)
    try:
        print("\n步骤 1：从 CASC 取出模型")
        raw = casc.read(MODEL_PATH)
        src_sha = hashlib.sha1(raw).hexdigest()
        check("模型可读取", len(raw) > 1000, f"{len(raw):,} B  sha1={src_sha[:16]}")

        dst = OUT / "00-original.mdx"
        dst.write_bytes(raw)
        check("写出的副本与 CASC 字节完全一致",
              hashlib.sha1(dst.read_bytes()).hexdigest() == src_sha)

        mdx = parse(raw)
        texs = ops.list_textures(mdx)
        names = [t.path for t in texs if not t.replaceable_id]
        forward = sum(1 for x in names if "/" in x and "\\" not in x)
        back = sum(1 for x in names if "\\" in x and "/" not in x)
        mixed = sum(1 for x in names if "/" in x and "\\" in x)
        print(f"        chunk: {' '.join(c.tag for c in mdx.chunks)}")
        print(f"        贴图 {len(texs)} 条（正斜杠 {forward} / 反斜杠 {back} / 混用 {mixed}）")

        print("\n步骤 2：两种分隔符等价性（这条是关键）")
        one = names[2] if len(names) > 2 else names[0]
        variants = {
            "原样": one,
            "全反斜杠": one.replace("/", "\\"),
            "全正斜杠": one.replace("\\", "/"),
            "混用": one.replace("/", "\\", 1).replace("\\", "/", 0) if "\\" not in one
                    else one.replace("\\", "/", 1),
        }
        digests = {}
        for label, path in variants.items():
            resolved = casc.resolve(path)
            data = casc.read(path) if resolved else None
            digests[label] = hashlib.sha1(data).hexdigest() if data else None
            print(f"        {label:6s} {path}")
            print(f"               → {resolved}")
        check("四种写法都解析到同一个文件",
              len({d for d in digests.values() if d}) == 1,
              f"{len(digests)} 种写法")
        check("四种写法读出的字节相同（分隔符对引擎透明）",
              all(v is not None for v in digests.values()))

        # 分类不受分隔符影响（否则"一键改外部路径"会误改游戏内置贴图）
        cls_ok = all(
            P.classify(v) == P.classify(one) for v in variants.values())
        check("分类结果与分隔符无关", cls_ok, f"{P.classify(one)}")

        print("\n步骤 3：改一条贴图路径（定长覆写，不能改长度）")
        target_index = 2 if len(texs) > 2 else 0
        old = texs[target_index].path
        # 换成混用写法，同时把 .tif 换成 .dds（都没变长度的话不适合；这里只改分隔符）
        new = old.replace("/", "\\", 1)
        if new == old:                     # 原本就是反斜杠，改成混用
            new = old.replace("\\", "/", 1)
        payload = ops.rename_texture(mdx, target_index, new)
        after = rebuild(mdx, payload)
        # 注意：texture_span 返回的是 chunk 内偏移，check_delta 要的是文件绝对偏移，
        # 得用 texture_spans_bulk（它自己加了 chunk.data_offset）
        spans = ops.texture_spans_bulk(mdx, [target_index])
        issues = check_delta(mdx.raw, after, mdx, spans)
        check("只在该贴图字段内变化", not issues, "；".join(issues) or f"span {spans[0]}")

        m2 = parse(after)
        t2 = ops.list_textures(m2)[target_index]
        # 写入不做规范化：给什么写什么，分隔符按调用方决定
        check("回读路径与写入值一致", t2.path == new, t2.path)
        check("改写后仍是内置分类", P.classify(t2.path) == "builtin")
        check("改写后 CASC 仍能命中", casc.resolve(t2.path) is not None)
        check("文件长度不变", len(after) == len(mdx.raw),
              f"{len(mdx.raw):,} B")
        (OUT / "01-sep-swap.mdx").write_bytes(after)

        # 改写规则要保留作者原有的分隔符风格（正斜杠是主流写法）
        print("\n步骤 3b：批量改写保留原分隔符风格")
        rule = P.Rule(mode="prefix", prefix="war3mapImported", flatten=True, only=(P.EXTERNAL,))
        fwd = "Units/Human/HeroHalahk/A.tif"
        bwd = "Units\\Human\\HeroHalahk\\B.tif"
        out_fwd = P.rewrite_one(fwd, rule)
        out_bwd = P.rewrite_one(bwd, rule)
        check("正斜杠路径改写后仍是正斜杠", out_fwd == "war3mapImported/A.tif", out_fwd)
        check("反斜杠路径改写后仍是反斜杠",
              out_bwd == "war3mapImported\\B.tif", out_bwd)

        print("\n步骤 4：新增附着点挂武器（结构变更）")
        info = nodesedit.inspect(mdx)
        check("该模型允许新增节点", info.get("ok") is True,
              info.get("reason") or f"下一个编号 {info.get('next_id')}")

        idx = nodes.analyze(mdx, len(ops.list_pivots(mdx)[0]))[0]
        parent_id = None
        for ref in idx:
            if ref and "hand" in ref.name.lower() and "right" in ref.name.lower():
                parent_id = ref.object_id
                break
        if parent_id is None:
            parent_id = next((r.object_id for r in idx if r and r.tag == "BONE"), 0)
        check("找到父骨骼", parent_id is not None, f"objectId={parent_id}")

        repl, add_info = nodesedit.insert_attachment(
            mdx, name="FlowWeapon Ref", parent_id=parent_id,
            path=r"abilities\weapons\axe\axemissile.mdx")
        after2 = rebuild(mdx, repl)
        issues2 = check_chunk_rewrite(mdx.raw, after2, mdx, {"ATCH", "PIVT"})
        check("只有 ATCH/PIVT 变化", not issues2, "；".join(issues2) or "通过")

        m3 = parse(after2)
        atts = [a for a in __import__("mdxcore.chunks.atch", fromlist=["x"]).parse(
            m3.data(m3.find("ATCH"))) if a.name == "FlowWeapon Ref"]
        check("新增的附着点可回读", len(atts) == 1,
              f"objectId={atts[0].object_id} 父={atts[0].parent_id}" if atts else "")
        check("新节点编号 = 原轴心点数", bool(atts) and atts[0].object_id == info["next_id"])
        (OUT / "02-attachment.mdx").write_bytes(after2)

        print("\n步骤 5：再次解析并重建，必须逐字节还原")
        for f in ("00-original.mdx", "01-sep-swap.mdx", "02-attachment.mdx"):
            blob = (OUT / f).read_bytes()
            mm = parse(blob)
            check(f"{f} 解析→重建一致", rebuild(mm, {}) == blob)

    finally:
        casc.close()

    print("\n步骤 6：游戏目录零改动核验")
    after_game = snapshot(data_dir)
    check("Data 目录文件数未变",
          len(after_game) == len(before_game),
          f"{len(before_game)} → {len(after_game)}")
    changed = [k for k in before_game if before_game[k] != after_game.get(k)]
    check("所有文件的大小与修改时间未变", not changed,
          "、".join(changed[:3]) if changed else f"{len(before_game)} 个文件全一致")

    print(f"\n产物目录：{OUT}")
    print(f"结论：{passed} 项通过，{failed} 项失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
