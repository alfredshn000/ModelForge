"""贴图链路验证：BLP1 解码 + CASC 路径查找。

跑法（需要本机装了 WC3 Reforged）：

    python tools/test_textures.py

覆盖三件容易出错的事：

1. **BLP1 调色板位置**：调色板固定跟在 156 字节头后面，不在 mip 内部。
   算错位置会让整张图变成彩色噪点——但尺寸和 alpha 统计都正常，
   光看数字发现不了，必须看颜色语义。
2. **BLP1 通道顺序**：调色板是 BGRA，读成 RGBA 会红蓝互换
   （霜之镰刀变红色、火焰之心变蓝色）。这里用语义断言锁住。
3. **CASC 路径查找**：Reforged 把人类/兽人/精灵资产放在
   `war3.w3mod\\_hd.w3mod`（war3.w3mod 内部再套一层），而且 TVFS 前缀树
   会折叠分隔符（`commandbuttonsbtnxxx.blp`）。两条都要处理，
   否则大量内置贴图会被判成"缺失"。

没装游戏时整段跳过，不算失败。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mdxcore import blp, dds                                  # noqa: E402
from mdxcore.casc import deepest_install, open_casc           # noqa: E402

GAME = pathlib.Path(r"D:/Games/War3 2.0")

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def avg(w: int, h: int, rgba: bytes) -> tuple[float, float, float]:
    n = w * h
    return tuple(sum(rgba[i * 4 + j] for i in range(n)) / n for j in range(3))


def main() -> None:
    if not GAME.exists():
        print(f"跳过：找不到游戏目录 {GAME}")
        return
    casc = open_casc(deepest_install(GAME))

    print("CASC 容器与路径查找：")
    hd = casc.list_all("war3.w3mod\\_hd.w3mod")
    check("HD 容器可枚举", len(hd) > 20000, f"{len(hd):,} 条")

    classics = casc.list_all("war3.w3mod")
    check("经典容器可枚举", len(classics) > 20000, f"{len(classics):,} 条")

    human = [p for p in hd if p.lower().startswith("units\\human")]
    check("人类资产在 HD 容器里", len(human) > 500, f"{len(human)} 条")

    # 标准路径（模型里就是这么写的）
    probes = [
        (r"Units\Human\HeroHalahk\Human_HalahkTheLifebringer_Main_Diffuse.tif", b"DDS "),
        (r"Units\Human\HeroHalahk\HeroHalahk.mdx", b"MDLX"),
        (r"ReplaceableTextures\CommandButtons\BTNAbominationsHook.blp", b"BLP1"),
        (r"ReplaceableTextures\Weather\Clouds8x8.blp", b"DDS "),
    ]
    for path, magic in probes:
        try:
            raw = casc.read(path)
            ok = raw[:4] == magic
            check(f"标准路径可读 {path.rsplit(chr(92), 1)[-1]}",
                  ok, f"{len(raw):,} B {raw[:4]!r}")
        except Exception as exc:                              # noqa: BLE001
            check(f"标准路径可读 {path.rsplit(chr(92), 1)[-1]}", False, str(exc))

    # .tif 回退到 .dds（Reforged 重制贴图换了容器）
    try:
        a = casc.read(r"Units\Human\HeroHalahk\Human_HalahkTheLifebringer_Main_Diffuse.tif")
        b = casc.read(r"Units\Human\HeroHalahk\Human_HalahkTheLifebringer_Main_Diffuse.dds")
        check(".tif 回退到同一份 .dds", a == b, f"{len(a):,} B")
    except Exception as exc:                                  # noqa: BLE001
        check(".tif 回退到同一份 .dds", False, str(exc))

    print("\nBLP1 解码：")
    blps = [p for p in classics if p.lower().endswith(".blp")]
    ok_n = jpeg_n = other_n = 0
    alpha_n = bad_alpha = 0
    for p in blps:
        raw = casc.read(p)
        try:
            w, h, rgba = blp.decode(raw)
            assert len(rgba) == w * h * 4
            ok_n += 1
            meta = blp.info(raw)
            if meta["alpha"]:
                alpha_n += 1
                # 结构断言：带 alpha 的 mip 必须能装下 indices + alpha 两份像素
                if meta["mip_size"] < w * h * 2:
                    bad_alpha += 1
        except blp.BlpError as exc:
            if str(exc) == "BLP1_JPEG":
                jpeg_n += 1
            else:
                other_n += 1
    check("调色板 BLP1 全部解码", ok_n >= 600, f"{ok_n} 个")
    check("JPEG 压缩明确区分而非静默出错", jpeg_n > 0, f"{jpeg_n} 个报 BLP1_JPEG")
    check("没有未知错误", other_n == 0, f"{other_n} 个")
    # 注意：实测这 299 张带 alpha 的 BLP 的 alpha 区段全是 0xFF（不透明）——
    # command button 图标就是方块，本来就没有透明。所以这里只断言结构尺寸，
    # 不断言"存在透明像素"，否则会误判成解码错误。
    check("带 alpha 的 mip 尺寸容纳两份像素", bad_alpha == 0,
          f"{alpha_n} 张带 alpha，{bad_alpha} 张尺寸不足")

    # 颜色语义：锁住通道顺序（BGRA 读成 RGBA 会把红蓝互换）
    print("\n通道顺序（语义断言）：")
    semantic = [
        ("BTNHeartOfTheFirebender", "r", "火焰之心应该偏红"),
        ("BTNScytheOfFrost", "b", "霜之镰刀应该偏蓝"),
        ("BTNBlueDragonFigurine", "b", "蓝龙雕像应该偏蓝"),
        ("BTNAbominationsHook", None, "憎恶的钩子：灰绿，不做通道断言"),
    ]
    for name, dominant, why in semantic:
        try:
            w, h, rgba = blp.decode(
                casc.read(rf"ReplaceableTextures\CommandButtons\{name}.blp"))
        except Exception as exc:                              # noqa: BLE001
            check(f"{name} 可解码", False, str(exc))
            continue
        r, g, b = avg(w, h, rgba)
        if dominant == "r":
            check(why, r > b, f"R={r:.0f} B={b:.0f}")
        elif dominant == "b":
            check(why, b > r, f"B={b:.0f} R={r:.0f}")
        else:
            check(why, True, f"R={r:.0f} G={g:.0f} B={b:.0f}")

    casc.close()
    print(f"\n结论：{passed} 项通过，{failed} 项失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
