"""验证贴图路径分类与批量改写：全部结论必须来自真实模型的字节，不来自推测。

跑法：
    python tools/test_paths.py

四组检查：
  A 分类   —— 11 个真实模型的 367 条贴图，逐条判定并与人工核对过的样本比对
  B 前缀   —— 只改外部贴图，内置一条不许动；check_delta 必须通过、长度必须不变
  C 替换   —— 字面子串替换，改完重新解析读回
  D 幂等   —— 同一条规则跑第二遍不应产生任何改动
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mdxcore import load, rebuild                      # noqa: E402
from mdxcore import paths as P                          # noqa: E402
from mdxcore.container import parse                     # noqa: E402
from mdxcore.ops import format_version, list_textures   # noqa: E402
from mdxcore.verify import check_delta                  # noqa: E402
from mdxcore.writers import ChunkEditor, pack_path_field  # noqa: E402

MODELS = HERE.parent.parent / "test-models"

# 人工核对过的样本：(模型, 路径片段, 期望分类)
KNOWN = [
    ("kultiranbarracksdeath.mdx", "kultiran_Barracks_Diffuse.dds", P.EXTERNAL),
    ("kultiranbarracksdeath.mdx", "kultiran_flag_ORM.dds", P.EXTERNAL),
    ("David.mdx", "AF\\Units\\Human\\David\\David_Helm_Diffuse.dds", P.EXTERNAL),
    # 同一个模型里既有别人的 AF\ 包、又有游戏内置的 Halahk 本体贴图
    ("David.mdx", "Units/Human/HeroHalahk/Human_HalahkTheLifebringer_Main_Diffuse.tif", P.BUILTIN),
    ("BotanistGarden.mdx", "Textures/Black32.blp", P.BUILTIN),
    ("BotanistGarden.mdx", "ReplaceableTextures/EnvironmentMap.blp", P.BUILTIN),
]


def collect():
    found = []
    for p in sorted(MODELS.rglob("*.mdx")):
        try:
            mdx = load(p)
            found.append((p, mdx, list_textures(mdx)))
        except Exception as exc:                        # noqa: BLE001
            print(f"  跳过 {p.name}: {exc}")
    return found


def check_classify(found) -> int:
    print("\n[A] 分类")
    bad = 0
    counts = {P.BUILTIN: 0, P.EXTERNAL: 0, P.REPLACEABLE: 0, P.EMPTY: 0}
    total = 0
    for _p, _mdx, texs in found:
        for t in texs:
            counts[P.classify(t.path, t.replaceable_id)] += 1
            total += 1
    print(f"  全部模型 {len(found)} 个 / 贴图 {total} 条："
          f"内置 {counts[P.BUILTIN]}  外部 {counts[P.EXTERNAL]}  "
          f"可替换 {counts[P.REPLACEABLE]}  空 {counts[P.EMPTY]}")

    for name, needle, expect in KNOWN:
        hit = None
        for p, _mdx, texs in found:
            if p.name != name:
                continue
            for t in texs:
                if P.norm(t.path).endswith(P.norm(needle)) or P.norm(t.path) == P.norm(needle):
                    hit = t
                    break
            if hit:
                break
        if hit is None:
            print(f"  ! 找不到样本 {name} :: {needle}")
            bad += 1
            continue
        got = P.classify(hit.path, hit.replaceable_id)
        ok = got == expect
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else '!! '} {got:11s} {hit.path}   ({P.reason(hit.path, hit.replaceable_id)})")
    return bad


def write_paths(src: Path, mapping: dict[int, str], dst: Path) -> tuple[bool, list[str], int]:
    """按 mapping 改 TEXS 路径，走 check_delta 断言后写出。返回 (通过, 问题, 改动字节)。"""
    mdx = load(src)
    before = src.read_bytes()
    ed = ChunkEditor(mdx)
    for i, new in mapping.items():
        ed.write_raw("TEXS", i * 268 + 4, pack_path_field(new, 256))
    after = rebuild(mdx, ed.apply())
    problems = check_delta(before, after, mdx, ed.abs_spans())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(after)
    return not problems, problems, ed.touched_bytes()


def check_prefix(found) -> int:
    print("\n[B] 前缀改写（只改外部）")
    bad = 0
    src = next((p for p, _m, _t in found if p.name == "kultiranbarracksdeath.mdx"), None)
    if src is None:
        print("  ! 缺少样本 kultiranbarracksdeath.mdx")
        return 1

    mdx = load(src)
    texs = list_textures(mdx)
    rule = P.Rule(mode="prefix", prefix="war3mapImported\\MyPack", flatten=True)
    mapping = P.apply_rule(texs, rule)
    print(f"  {src.name}: {len(texs)} 条贴图 → 命中 {len(mapping)} 条")
    for i, new in mapping.items():
        print(f"    [{i}] {texs[i].path or '(空)'}  →  {new}")

    ok, problems, nbytes = write_paths(src, mapping, HERE.parent / "out" / "paths_prefix.mdx")
    after_texs = list_textures(load(HERE.parent / "out" / "paths_prefix.mdx"))
    back_ok = all(after_texs[i].path == new for i, new in mapping.items())
    untouched = all(after_texs[i].path == t.path
                    for i, t in enumerate(texs) if i not in mapping)
    print(f"  校验 check_delta: {'通过' if ok else '失败 ' + str(problems)}")
    print(f"  改动 {nbytes} 字节；回读路径正确：{back_ok}；未命中项原样：{untouched}")
    bad += 0 if (ok and back_ok and untouched and len(mapping) == 6) else 1
    return bad


def check_replace(found) -> int:
    print("\n[C] 字面替换（AF\\ → MyMod\\）")
    bad = 0
    src = next((p for p, _m, _t in found if p.name == "David.mdx"), None)
    if src is None:
        print("  ! 缺少样本 David.mdx")
        return 1

    mdx = load(src)
    texs = list_textures(mdx)
    rule = P.Rule(mode="replace", find="AF\\", repl="MyMod\\")
    mapping = P.apply_rule(texs, rule)
    builtin_hit = [i for i in mapping if P.classify(texs[i].path, texs[i].replaceable_id) == P.BUILTIN]
    print(f"  {src.name}: {len(texs)} 条 → 命中 {len(mapping)} 条；误伤内置 {len(builtin_hit)} 条")
    if mapping:
        i = next(iter(mapping))
        print(f"    例：[{i}] {texs[i].path} → {mapping[i]}")

    ok, problems, nbytes = write_paths(src, mapping, HERE.parent / "out" / "paths_replace.mdx")
    after = list_textures(load(HERE.parent / "out" / "paths_replace.mdx"))
    back_ok = all(after[i].path == new for i, new in mapping.items())
    no_af = not any(P.norm(t.path).upper().startswith("AF\\")
                    for t in after if P.classify(t.path, t.replaceable_id) == P.EXTERNAL)
    print(f"  校验 check_delta: {'通过' if ok else '失败 ' + str(problems)}")
    print(f"  改动 {nbytes} 字节；回读正确：{back_ok}；残留 AF\\：{not no_af}")
    bad += 0 if (ok and back_ok and no_af and not builtin_hit) else 1
    return bad


def check_idempotent(found) -> int:
    print("\n[D] 幂等（同规则第二遍不应再改）")
    src = next((p for p, _m, _t in found if p.name == "kultiranbarracksdeath.mdx"), None)
    rule = P.Rule(mode="prefix", prefix="war3mapImported\\MyPack", flatten=True)
    once = P.apply_rule(list_textures(load(src)), rule)
    twice = P.apply_rule(list_textures(load(HERE.parent / "out" / "paths_prefix.mdx")), rule)
    print(f"  第一遍改 {len(once)} 条，第二遍改 {len(twice)} 条")
    ok = len(once) == 6 and len(twice) == 0
    print(f"  {'通过' if ok else '失败'}")
    return 0 if ok else 1


def check_separators() -> int:
    """分隔符：游戏两种都认，且作者写法不一（实测 357 条里正斜杠 271、反斜杠 80、混用 6）。

    要保证两件事：
    1. 分类与分隔符无关——否则"一键改外部路径"会把游戏内置贴图当外部改掉
    2. 改写保留原风格——只改前缀，不该把整条路径的每个分隔符都换掉
    """
    print("\n[E] 路径分隔符")
    bad = 0
    rule = P.Rule(mode="prefix", prefix="war3mapImported", flatten=True)

    cases = [
        ("Units\\Human\\A.tif", P.BUILTIN),
        ("Units/Human/A.tif", P.BUILTIN),
        ("Units\\Human/A.tif", P.BUILTIN),
        ("AF/Units/Human/A.dds", P.EXTERNAL),
        ("AF\\Units/Human\\A.dds", P.EXTERNAL),
        ("onlyfile.dds", P.EXTERNAL),
    ]
    for path, expect in cases:
        got = P.classify(path)
        ok = got == expect
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else 'BAD'} 分类 {got:9s} {path}")

    rewrites = [
        ("Units/Human/A.tif", "war3mapImported/A.tif", "纯正斜杠保持正斜杠"),
        ("Units\\Human\\B.tif", "war3mapImported\\B.tif", "纯反斜杠保持反斜杠"),
        ("Units\\Human/C.tif", "war3mapImported\\C.tif", "混用统一成反斜杠"),
    ]
    for src, expect, why in rewrites:
        got = P.rewrite_one(src, rule)
        ok = got == expect
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else 'BAD'} {why}: {src} → {got}")

    # 字面替换也要保风格
    r2 = P.Rule(mode="replace", find="AF", repl="MyMod")
    got = P.rewrite_one("AF/Units/X.dds", r2)
    ok = got == "MyMod/Units/X.dds"
    bad += 0 if ok else 1
    print(f"  {'OK ' if ok else 'BAD'} 替换保留正斜杠: {got}")
    return bad


def main() -> int:
    if not MODELS.is_dir():
        print(f"找不到测试模型目录：{MODELS}")
        return 2
    found = collect()
    print(f"已加载 {len(found)} 个模型")

    bad = 0
    bad += check_classify(found)
    bad += check_prefix(found)
    bad += check_replace(found)
    bad += check_idempotent(found)
    bad += check_separators()

    print("\n" + ("全部通过" if bad == 0 else f"有 {bad} 项未通过"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
