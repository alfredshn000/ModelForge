"""批量导出多个模型到 models/<slug>/，每个目录内含 data.js / model.js / textures.js / tex/。

用法：
    python build_models.py                # 导出下面 MODELS 里的全部
    python build_models.py david          # 只导一个
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TEST = ROOT / "test-models"

sys.path.insert(0, str(ROOT / "mdx-toolkit"))

# (slug, 本地文件名, 游戏内路径)
# 第三个非空表示"本地没有，从游戏数据取出来"——只会读到工作目录，绝不写游戏文件。
MODELS = [
    ("david", "David.mdx", None),
    ("kultiranbarracks", "kultiranbarracks.mdx", None),
    ("fire", "LargeFireEmitter.mdx", None),
    ("halahk", "HeroHalahk.mdx", r"Units\Human\HeroHalahk\HeroHalahk.mdx"),
]

SEARCH = [TEST, ROOT / "ReteraModelStudio_0.04.7a9"]
GAME = pathlib.Path(os.environ.get("MDX_CASC_PATH", r"D:/Games/War3 2.0"))


def fetch_from_casc(casc_path: str, dest: pathlib.Path) -> pathlib.Path | None:
    """把游戏里的模型读出来写成副本。CASC 全程只读。"""
    if not GAME.exists():
        return None
    try:
        from mdxcore.casc import deepest_install, open_casc
        casc = open_casc(deepest_install(GAME))
        try:
            raw = casc.read(casc_path)
        finally:
            casc.close()
    except Exception as exc:                                  # noqa: BLE001
        print(f"    从游戏取模型失败：{exc}")
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    print(f"    已从游戏取出 → {dest.name}（{len(raw):,} B，游戏目录只读）")
    return dest


def run(script: str, model: pathlib.Path, out: pathlib.Path) -> None:
    r = subprocess.run(
        [sys.executable, str(HERE / script), str(model), str(out)],
        cwd=str(HERE), capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stdout[-800:])
        print(r.stderr[-1500:])
        raise SystemExit(f"{script} 失败")
    for line in r.stdout.strip().splitlines():
        print("   ", line)


def main() -> None:
    only = sys.argv[1:] or None
    for slug, filename, casc_path in MODELS:
        if only and slug not in only:
            continue
        out = HERE / "models" / slug
        out.mkdir(parents=True, exist_ok=True)

        found = []
        for base in SEARCH:
            found += list(base.rglob(filename))
            if found:
                break
        model = found[0] if found else None
        if model is None and casc_path:
            model = fetch_from_casc(casc_path, out / f"_source-{filename}")
        if model is None:
            print(f"跳过 {slug}：找不到 {filename}")
            continue
        print(f"\n=== {slug}  ({model.name}, {model.stat().st_size/1048576:.2f} MB)")
        t0 = time.time()
        for script in ("export_data.py", "export_geometry.py", "export_textures.py"):
            run(script, model, out)
        total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
        print(f"    合计 {total/1048576:.2f} MB，用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
