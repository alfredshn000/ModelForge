"""产品信息。名字只在这里定义一次，其它地方一律引用。

改名字的时候只改这个文件（以及 assets/ 里的图标），不用担心漏掉某个角落。
"""

from __future__ import annotations

import os
import pathlib

APP_NAME = "ModelForge"
APP_ID = "ModelForge"                 # exe 名 / 目录名，不要有空格
VERSION = "0.1.0"
AUTHOR = "Alfred"
COPYRIGHT = f"Copyright (c) 2026 {AUTHOR}"
TAGLINE = "Warcraft III 模型编辑器"
DESCRIPTION = f"{APP_NAME} — {TAGLINE}"

# 窗口尺寸
WINDOW_SIZE = (1440, 900)
WINDOW_MIN = (1080, 680)


def user_dir() -> pathlib.Path:
    """配置与日志的落点：%LOCALAPPDATA%\\ModelForge（Windows），其它平台走家目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "share"
    return root / APP_NAME


def log_file() -> pathlib.Path:
    """启动日志。windowed 版没有控制台，出了问题只能靠它。"""
    d = user_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.log"


def cache_dir() -> pathlib.Path:
    """解码后的贴图缓存。同一个模型第二次打开就不用再解一遍 DXT 了。"""
    d = user_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d
