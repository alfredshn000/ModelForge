"""无界面往返校验：在 Blender 里跑一遍「导入 → 导出 → 字节比对」。

用法（Blender 4.x）：
    blender --background --python check_roundtrip.py -- "D:/path/to/model.mdx"

输出：
    - 控制台打印报告（JSON）
    - 写出文件到 <工作区>/mdx-toolkit/out/blender/<原名>.mdx
"""

import json
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
WORKSPACE = ADDON_DIR.parent

if "--" in sys.argv:
    argv = sys.argv[sys.argv.index("--") + 1:]
else:
    argv = []

if not argv:
    print("用法：blender --background --python check_roundtrip.py -- <model.mdx | 目录> [更多...]")
    raise SystemExit(2)

targets = []
for raw in argv:
    p = Path(raw).resolve()
    if p.is_dir():
        targets.extend(sorted(p.rglob("*.mdx")))
    elif p.is_file():
        targets.append(p)
    else:
        print(f"跳过（不存在）：{p}")

if not targets:
    print("没有找到任何 .mdx")
    raise SystemExit(2)

# 让 mdx_io 可导入
sys.path.insert(0, str(ADDON_DIR))

import bpy  # noqa: E402

import mdx_io  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402

mdx_io.register()

out_dir = WORKSPACE / "mdx-toolkit" / "out" / "blender"
out_dir.mkdir(parents=True, exist_ok=True)

summary = []
for model in targets:
    print(f"\n=== {model.name} ===")
    try:
        meta = import_mdx(str(model))
    except Exception as exc:                        # noqa: BLE001
        print(f"  导入失败：{exc}")
        summary.append((model.name, "导入失败", str(exc)))
        continue

    out_path = out_dir / model.name
    try:
        report, _ = export_mdx(str(out_path))
    except Exception as exc:                        # noqa: BLE001
        print(f"  导出失败：{exc}")
        summary.append((model.name, "导出失败", str(exc)))
        continue

    checks = report["checks"]
    mism = sum(c["vertex_mismatch"] for c in checks)
    dev = max((c["max_deviation"] for c in checks), default=0.0)
    uv_bad = sum(0 if c["uv_ok"] else 1 for c in checks)
    ok = report["byte_identical"] and report["geometry_unchanged"]

    print(f"  geoset={len(checks)}  顶点不符={mism}  最大偏差={dev:.6g}  UV异常={uv_bad}  "
          f"字节相同={report['byte_identical']}")
    for prob in report["problems"][:5]:
        print(f"    ! {prob}")

    summary.append((model.name, "通过" if ok else "未通过",
                    f"顶点不符={mism} 偏差={dev:.6g} UV异常={uv_bad}"))

print("\n\n=== 汇总 ===")
for name, status, detail in summary:
    print(f"  {status:<6} {name:<32} {detail}")
passed = sum(1 for _, s, _ in summary if s == "通过")
print(f"\n通过 {passed}/{len(summary)}")

mdx_io.unregister()
raise SystemExit(0 if passed == len(summary) else 1)
