"""附着点新增 / 删除的端到端校验（Blender 侧，走真实算子）。

三条关键断言：
  1 新增后文件变长 376 字节（记录 364 + 轴心点 12），备份等于原件
  2 重新导入后场景里有 `ATCH_` 空物体，附着点列表多一条
  3 **再删掉，必须逐字节还原原文件**
  4 新增后立刻导出必须与刚写出的文件逐字节相同（场景与文件同步）

全部在临时目录里跑，不碰 test-models 原件。

用法：
    blender --background --python check_attach.py -- <model.mdx>
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    print("用法：blender --background --python check_attach.py -- <model.mdx>")
    raise SystemExit(2)

src = Path(argv[0]).resolve()

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io import state as statemod  # noqa: E402

mdx_io.register()

cases = []


def check(name, ok, detail=""):
    cases.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


tmp = Path(tempfile.mkdtemp(prefix="mdx_attach_"))
work = tmp / src.name
shutil.copyfile(src, work)
original = src.read_bytes()

print(f"\n=== {src.name} ===")
meta = mdx_io.import_mdx(str(work))
st = statemod.load()
before_count = len(st.get("attachments") or [])
print(f"  新增前：附着点 {before_count} 个，轴心点 {len(st.get('pivots') or [])} 个")

# ---- 新增 ----
r = bpy.ops.mdx.add_attachment(name="WeaponCheck Ref", parent="33",
                               path="war3mapImported\\Check.mdx",
                               from_local=False, use_cursor=False)
ok = "FINISHED" in r
check("新增算子执行", ok, "" if ok else str(r))

if ok:
    after = work.read_bytes()
    backup = work.with_suffix(work.suffix + ".bak")
    check("文件变长 376 字节", len(after) - len(original) == 376,
          f"{len(original)} → {len(after)} B")
    check("备份等于原件", backup.is_file() and backup.read_bytes() == original,
          backup.name)

    st2 = statemod.load()
    items = st2.get("attachments") or []
    check("场景里多一条附着点", len(items) == before_count + 1,
          f"{before_count} → {len(items)}")
    last = items[-1] if items else {}
    check("新点字段正确",
          last.get("name") == "WeaponCheck Ref" and last.get("path") == "war3mapImported\\Check.mdx",
          f"{last.get('name')} / {last.get('path')} / 编号 {last.get('object_id')}")

    empties = [o.name for o in bpy.data.objects if o.name.startswith("ATCH_")]
    hit = any("WeaponCheck" in n for n in empties)
    check("视口有了 ATCH_ 空物体", hit, f"{len(empties)} 个 ATCH_：{empties[-3:]}")

    # 立刻导出必须与文件一致
    out = tmp / "reexport.mdx"
    report, written = mdx_io.export_mdx(str(out))
    check("新增后导出与文件一致", Path(written).read_bytes() == after,
          report.get("mode"))

    # ---- 删除（刚加的那条是编号最大的，可删）----
    r = bpy.ops.mdx.remove_attachment(index=len(items) - 1)
    ok2 = "FINISHED" in r
    check("删除算子执行", ok2, "" if ok2 else str(r))
    if ok2:
        restored = work.read_bytes()
        check("加再删逐字节还原", restored == original,
              "字节完全相同" if restored == original else
              f"差 {sum(1 for a, b in zip(restored, original) if a != b)} 字节")
        st3 = statemod.load()
        check("附着点数量回到原值",
              len(st3.get("attachments") or []) == before_count,
              f"{len(st3.get('attachments') or [])}")

    # ---- 删中间已有的一条（现在支持任意位置）----
    if before_count > 1:
        try:
            r = bpy.ops.mdx.remove_attachment(index=1)
            ok3 = "FINISHED" in r
            st3 = statemod.load()
            check("删除中间附着点", ok3 and len(st3.get("attachments") or []) == before_count - 1,
                  f"剩 {len(st3.get('attachments') or [])} 条")
        except RuntimeError as exc:
            check("删除中间附着点", False, str(exc).split("Error: ")[-1][:60])

print("\n=== 汇总 ===")
bad = [n for n, ok, _d in cases if not ok]
print(f"  通过 {len(cases) - len(bad)}/{len(cases)}    临时目录 {tmp}")
for n in bad:
    print(f"  ! {n}")

mdx_io.unregister()
raise SystemExit(0 if not bad else 1)
