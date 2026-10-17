"""批量操作校验（Blender 侧，走真实算子）。

三件事：
  1 批量体检：11 个模型「解析 → 重建」全部逐字节一致
  2 批量改外部贴图路径：输出文件存在，且只有 TEXS 块变化、长度不变
  3 批量解绑动画：输出文件存在，BONE 块变短，其余块不动

全部输出到临时目录，不碰 test-models 原件。

用法：
    blender --background --python check_batch.py -- <目录>
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
src = Path(argv[0]).resolve() if argv else None
if not src:
    print("用法：blender --background --python check_batch.py -- <目录>")
    raise SystemExit(2)

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402

mdx_io.register()
core = get_core()

cases = []


def check(name, ok, detail=""):
    cases.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


tmp = Path(tempfile.mkdtemp(prefix="mdx_batch_"))
# 复制一份到临时目录，绝不改原件
work = tmp / "src"
shutil.copytree(src, work)
print(f"\n源目录 {work}（临时副本）")

# ---- 1 批量体检 ----
r = bpy.ops.mdx.batch_verify(folder=str(work), recursive=True)
report = json.loads(bpy.context.scene.get("mdx_batch_report") or "{}")
rows = report.get("rows") or []
ok_rows = [x for x in rows if x["status"] == "一致"]
check("批量体检执行", "FINISHED" in r, report.get("summary", "")[:60])
check("全部模型逐字节一致", len(ok_rows) == len(rows) and len(rows) >= 11,
      f"{len(ok_rows)}/{len(rows)}")

# ---- 2 批量改贴图路径 ----
out_tex = tmp / "out_tex"
r = bpy.ops.mdx.batch_textures(folder=str(work), out_dir=str(out_tex),
                              recursive=True, mode="PREFIX",
                              prefix="war3mapImported\\Batch", flatten=True)
report = json.loads(bpy.context.scene.get("mdx_batch_report") or "{}")
rows = report.get("rows") or []
done = [x for x in rows if x["status"] == "改好"]
check("批量改路径执行", "FINISHED" in r, report.get("summary", "")[:60])
check("有文件被改动", len(done) >= 6, f"{len(done)}/{len(rows)} 个文件")

# 逐个人工复核：长度不变 + 只有 TEXS 变
from mdxcore.chunks import texs as texsmod  # noqa: E402
bad = []
for row in done:
    rel = row["file"]
    src_file = next(work.rglob(rel))
    out_file = next(out_tex.rglob(rel))
    a, b = src_file.read_bytes(), out_file.read_bytes()
    if len(a) != len(b):
        bad.append((rel, "长度变了"))
        continue
    ma, mb = core.parse(a), core.parse(b)
    if [c.tag for c in ma.chunks] != [c.tag for c in mb.chunks]:
        bad.append((rel, "chunk 序列变了"))
        continue
    changed = [ca.tag for ca, cb in zip(ma.chunks, mb.chunks)
               if ma.data(ca) != mb.data(cb)]
    if changed != ["TEXS"]:
        bad.append((rel, f"变了 {changed}"))
        continue
    ta, tb = texsmod.parse(ma.data(ma.find("TEXS"))), texsmod.parse(mb.data(mb.find("TEXS")))
    ext = [t for t in ta if not t.replaceable_id and
           __import__("mdxcore.paths", fromlist=["x"]).classify(t.path) == "external"]
    if len(ta) != len(tb):
        bad.append((rel, "贴图条数变了"))
        continue
    for i, t in enumerate(tb):
        if t.path and not t.path.startswith("war3mapImported\\Batch\\"):
            if ta[i].path in [e.path for e in ext]:
                bad.append((rel, f"第 {i} 条没改成：{t.path}"))
                break
check("改动只落在 TEXS 且长度不变", not bad, f"{len(done)} 个文件，问题 {bad[:2]}")

# ---- 3 批量解绑 ----
out_un = tmp / "out_unbind"
r = bpy.ops.mdx.batch_unbind(folder=str(work), out_dir=str(out_un), recursive=True)
report = json.loads(bpy.context.scene.get("mdx_batch_report") or "{}")
rows = report.get("rows") or []
done = [x for x in rows if x["status"] == "解绑"]
check("批量解绑执行", "FINISHED" in r, report.get("summary", "")[:60])
check("有文件被解绑", len(done) >= 6, f"{len(done)}/{len(rows)}")

bad = []
for row in done:
    src_file = next(work.rglob(row["file"]))
    out_file = next(out_un.rglob(row["file"]))
    a, b = src_file.read_bytes(), out_file.read_bytes()
    ma, mb = core.parse(a), core.parse(b)
    changed = [ca.tag for ca, cb in zip(ma.chunks, mb.chunks)
               if ma.data(ca) != mb.data(cb)]
    if changed != ["BONE"]:
        bad.append((row["file"], changed))
    elif len(b) >= len(a):
        bad.append((row["file"], f"没有变短 {len(a)}→{len(b)}"))
check("解绑只动 BONE 且变短", not bad, f"问题 {bad[:2]}")

print("\n=== 汇总 ===")
fails = [n for n, ok, _d in cases if not ok]
print(f"  通过 {len(cases) - len(fails)}/{len(cases)}    临时目录 {tmp}")
for n in fails:
    print(f"  ! {n}")

mdx_io.unregister()
raise SystemExit(0 if not fails else 1)
