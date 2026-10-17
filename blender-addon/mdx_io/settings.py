"""记住上次用的目录。

为什么需要这个：Windows 注册表里**没有** WC3 Reforged 的安装路径
（`HKLM\\...\\Blizzard Entertainment\\Warcraft III` 只有文件关联，没有 InstallLocation），
所以没法自动定位，只能记住用户选过一次的结果。

存放位置：
    1. 工作区内的 mdx-toolkit/work/settings.json（首选，文件不出域）
    2. 工作区不可写时退到 Blender 的配置目录
"""

from __future__ import annotations

import json
from pathlib import Path

from .bridge import WORKSPACE

_FILENAME = "settings.json"
_CACHE: dict | None = None


def _path() -> Path:
    if (WORKSPACE / "mdx-toolkit").is_dir():
        return WORKSPACE / "mdx-toolkit" / "work" / _FILENAME
    try:
        import bpy

        base = Path(bpy.utils.user_resource("CONFIG")) / "mdx_toolkit"
    except Exception:                              # noqa: BLE001
        base = Path.home() / ".mdx_toolkit"
    return base / _FILENAME


def load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _path()
    if not path.is_file():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _CACHE = {}
    return _CACHE


def save(**values) -> None:
    data = dict(load())
    data.update({k: v for k, v in values.items() if v is not None})
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return
    global _CACHE
    _CACHE = data


def get(key: str, default=None):
    return load().get(key, default)


def remember_casc(install: str, model_path: str | None = None) -> None:
    save(casc_install=str(install), casc_path=model_path)


def remember_textures(root: str) -> None:
    save(texture_root=str(root))
