"""附着点增删验证（任意位置）。

最硬的两条：
  · **末尾加一条再删掉 → 逐字节还原**
  · **中间插一条、再删同一个位置 → 逐字节还原**

中间插删会重编号它后面所有节点的 objectId / parentId，所以还要一条语义断言：
**删掉一条附着点之后，其余每个节点的"自己名字 → 父节点名字"必须完全不变。**
这条比字节比对更能抓住"编号挪错一位"的错误——那种错字节层面看起来很正常。

跑法：
    python tools/test_nodesedit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mdxcore import load, rebuild                      # noqa: E402
from mdxcore import nodesedit as NE                    # noqa: E402
from mdxcore.chunks import atch as atchmod             # noqa: E402
from mdxcore.chunks import bone as bonemod             # noqa: E402
from mdxcore.chunks import nodes as nodesmod           # noqa: E402
from mdxcore.chunks import pivt as pivtmod             # noqa: E402
from mdxcore.container import parse                    # noqa: E402
from mdxcore.verify import check_chunk_rewrite         # noqa: E402

MODELS = HERE.parent.parent / "test-models"
OUT = HERE.parent / "out" / "nodesedit"

cases: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    cases.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def rebuild_checked(mdx, replacements, dst: Path | None = None) -> bytes:
    after = rebuild(mdx, replacements)
    problems = check_chunk_rewrite(mdx.raw, after, mdx,
                                  {mdx.chunks[i].tag for i in replacements})
    if problems:
        raise AssertionError("；".join(problems))
    if dst:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(after)
    return after


def parent_map(mdx) -> dict[str, str]:
    """每个节点：名字 → 父节点名字。用来验证重编号没有把父子关系搞错。"""
    info = nodesmod.scan_all(mdx)
    out = {}
    for b in info["blocks"]:
        r = b["ref"]
        par = info["by_id"].get(r.parent_id)
        out[f"{r.tag}:{r.name}:{r.object_id}"] = par["ref"].name if par else "（根）"
    return out


def parent_map_by_name(mdx) -> dict[str, str]:
    """去掉编号，只留 名字 → 父名字（编号会变，名字不会）。"""
    info = nodesmod.scan_all(mdx)
    out = {}
    for b in info["blocks"]:
        r = b["ref"]
        par = info["by_id"].get(r.parent_id)
        out[f"{r.tag}:{r.name}"] = par["ref"].name if par else "（根）"
    return out


def main() -> int:
    print("[T1] 可增删性")
    targets = []
    for p in sorted(MODELS.rglob("*.mdx")):
        m = load(p)
        info = NE.inspect(m)
        print(f"  {p.name:30s} {'可' if info['ok'] else '否'}  轴心点 {info['pivots']:3d}  "
              f"节点 {info.get('nodes', 0):3d}  附着点 {info['attachments']:2d}  "
              f"骨骼上界 {info.get('bone_ceiling', -1):3d}  {info['reason'][:34]}")
        if info["ok"]:
            targets.append(p)
    record("可增删的模型数 ≥ 5", len(targets) >= 5, f"{len(targets)} 个")

    for model in targets:
        print(f"\n=== {model.name} ===")
        base = load(model)
        info = NE.inspect(base)
        items = atchmod.parse(base.data(base.find("ATCH")))
        bones = bonemod.parse(base.data(base.find("BONE")))
        hand = next((b for b in bones if "hand" in b.name.lower()), bones[0])
        before_map = parent_map_by_name(base)

        try:
            # ---- 末尾追加 ----
            repl, meta = NE.insert_attachment(
                base, name="AppendTest Ref", parent_id=hand.object_id,
                path="war3mapImported\\T.mdx")
            after = rebuild_checked(base, repl, OUT / f"{model.stem}.append.mdx")
            new = parse(after)
            got = atchmod.parse(new.data(new.find("ATCH")))[-1]
            record(f"{model.stem} 末尾追加",
                   got.name == "AppendTest Ref" and len(after) - len(base.raw) == got.outer + 12,
                   f"id={got.object_id} +{len(after) - len(base.raw)}B")

            # ---- 末尾加再删 → 逐字节还原 ----
            rm, _ = NE.remove_attachment(new, len(atchmod.parse(new.data(new.find("ATCH")))) - 1)
            restored = rebuild(new, rm)
            record(f"{model.stem} 末尾加再删逐字节还原", restored == base.raw,
                   "字节完全相同" if restored == base.raw else
                   f"差 {sum(1 for a, b in zip(restored, base.raw) if a != b)} 字节")

            # ---- 中间插入（插在第 2 条附着点之前；必须落在骨骼区之后）----
            at = items[1].object_id if len(items) >= 2 else items[0].object_id
            repl2, meta2 = NE.insert_attachment(
                base, name="MiddleTest Ref", parent_id=hand.object_id,
                path="war3mapImported\\M.mdx", at=at)
            after2 = rebuild_checked(base, repl2, OUT / f"{model.stem}.middle.mdx")
            mid = parse(after2)
            mid_items = atchmod.parse(mid.data(mid.find("ATCH")))
            target = next((a for a in mid_items if a.object_id == at), None)
            if target is None:
                raise AssertionError(f"插入后找不到 objectId={at} 的附着点")
            ids = [a.object_id for a in mid_items]
            ok_mid = (target.name == "MiddleTest Ref" and target.object_id == at
                      and ids == sorted(ids) and len(set(ids)) == len(ids))
            record(f"{model.stem} 中间插入",
                   ok_mid, f"位置 {at} 得到 id={target.object_id}，"
                           f"重编号 {meta2['shifted']} 处，附着点 {len(mid_items)} 条")

            # ---- 中间插入后父子关系未变（编号挪动正确）----
            mid_map = parent_map_by_name(mid)
            expect = dict(before_map)
            expect["ATCH:MiddleTest Ref"] = hand.name
            ok_parents = mid_map == expect
            record(f"{model.stem} 中间插入父子关系不变", ok_parents,
                   f"{len(mid_map)} 个节点" if ok_parents else
                   f"差异 {[(k, expect.get(k), mid_map.get(k)) for k in (set(expect) | set(mid_map)) if expect.get(k) != mid_map.get(k)][:3]}")

            # ---- 中间插再删同一位置 → 逐字节还原 ----
            idx_mid = next(i for i, a in enumerate(mid_items) if a.object_id == at)
            rm2, _ = NE.remove_attachment(mid, idx_mid)
            back = rebuild(mid, rm2)
            record(f"{model.stem} 中间插再删逐字节还原", back == base.raw,
                   "字节完全相同" if back == base.raw else
                   f"差 {sum(1 for a, b in zip(back, base.raw) if a != b)} 字节")

            # ---- 删已有的一条 ----
            if len(items) >= 2:
                victim = items[1]
                rm3, meta3 = NE.remove_attachment(base, 1)
                after3 = rebuild_checked(base, rm3, OUT / f"{model.stem}.del.mdx")
                gone = parse(after3)
                left = atchmod.parse(gone.data(gone.find("ATCH")))
                gone_map = parent_map_by_name(gone)
                expect3 = {k: v for k, v in before_map.items() if k != f"ATCH:{victim.name}"}
                expect3 = {k: v for k, v in expect3.items() if v != victim.name}
                ok3 = (len(left) == len(items) - 1
                       and victim.name not in [a.name for a in left]
                       and gone_map == expect3
                       and len(pivtmod.parse(gone.data(gone.find("PIVT")))) == info["pivots"] - 1)
                record(f"{model.stem} 删除中间已有", ok3,
                       f"删「{victim.name}」，剩 {len(left)} 条，重编号 {meta3['shifted']} 处"
                       + ("" if ok3 else f"，父子关系差异 {len([k for k in set(expect3) | set(gone_map) if expect3.get(k) != gone_map.get(k)])} 处"))

            # ---- 越界父节点 ----
            try:
                NE.insert_attachment(base, name="X", parent_id=info["pivots"] + 3)
                record(f"{model.stem} 越界父节点拒绝", False, "没有拒绝")
            except NE.NodeEditError as exc:
                record(f"{model.stem} 越界父节点拒绝", True, str(exc)[:46])
        except Exception as exc:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            record(f"{model.stem} 用例", False, str(exc)[:120])

    # ---- 无 ATCH 的模型必须拒绝 ----
    for name in ("alchemy_rack.mdx", "BigBookshelfClean.mdx"):
        p = next(MODELS.rglob(name), None)
        if p is None:
            continue
        info = NE.inspect(load(p))
        record(f"{name} 无 ATCH 拒绝", not info["ok"], info["reason"][:44])

    print()
    bad = [n for n, ok, _d in cases if not ok]
    if bad:
        print(f"{len(bad)} 项失败：{bad}")
        return 1
    print(f"全部通过（{len(cases)} 项）    输出：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
