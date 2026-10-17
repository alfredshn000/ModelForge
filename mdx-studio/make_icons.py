"""从原始 logo 生成 exe 图标与页面图标。

原始图 `ModelForge_logo_no_background.png` 是「图形 + ModelForge 字样」的竖排构图，
而 Windows 图标在 16×16 下只有十几个像素，带文字必然糊成一团。所以这里按 alpha
扫描找出图形与文字之间的空白带，只取图形部分，再居中裁成正方形。

    python make_icons.py
"""

from __future__ import annotations

import pathlib

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "ModelForge_logo_no_background.png"
ASSETS = HERE / "assets"
WEB = HERE / "web"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def find_figure_band(alpha: Image.Image, gap: int = 8) -> tuple[int, int]:
    """返回 logo 中「图形」部分的纵向区间——即第一段被空白带隔开的连续内容。"""
    w, h = alpha.size
    px = alpha.load()
    runs: list[tuple[int, int]] = []
    start = None
    for y in range(h):
        filled = any(px[x, y] > 16 for x in range(0, w, 4))
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            runs.append((start, y))
            start = None
    if start is not None:
        runs.append((start, h))

    # 细小的缝隙（噪点、笔画间隙）不该被当成图形与文字的分界
    merged: list[tuple[int, int]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged[0] if merged else (0, h)


def square(img: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """按内容包围盒裁紧，再补成正方形（留一点内边距，避免贴边）。"""
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    side = max(img.size)
    pad = int(side * pad_ratio)
    canvas = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
    canvas.paste(img, ((canvas.width - img.width) // 2,
                       (canvas.height - img.height) // 2), img)
    return canvas


def main() -> int:
    if not SRC.is_file():
        print(f"找不到源图：{SRC}")
        return 1

    src = Image.open(SRC).convert("RGBA")
    top, bottom = find_figure_band(src.getchannel("A"))
    figure = src.crop((0, top, src.width, bottom))
    icon = square(figure)

    ASSETS.mkdir(exist_ok=True)
    WEB.mkdir(exist_ok=True)

    ico = ASSETS / "model-forge.ico"
    icon.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"exe 图标   {ico}  ({ico.stat().st_size // 1024} KB)")

    # 浏览器标签页图标走免 token 的 /favicon.ico，单独放一份
    favicon = WEB / "logo.ico"
    icon.resize((64, 64), Image.LANCZOS).save(
        favicon, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print(f"标签页图标 {favicon}  ({favicon.stat().st_size // 1024} KB)")

    for name, size in (("logo.png", 512), ("logo-128.png", 128)):
        out = WEB / name
        icon.resize((size, size), Image.LANCZOS).save(out, format="PNG", optimize=True)
        print(f"页面图标   {out}  ({out.stat().st_size // 1024} KB)")

    prev = HERE / "assets" / "icon-preview.png"
    icon.resize((256, 256), Image.LANCZOS).save(prev, format="PNG")
    print(f"预览       {prev}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
