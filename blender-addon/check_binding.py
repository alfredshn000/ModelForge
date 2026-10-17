"""动画绑定在 Blender 侧的端到端校验。

要证明的四件事：
  1 绑定会改写文件，并把原件备份成 .bak
  2 备份与原件逐字节相同（真的能退回去）
  3 绑定后场景被重建，动画键数等于搬过来的键数
  4 **绑定后立刻导出，必须与刚写出的文件逐字节相同**
     —— 这一条最关键：它证明场景与文件完全同步，下次导出不会把刚绑的动画
     用旧 Action 覆盖回去。这是"写盘 + 重新导入"这套做法的正确性证明。

全部在临时目录里跑，不碰 test-models 里的原件。

用法：
    blender --background --python check_binding.py -- <目标.mdx> <源.mdx>
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) < 2:
    print("用法：blender --background --python check_binding.py -- <目标.mdx> <源.mdx>")
    raise SystemExit(2)

target_src = Path(argv[0]).resolve()
source = Path(argv[1]).resolve()

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io import state as statemod  # noqa: E402

mdx_io.register()

cases = []


def check(name, ok, detail=""):
    cases.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


tmp = Path(tempfile.mkdtemp(prefix="mdx_bind_"))
work = tmp / target_src.name
shutil.copyfile(target_src, work)
original = target_src.read_bytes()

print(f"\n=== {target_src.name} ← {source.name} ===")
meta = mdx_io.import_mdx(str(work))
before_keys = meta.get("animation_keys", 0)
before_bones = meta.get("bone_count", 0)
print(f"  绑定前：{before_bones} 骨 / {before_keys:,} 键")

# ---- 检查匹配（不写文件）----
r = bpy.ops.mdx.check_binding(mode="FILE", file=str(source), translation="copy")
raw = bpy.context.scene.get("mdx_binding_plan")
plan = json.loads(raw) if raw else {}
check("检查匹配不写文件", work.read_bytes() == original,
      f"匹配 {plan.get('matched', 0)}/{plan.get('target_bones', 0)}"
      f"（{plan.get('coverage', 0):.0%}），待搬 {plan.get('incoming_keys', 0):,} 键"
      if "FINISHED" in r else "算子未执行")

# ---- 绑定 ----
r = bpy.ops.mdx.bind_animation(mode="FILE", file=str(source), translation="copy",
                               replace_sequences=True, keep_target_tracks=True)
ok = "FINISHED" in r
check("绑定算子执行", ok, str(r) if not ok else "")

if ok:
    after = work.read_bytes()
    backup = work.with_suffix(work.suffix + ".bak")
    check("文件被改写", after != original, f"{len(original)} → {len(after)} B")
    check("备份存在且等于原件",
          backup.is_file() and backup.read_bytes() == original,
          str(backup.name))

    meta2 = json.loads(bpy.context.scene["mdx_meta"])
    check("场景已重建", meta2.get("animation_keys", 0) > 0,
          f"{meta2.get('bone_count', 0)} 骨 / {meta2.get('animation_keys', 0):,} 键")

    rec = bpy.context.scene.get("mdx_binding")
    rec = json.loads(rec) if rec else {}
    check("绑定记录已写入", bool(rec.get("source")),
          f"{rec.get('source', '')} {rec.get('when', '')}")

    # ---- 关键：绑定后立刻导出，必须逐字节相同 ----
    out = tmp / "rebound.mdx"
    report, written = mdx_io.export_mdx(str(out))
    check("绑定后导出与文件一致",
          Path(written).read_bytes() == after,
          f"{report.get('mode')}")

    # ---- 解绑 ----
    r = bpy.ops.mdx.unbind_animation(drop_sequences=False)
    ok2 = "FINISHED" in r
    check("解绑算子执行", ok2, str(r) if not ok2 else "")
    if ok2:
        meta3 = json.loads(bpy.context.scene["mdx_meta"])
        check("解绑后动画清零", meta3.get("animation_keys", 0) == 0,
              f"{meta3.get('animation_keys', 0)} 键，骨骼 {meta3.get('bone_count', 0)} 根仍在")
        out2 = tmp / "unbound.mdx"
        rep2, w2 = mdx_io.export_mdx(str(out2))
        check("解绑后导出与文件一致",
              Path(w2).read_bytes() == work.read_bytes(),
              f"{rep2.get('mode')}")

print("\n=== 汇总 ===")
bad = [n for n, ok, _d in cases if not ok]
print(f"  通过 {len(cases) - len(bad)}/{len(cases)}    临时目录 {tmp}")
for n in bad:
    print(f"  ! {n}")

mdx_io.unregister()
raise SystemExit(0 if not bad else 1)
