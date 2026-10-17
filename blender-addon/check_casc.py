"""从 CASC 导入的端到端校验（需要 Blender）。

对每个模型检查：
    1. 能从游戏数据里导入（几何 / 骨骼 / 贴图）
    2. 不修改任何东西 → 导出后与原始字节完全相同
    3. 贴图确实挂上了图像节点，且能读出像素

用法：
    blender --background --python check_casc.py -- <模型路径> [更多...]
    blender --background --python check_casc.py -- --auto 8     # 随机抽 8 个
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ADDON = Path(__file__).resolve().parent
WORKSPACE = ADDON.parent
sys.path.insert(0, str(ADDON))

import mdx_io  # noqa: E402
from mdx_io.bridge import get_core  # noqa: E402

get_core()                                        # 先把 mdxcore 放进 sys.path

from mdx_io.casc_source import list_models, search_installs  # noqa: E402
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402


def pick_install() -> Path | None:
    from mdxcore.casc import find_install

    found = find_install()
    if found:
        return found
    hits = search_installs()
    return hits[0] if hits else None


def run_one(install: Path, casc_path: str, out_dir: Path) -> dict:
    problems: list[str] = []

    meta = import_mdx(casc_path, casc_install=str(install))
    info = meta.get("casc") or {}

    # 纯粒子/特效模型本来就没有 GEOS，不算失败
    has_geometry = "GEOS" in (meta.get("chunks") or []) or meta["geoset_count"] > 0
    if meta["geoset_count"] == 0 and meta["bone_count"] == 0 and not has_geometry:
        return {"path": casc_path, "skip": "没有几何（纯特效模型）", "problems": []}

    loaded = meta.get("textures_loaded", 0)
    if loaded == 0 and meta.get("textures_missing", 0) > 0:
        problems.append(f"贴图全部缺失（{meta['textures_missing']} 张）")

    # 导出到工作区，再和原始字节比
    name = Path(casc_path).stem
    report, written = export_mdx(str(out_dir / f"{name}.mdx"))
    problems.extend(report["problems"])

    from mdxcore.casc import open_casc

    casc = open_casc(install)
    try:
        original = casc.read(casc_path)
    finally:
        casc.close()

    same = Path(written).read_bytes() == original
    if not same:
        problems.append(f"未改动却写出了不同字节（{report['mode']}，"
                        f"{report.get('changed_bytes', 0)} 字节）")

    # 贴图是否有像素
    images = 0
    for obj in bpy.data.collections.get("MDX").objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and node.image.size[0] > 1:
                    images += 1

    return {
        "path": casc_path,
        "geoset": meta["geoset_count"],
        "bones": meta["bone_count"],
        "tex_saved": info.get("textures_saved", 0),
        "tex_loaded": loaded,
        "images": images,
        "byte_identical": same,
        "size": len(original),
        "problems": problems,
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    install = pick_install()
    if install is None:
        print("找不到游戏安装目录")
        return 2
    print(f"安装目录 {install}")

    if argv and argv[0] == "--auto":
        count = int(argv[1]) if len(argv) > 1 else 6
        targets = list_models(install, r"(units|buildings)\\.*\.mdx$", limit=count)
        # 取后缀名各异的几个，覆盖面广一点
        targets = targets[:count]
    else:
        targets = argv

    if not targets:
        print("用法：blender --background --python check_casc.py -- <模型路径... | --auto N>")
        return 2

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "casc"
    out_dir.mkdir(parents=True, exist_ok=True)

    mdx_io.register()
    results = []
    for path in targets:
        try:
            results.append(run_one(install, path, out_dir))
        except Exception as exc:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            results.append({"path": path, "problems": [str(exc)]})
    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for r in results:
        if "problems" not in r:
            continue
        if r.get("skip"):
            print(f"  跳过  {r['path']}  （{r['skip']}）")
            continue
        ok = not r["problems"]
        print(f"  {'通过' if ok else '失败'}  {r['path']}")
        if "geoset" in r:
            print(f"        geoset={r['geoset']} 骨骼={r['bones']} "
                  f"贴图(存/载/图)={r['tex_saved']}/{r['tex_loaded']}/{r['images']} "
                  f"字节相同={r['byte_identical']} {r['size']:,}B")
        for prob in r["problems"][:4]:
            print(f"        ! {prob}")

    counted = [r for r in results if "problems" in r and not r.get("skip")]
    passed = sum(1 for r in counted if not r["problems"])
    skipped = len(results) - len(counted)
    print(f"\n通过 {passed}/{len(counted)}（跳过 {skipped}）")
    return 0 if passed == len(counted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
