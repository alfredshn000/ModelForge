"""体验层校验：序列标记、贴图加载、导出报告。

这三件事都对「能不能用」有直接影响，但跟字节级正确性无关，所以单独一个脚本：

1. **序列标记**：SEQS 的每一段都要在时间轴上有一个 marker，帧号 = 毫秒/1000×fps
2. **贴图加载**：在临时目录里造几张同名（但扩展名不同）的 PNG，导入时要能认出来
   ——这同时验证了「按文件名去扩展名兜底」这条规则
3. **导出报告**：导出后报告要落到场景属性里，面板才画得出来

用法：
    blender --background --python check_scene.py -- <model.mdx | 目录> [更多...]
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
from mdx_io.exporter import export_mdx  # noqa: E402
from mdx_io.importer import import_mdx  # noqa: E402


def resolve_targets(argv):
    out = []
    for raw in argv:
        p = Path(raw).resolve()
        if p.is_dir():
            out.extend(sorted(p.rglob("*.mdx")))
        elif p.is_file():
            out.append(p)
    return out


def make_fixtures(root: Path, stems: list[str]) -> int:
    """给每个贴图 stem 造一张纯色 PNG（故意换扩展名，测试兜底匹配）。"""
    root.mkdir(parents=True, exist_ok=True)
    made = 0
    for i, stem in enumerate(stems):
        target = root / f"{stem}.png"
        if target.exists():
            made += 1
            continue
        image = bpy.data.images.new(f"fixture_{i}", 4, 4, alpha=True)
        color = [(1.0, 0.2, 0.2, 1.0), (0.2, 1.0, 0.2, 1.0), (0.2, 0.2, 1.0, 1.0)][i % 3]
        image.pixels = list(color) * 16
        image.filepath_raw = str(target)
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)
        made += 1
    return made


def run(model: Path, out_dir: Path, tex_root: Path) -> dict:
    core = get_core()
    from mdxcore.ops import list_sequences

    mdx = core.load(model)
    try:
        seqs = list_sequences(mdx)
    except Exception:                              # noqa: BLE001
        seqs = []

    # 按模型自己的贴图名造素材（去扩展名）
    tex_items = []
    try:
        from mdxcore.ops import list_textures
        tex_items = list_textures(mdx)
    except Exception:                              # noqa: BLE001
        pass
    stems = sorted({Path(t.path.replace("\\", "/")).stem for t in tex_items if t.path})
    make_fixtures(tex_root, stems)

    meta = import_mdx(str(model), texture_root=str(tex_root))
    fps = float(bpy.context.scene.render.fps or 24)
    problems: list[str] = []

    # ---- 1. 序列标记 ----
    markers = list(bpy.context.scene.timeline_markers)
    if len(markers) != len(seqs):
        problems.append(f"marker 数 {len(markers)} ≠ 序列数 {len(seqs)}")
    by_name = {m.name: m for m in markers}
    for s in seqs:
        marker = by_name.get(s.name)
        if marker is None:
            problems.append(f"缺少 marker：{s.name}")
            continue
        want = round(s.start / 1000.0 * fps)
        if abs(marker.frame - want) > 1:
            problems.append(f"{s.name} 帧号 {marker.frame}，应为 {want}")

    if len(meta.get("sequences") or []) != len(seqs):
        problems.append("meta.sequences 数量不对")

    # ---- 2. 贴图加载 ----
    coll = bpy.data.collections.get("MDX")
    loaded = 0
    with_image = 0
    if coll:
        for obj in coll.objects:
            if obj.type != "MESH":
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or not mat.get("mdx_texture_path"):
                    continue
                if mat.get("mdx_texture_file"):
                    loaded += 1
                node = next((n for n in mat.node_tree.nodes
                             if n.type == "TEX_IMAGE" and n.image), None)
                if node is not None:
                    with_image += 1
    if tex_items and loaded == 0:
        problems.append("一张贴图都没解析到")

    # ---- 3. 导出报告 ----
    report, written = export_mdx(str(out_dir / f"{model.stem}.scene.mdx"))
    raw = bpy.context.scene.get("mdx_last_report")
    if not raw:
        problems.append("导出后场景里没有 mdx_last_report")
    else:
        import json
        stored = json.loads(raw)
        if stored.get("mode") != report.get("mode"):
            problems.append("报告内容与返回值不一致")

    return {"model": model.name,
            "status": "通过" if not problems else "失败",
            "sequences": len(seqs), "markers": len(markers),
            "textures": f"{loaded}/{meta.get('geoset_count', 0)} 材质",
            "with_image_node": with_image,
            "mode": report.get("mode", ""),
            "problems": problems}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    targets = resolve_targets(argv)
    if not targets:
        print("用法：blender --background --python check_scene.py -- <model | 目录>")
        return 2

    out_dir = WORKSPACE / "mdx-toolkit" / "out" / "scene"
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_root = WORKSPACE / "mdx-toolkit" / "work" / "tex-fixture"

    mdx_io.register()
    results = []
    for model in targets:
        try:
            results.append(run(model, out_dir, tex_root))
        except Exception as exc:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            results.append({"model": model.name, "status": "异常", "problems": [str(exc)]})
    mdx_io.unregister()

    print("\n=== 汇总 ===")
    for r in results:
        print(f"  {r['status']:<4} {r['model']:<28} "
              f"序列 {r.get('sequences', '-')} 标记 {r.get('markers', '-')}  "
              f"贴图 {r.get('textures', '-')}  图像节点 {r.get('with_image_node', '-')}")
        for prob in r.get("problems", [])[:4]:
            print(f"       ! {prob}")

    passed = sum(1 for r in results if r["status"] == "通过")
    print(f"\n通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
