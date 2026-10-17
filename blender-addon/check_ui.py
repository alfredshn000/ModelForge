"""界面层冒烟检查：注册、ID 唯一、枚举能取到数据、面板引用的属性都存在。

后台模式下没法真的画面板（没有窗口），但注册失败、算子 id 冲突、
枚举回调抛异常、面板引用了不存在的 Scene 属性——这些都会在打开 Blender
的瞬间炸在用户面前，必须在脚本里先撞一遍。

用法：
    blender --background --python check_ui.py -- <model.mdx>
"""

import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
target = Path(argv[0]).resolve() if argv else None

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io import state as statemod  # noqa: E402
from mdx_io import ui_edit  # noqa: E402

problems = []
mdx_io.register()

# ---- 1. 注册 ----
print("=== 注册 ===")
ids = []
for cls in ui_edit.CLASSES:
    rna = getattr(bpy.types, cls.__name__, None)
    if rna is None:
        problems.append(f"{cls.__name__} 未注册")
        continue
    ident = getattr(cls, "bl_idname", "") or "(面板，注册时按类名生成)"
    ids.append(ident)
    print(f"  {cls.__name__:<28} {ident}")

dupes = {i for i in ids if ids.count(i) > 1 and not i.startswith("(")}
if dupes:
    problems.append(f"算子 id 重复：{dupes}")

# ---- 2. Scene 属性 ----
print("\n=== Scene 属性 ===")
for attr in ("mdx_pivot_all", "mdx_texture_root", "mdx_casc_install", "mdx_casc_path"):
    ok = hasattr(bpy.types.Scene, attr)
    print(f"  {attr:<20} {'有' if ok else '缺'}")
    if not ok:
        problems.append(f"Scene 缺属性 {attr}")

# ---- 3. 面板都挂在主面板下 ----
print("\n=== 面板层级 ===")
panels = [c for c in ui_edit.CLASSES if c.__name__.startswith("MDX_PT_")]
for p in panels:
    parent = getattr(p, "bl_parent_id", "")
    ok = parent == "MDX_PT_panel" and hasattr(bpy.types, "MDX_PT_panel")
    print(f"  {p.__name__:<20} 父级={parent or '(无)'} {'ok' if ok else '!!'}")
    if not ok:
        problems.append(f"{p.__name__} 的父面板不存在")

# ---- 4. 枚举 ----
print("\n=== 枚举回调 ===")
if target and target.is_file():
    mdx_io.import_mdx(str(target))
    st = statemod.load()
    items = ui_edit._texture_enum(None, bpy.context)
    n_tex = len(st.get("textures") or [])
    print(f"  贴图枚举 {len(items)} 项（模型有 {n_tex} 条）")
    if not items:
        problems.append("贴图枚举为空")
    if n_tex and len(items) != n_tex:
        problems.append(f"贴图枚举 {len(items)} 项 ≠ 状态 {n_tex} 条")
    for ident, label, desc in items[:2]:
        print(f"    {ident}  {label[:60]}")
    c = statemod.counts(st)
    print(f"  counts: 贴图 {c['textures']}（外 {c['external']}）材质 {c['materials']} "
          f"层 {c['layers']} 序列 {c['sequences']} 几何动画 {c['geoa']}")
    if c["layers"] and statemod.layer_of(st, 0, 0) is None:
        problems.append("layer_of 取不到 0/0")
else:
    print("  未给模型，跳过")

# ---- 5. FilterMode 枚举与内核一致 ----
from mdxcore.chunks import mtls as mtlsmod  # noqa: E402

print(f"\n=== FilterMode ===")
print(f"  界面 {len(ui_edit.FILTER_ITEMS)} 项，内核 {len(mtlsmod.FILTER_MODES)} 项")
if len(ui_edit.FILTER_ITEMS) != len(mtlsmod.FILTER_MODES):
    problems.append("界面 FilterMode 与内核不一致")

mdx_io.unregister()
print("\n=== 结论 ===")
if problems:
    for p in problems:
        print(f"  ! {p}")
    raise SystemExit(1)
print("  全部通过")
raise SystemExit(0)
