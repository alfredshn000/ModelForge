"""直接从 WC3 Reforged 的 CASC 存储读取模型与贴图。

为什么先把文件落盘、而不是全在内存里走：
    - 导出器要拿「原始字节」做 delta 比对，必须能重复读到同一份源
    - Blender 的图片加载只认真实文件（DDS/TGA/PNG），不吃内存 buffer
    - 落盘位置固定在工作区里，不写到系统临时目录，符合"文件不出域"

目录结构：
    mdx-toolkit/work/casc/<模型名>/
        model.mdx                 —— 模型本体
        <贴图原始相对路径>          —— 材质实际引用到的贴图
"""

from __future__ import annotations

import re
from pathlib import Path

from .bridge import WORKSPACE, get_core


class CascSourceError(RuntimeError):
    """CASC 读取失败。"""


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", name).strip("_")
    return stem or "model"


def extract(install: Path, casc_path: str, out_root: Path | None = None) -> dict:
    """把 CASC 里的一个模型连同它引用的贴图导出到工作区。

    返回 {"model": 模型路径, "texture_root": 贴图根目录, "report": {...}}。
    """
    core = get_core()
    from mdxcore.casc import CascError, open_casc
    from mdxcore.ops import list_textures

    casc_path = casc_path.replace("/", "\\").strip("\\")
    if not casc_path:
        raise CascSourceError("CASC 路径为空")

    try:
        casc = open_casc(install)
    except CascError as exc:
        raise CascSourceError(str(exc)) from None

    try:
        raw = casc.read(casc_path)
    except CascError as exc:
        casc.close()
        raise CascSourceError(f"读不到 {casc_path}：{exc}") from None

    mdx = core.parse(raw)
    try:
        tex_items = list_textures(mdx)
    except Exception:                              # noqa: BLE001
        tex_items = []

    root = out_root or (WORKSPACE / "mdx-toolkit" / "work" / "casc")
    name = _safe_stem(Path(casc_path).stem)
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)

    model_path = folder / f"{name}.mdx"
    model_path.write_bytes(raw)

    saved = 0
    missing = 0
    skipped = 0
    for item in tex_items:
        if item.replaceable_id:
            skipped += 1
            continue
        try:
            real = casc.resolve(item.path)
        except CascError:
            real = None
        if real is None:
            missing += 1
            continue
        try:
            blob = casc.read(item.path)
        except CascError:
            missing += 1
            continue

        # 保留原始相对路径，顺便把扩展名改成实际文件的后缀（.blp → .dds）
        rel = item.path.replace("/", "\\")
        actual_suffix = Path(real).suffix
        if actual_suffix and not rel.lower().endswith(actual_suffix.lower()):
            rel = rel.rsplit(".", 1)[0] + actual_suffix
        target = folder / rel.lower()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        saved += 1

    casc.close()
    return {
        "model": model_path,
        "texture_root": folder,
        "report": {
            "casc_path": casc_path,
            "install": str(install),
            "size": len(raw),
            "textures_saved": saved,
            "textures_missing": missing,
            "textures_skipped": skipped,
        },
    }


def read_model(install: Path, casc_path: str) -> bytes:
    """只读出模型字节，不落盘。

    动画移植要的是"另一个模型的骨骼与轨道"，不关心它的贴图，
    所以没必要把 3GB 的资产目录结构复制一遍。
    """
    from mdxcore.casc import CascError, open_casc

    casc_path = casc_path.replace("/", "\\").strip("\\")
    if not casc_path:
        raise CascSourceError("CASC 路径为空")
    try:
        casc = open_casc(install)
    except CascError as exc:
        raise CascSourceError(str(exc)) from None
    try:
        return casc.read(casc_path)
    except CascError as exc:
        raise CascSourceError(f"读不到 {casc_path}：{exc}") from None
    finally:
        casc.close()


def list_models(install: Path, pattern: str = r"\.mdx$", limit: int = 500) -> list[str]:
    """列出安装里可用的模型路径。"""
    from mdxcore.casc import CascError, open_casc

    try:
        casc = open_casc(install)
    except CascError as exc:
        raise CascSourceError(str(exc)) from None
    try:
        return casc.find(pattern, limit=limit)
    finally:
        casc.close()


def search_installs() -> list[Path]:
    """列出可能的安装目录，供 UI 下拉。"""
    import string

    from mdxcore.casc import deepest_install

    found: list[Path] = []
    roots = [Path(f"{letter}:\\Games") for letter in string.ascii_uppercase]
    roots += [Path(f"{letter}:\\Program Files") for letter in ("C", "D")]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                hit = deepest_install(child, 3)
                if (hit / ".build.info").is_file() and hit not in found:
                    found.append(hit)
        except OSError:
            continue
    return found
