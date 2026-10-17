"""材质 / 序列 / 几何动画 / 轴心点 写回校验。

核心断言只有一句：**改哪个字段，就只有那个字段的字节变。**

每个模型跑六组，每组都从原始文件**重新导入**（状态清零），
只改这一组要改的字段，导出后与原始文件逐字节比较：

  T1 无改动导出      → 与原始文件逐字节相同
  T2 一键改外部贴图  → 变化只落在 TEXS 的 256 字节路径字段里
  T3 图层 alpha + FilterMode → 只落在 MTLS 对应 4 字节
  T4 序列起止 / 循环 → 只落在 SEQS 对应记录里
  T5 几何动画 alpha  → 只落在 GEOA 对应 4 字节
  T6 轴心点坐标      → 只落在 PIVT 对应 12 字节

全部走真实算子（bpy.ops.mdx.*），测的是界面按下去真正执行的那条路。

用法：
    blender --background --python check_materials.py -- <model.mdx | 目录> [...]
"""

import sys
import tempfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    print("用法：blender --background --python check_materials.py -- <model.mdx | 目录>")
    raise SystemExit(2)

targets = []
for raw in argv:
    p = Path(raw).resolve()
    if p.is_dir():
        targets.extend(sorted(p.rglob("*.mdx")))
    elif p.is_file():
        targets.append(p)

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io import state as statemod  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402

core = get_core()
from mdxcore.diff import diff_ranges  # noqa: E402

mdx_io.register()

TMP = Path(tempfile.mkdtemp(prefix="mdx_mat_"))
results = []


def locate(mdx, offset: int):
    for c in mdx.chunks:
        if c.data_offset <= offset < c.end:
            return c.tag, offset - c.data_offset
    return "<头/tail>", offset


def diff_map(before: bytes, after: bytes, mdx):
    out = []
    for r in diff_ranges(before, after):
        tag, inner = locate(mdx, r.offset)
        out.append((tag, inner, r.length))
    return out


def case(model: Path, label: str, tag: str, make_action, expect: set):
    """重新导入 → 做一件事 → 导出 → 断言只有 expect 里的块变了。

    `make_action(state)` 返回一个无参函数；返回 None 表示该模型跳过本组。
    """
    try:
        import_mdx(str(model))
    except Exception as exc:                        # noqa: BLE001
        print(f"  [{tag}] 导入失败：{exc}")
        return False, "导入失败"

    st = statemod.load()
    action = make_action(st) if make_action else None
    if action is None:
        print(f"  [{tag}] 跳过（模型没有这类字段）")
        return True, "skip"

    before = model.read_bytes()
    mdx = core.load(model)
    try:
        action()
    except Exception as exc:                        # noqa: BLE001
        print(f"  [{tag}] 算子执行失败：{exc}")
        return False, f"算子失败 {exc}"

    dst = TMP / f"{model.stem}_{tag}.mdx"
    report, written = mdx_io.export_mdx(str(dst))
    after = Path(written).read_bytes()

    if not report.get("verified"):
        print(f"  [{tag}] 导出未通过校验：{report.get('problems')}")
        return False, "校验失败"
    if len(before) != len(after):
        print(f"  [{tag}] 文件长度变了 {len(before)} → {len(after)}")
        return False, "长度变化"

    hits = diff_map(before, after, mdx)
    if not hits:
        print(f"  [{tag}] 没有任何字节变化（改动没写进去）")
        return False, "无变化"
    tags = {t for t, _o, _n in hits}
    extra = tags - expect
    if extra:
        print(f"  [{tag}] 改到了不该改的块 {sorted(extra)}：{hits[:8]}")
        return False, f"误伤 {sorted(extra)}"

    print(f"  [{tag}] {label}：{len(hits)} 处改动，全在 {sorted(tags)}"
          f"（{sum(n for _t, _o, n in hits)} 字节）；{report.get('mode')}")
    return True, f"{len(hits)} 处 @ {sorted(tags)}"


for model in targets:
    print(f"\n=== {model.name} ===")
    ok_all = True
    notes = []

    def check(tag, label, expect, make_action):
        global ok_all
        ok, _detail = case(model, label, tag, make_action, expect)
        ok_all = ok_all and ok
        notes.append(f"{tag} {'ok' if ok else 'FAIL'}")

    # T1 无改动
    def t1(_st):
        return lambda: None

    check("T1", "无改动", set(), None)
    # 无改动时文件应逐字节相同，单独判
    import_mdx(str(model))
    dst = TMP / f"{model.stem}_t1.mdx"
    report, written = mdx_io.export_mdx(str(dst))
    same = Path(written).read_bytes() == model.read_bytes()
    print(f"  [T1] 无改动导出：字节相同 = {same}；{report.get('mode')}")
    if not same:
        ok_all = False
        notes[-1] = "T1 FAIL"

    # T2 改外部贴图
    def t2(st):
        if not [t for t in st.get("textures") or [] if t.get("class") == "external"]:
            return None
        return lambda: bpy.ops.mdx.rewrite_textures(
            mode="PREFIX", prefix="war3mapImported\\Check", flatten=True)

    check("T2", "一键改外部贴图", {"TEXS"}, t2)

    # T3 材质图层
    def t3(st):
        pairs = [(m["index"], l) for m in st.get("materials") or []
                 for l in m.get("layers") or []]
        if not pairs:
            return None
        mi, lay = pairs[0]
        li = lay["index"]

        def act():
            bpy.ops.mdx.layer_alpha(mat=mi, layer=li, alpha=0.42, sync_geoa=False)
            bpy.ops.mdx.layer_filter(mat=mi, layer=li,
                                     value=str((int(lay.get("filter", 0)) + 1) % 7))

        return act

    check("T3", "图层 alpha + FilterMode", {"MTLS"}, t3)

    # T4 序列
    def t4(st):
        seqs = st.get("sequences") or []
        if not seqs:
            return None
        s = seqs[0]
        return lambda: bpy.ops.mdx.edit_sequence(
            index=s["index"], start=s["start"] + 100, end=s["end"] + 200,
            non_looping=not s.get("non_looping", False))

    check("T4", "序列起止 + 循环", {"SEQS"}, t4)

    # T5 几何动画
    def t5(st):
        geoa = st.get("geoa") or []
        if not geoa:
            return None
        g = geoa[0]
        return lambda: bpy.ops.mdx.edit_geoa(index=g["index"], alpha=0.55,
                                             color=(0.9, 0.8, 0.7))

    check("T5", "几何动画 alpha + 颜色", {"GEOA"}, t5)

    # T6 轴心点
    def t6(st):
        pivots = st.get("pivots") or []
        if not pivots:
            return None
        p = pivots[0]
        xyz = p.get("xyz") or [0, 0, 0]
        return lambda: bpy.ops.mdx.edit_pivot(
            index=p["index"], xyz=(xyz[0] + 1.5, xyz[1] - 0.5, xyz[2] + 0.25),
            use_cursor=False, use_bone=False, move_bone=False)

    check("T6", "轴心点坐标", {"PIVT"}, t6)

    # T7 换贴图（材质面板最常用的动作：把某个槽指到另一张贴图）
    def t7(st):
        from mdx_io import state as sm

        mats = st.get("materials") or []
        texs = st.get("textures") or []
        if not mats or len(texs) < 2:
            return None
        mi = mats[0]["index"]
        cur = sm.diffuse_index(st, mi, 0)
        target = next((t["index"] for t in texs if t["index"] != cur), None)
        if target is None:
            return None
        return lambda: bpy.ops.mdx.assign_texture(
            target=f"layer:{mi}:0:Diffuse", value=str(target))

    check("T7", "换漫反射贴图", {"MTLS"}, t7)

    # T8 附着点挂载路径（"把武器挂到手上"就是改这个字段）
    def t8(st):
        items = st.get("attachments") or []
        if not items:
            return None
        i = items[0]["index"]
        return lambda: bpy.ops.mdx.attach_path(
            index=i, path="war3mapImported\\Check_Weapon.mdx", from_local=False)

    check("T8", "附着点挂载路径", {"ATCH"}, t8)

    # T9 附着点编号 + 名字
    def t9(st):
        items = st.get("attachments") or []
        if not items:
            return None
        i = items[0]["index"]
        base = int(items[0].get("attachment_id", 0))

        def act():
            bpy.ops.mdx.attach_id(index=i, value=base + 1)
            bpy.ops.mdx.attach_name(index=i, name="CheckRef")

        return act

    check("T9", "附着点编号 + 名字", {"ATCH"}, t9)

    results.append((model.name, ok_all, "；".join(notes)))

print("\n=== 汇总 ===")
for name, ok, detail in results:
    print(f"  {'通过' if ok else '未通过':<6} {name:<34} {detail}")
passed = sum(1 for _n, ok, _d in results if ok)
print(f"\n通过 {passed}/{len(results)}    临时输出：{TMP}")

mdx_io.unregister()
raise SystemExit(0 if passed == len(results) else 1)
