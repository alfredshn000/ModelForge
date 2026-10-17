"""把模型引用的贴图解码成 PNG，并生成 textures.js（TEXS 索引 → 文件）。

匹配规则：按 TEXS 路径的 basename 在 test-models 下找同名 dds。
David 的 14 条外部贴图路径形如 `AF\\Units\\Human\\David\\David_Helm_Diffuse.dds`，
本地文件就叫 `David_Helm_Diffuse.dds`，因此 basename 能对上。
内置路径（`Units/Human/HeroHalahk/...tif`）在游戏数据里已经查不到，命中就是 0。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mdx-toolkit"))

from mdxcore import blp, dds, load                      # noqa: E402
from mdxcore.ops import list_materials, list_textures  # noqa: E402

MODEL = next((ROOT / "test-models").rglob("David.mdx"))  # 可用 argv 覆盖
OUT_DIR = pathlib.Path(__file__).resolve().parent / "tex"
OUT = pathlib.Path(__file__).resolve().parent / "textures.js"
MAX_SIZE = 256      # CASC 回退后贴图数量大增，256 是体积与清晰度的折中


def open_game():
    """打开游戏 CASC。找不到就返回 None，退回只用本地文件。"""
    path = os.environ.get("MDX_CASC_PATH", r"D:/Games/War3 2.0")
    root = pathlib.Path(path)
    if not root.exists():
        print(f"  未找到游戏目录 {root}，仅使用本地贴图")
        return None
    try:
        from mdxcore.casc import deepest_install, open_casc
        return open_casc(deepest_install(root))
    except Exception as ex:
        print(f"  CASC 打开失败：{ex}")
        return None


def read_casc(casc, tex_path: str):
    """按 TEXS 路径去游戏数据里读，扩展名按 .dds / .blp 回退。"""
    if casc is None:
        return None, None
    stem = re.sub(r"\.\w+$", "", tex_path)
    for cand in (tex_path, stem + ".dds", stem + ".blp"):
        try:
            raw = casc.read(cand.replace("/", "\\"))
        except Exception:
            continue
        if raw and len(raw) > 128:
            return raw, "casc"
    return None, None


def main() -> None:
    # 参数：argv[1]=模型路径  argv[2]=输出目录（可选）
    global MODEL, OUT, OUT_DIR
    if len(sys.argv) > 1:
        MODEL = pathlib.Path(sys.argv[1])
    if len(sys.argv) > 2:
        _d = pathlib.Path(sys.argv[2])
        _d.mkdir(parents=True, exist_ok=True)
        OUT = _d / OUT.name
        OUT_DIR = _d / 'tex'
    OUT_DIR.mkdir(exist_ok=True)
    for old in OUT_DIR.glob("*.png"):
        old.unlink()

    # 本地贴图索引：basename(小写) → 路径
    index = {}
    for pat in ("*.dds", "*.png"):
        for p in list((ROOT / "test-models").rglob(pat)) + \
                  list((ROOT / "ReteraModelStudio_0.04.7a9").rglob(pat)):
            if p.name.lower().startswith("btn"):
                continue
            index.setdefault(p.name.lower(), p)

    mdx = load(MODEL)
    has = lambda tag: any(c.tag == tag for c in mdx.chunks)
    textures = list_textures(mdx) if has("TEXS") else []

    # 哪些 TEXS 真的被材质引用（漫反射槽 / HD Diffuse 槽）
    skipped_blp: dict[str, int] = {}
    used = set()
    for mat in (list_materials(mdx) if has("MTLS") else []):
        for lay in mat.layers:
            if lay.texture_id >= 0:
                used.add(lay.texture_id)
            for slot in (lay.hd_slots or []):
                if slot.texture_id >= 0:
                    used.add(slot.texture_id)

    # 粒子发射器引用的贴图也算"被用到"
    if has("PRE2"):
        from mdxcore.chunks import pre2 as pre2mod
        try:
            for e in pre2mod.parse(mdx.data(mdx.find("PRE2"))):
                if e.texture_id >= 0:
                    used.add(e.texture_id)
        except Exception:
            pass

    casc = open_game()

    entries = {}
    total_bytes = 0
    t0 = time.time()
    hit = miss = 0

    for i, tex in enumerate(textures):
        if tex.replaceable_id:
            continue
        if i not in used:
            continue                      # 没被任何材质/粒子引用，不导出
        base = tex.path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        src = index.get(base)
        raw = None
        origin = "local"
        if src is not None:
            raw = src.read_bytes()
            name_src = src
        else:
            raw, origin = read_casc(casc, tex.path)
            if raw is None:
                miss += 1
                continue
            name_src = pathlib.Path(tex.path.replace("/", "\\").rsplit("\\", 1)[-1])

        # 三种容器：DDS（Reforged）、BLP1 调色板（经典层）、PNG
        w = h = 0
        rgba = None
        if raw[:4] == b"DDS ":
            src = pathlib.Path(str(name_src).split(".")[0] + ".dds")
            w, h, rgba = dds.decode(raw)
        elif raw[:4] == b"BLP1":
            src = pathlib.Path(str(name_src).split(".")[0] + ".blp")
            try:
                w, h, rgba = blp.decode(raw)
            except blp.BlpError as ex:
                miss += 1
                skipped_blp[str(ex)] = skipped_blp.get(str(ex), 0) + 1
                continue
        elif raw[:4] == b"BLP2":
            src = pathlib.Path(str(name_src).split(".")[0] + ".blp2")
            miss += 1
            skipped_blp["BLP2"] = skipped_blp.get("BLP2", 0) + 1
            continue
        elif raw[:4] == b"\x89PNG":
            src = pathlib.Path(name_src.name)
            png = raw
            nw = nh = 0
            avg = [128, 128, 128]
            name = f"{i}-{src.stem}.png"
            (OUT_DIR / name).write_bytes(png)
            total_bytes += len(png)
            hit += 1
            entries[i] = {"file": name, "w": 0, "h": 0, "bytes": len(png),
                          "avg": avg, "used": i in used,
                          "src": f"{src.name} ({origin})"}
            continue
        else:
            miss += 1
            skipped_blp[f"未知格式 {raw[:4]!r}"] = \
                skipped_blp.get(f"未知格式 {raw[:4]!r}", 0) + 1
            continue

        nw, nh, small = dds.downscale(w, h, rgba, MAX_SIZE)
        png = dds.encode_png(nw, nh, small)
        avg = list(dds.average_color(nw, nh, small))
        name = f"{i}-{src.stem}.png"
        (OUT_DIR / name).write_bytes(png)
        total_bytes += len(png)
        hit += 1
        entries[i] = {
            "file": name,
            "w": nw,
            "h": nh,
            "bytes": len(png),
            "avg": avg,
            "used": i in used,
            "src": f"{src.name} ({origin})",
        }

    payload = (
        "window.MDX_TEX = " +
        json.dumps({"entries": entries, "dir": "tex/", "max": MAX_SIZE},
                   ensure_ascii=False, separators=(",", ":")) + ";\n"
    )
    OUT.write_text(payload, encoding="utf-8")

    used_hit = sum(1 for k, v in entries.items() if v["used"])
    print(f"贴图 {len(textures)} 条 → 命中 {hit} / 缺失 {miss}")
    print(f"  其中被材质引用的：{used_hit} 张")
    if skipped_blp:
        print("  跳过：" + "、".join(f"{k}×{v}" for k, v in skipped_blp.items()))
    print(f"  textures.js 元数据 {len(payload)/1024:.1f} KB")
    print(f"  PNG 合计 {total_bytes/1048576:.2f} MB，最大边 {MAX_SIZE}")
    print(f"  用时 {time.time()-t0:.1f} s")


if __name__ == "__main__":
    main()
