"""单文件启动器：把运行时释放到固定目录，再启动真正的程序。

## 为什么不直接把主程序打成 onefile

实测过：PyInstaller `--onefile` 出来的 exe 进程能起、服务也正常，但 pywebview 的窗口
永远不出现——卡在 `webview.start()` 里，无异常无日志。同样代码打成 onedir 5 秒内正常出窗口。
原因是 onefile 每次运行都解压到新的临时目录，WebView2 / .NET 桥接按路径加载托管程序集时对不上。

## 这个启动器做的事

外层是一个很小的 onefile exe（不含 pywebview），内嵌 `runtime.zip`：

    1. 首次运行：把 runtime.zip 解压到 %LOCALAPPDATA%\\ModelForge\\runtime\\<版本>\\
    2. 之后运行：目录已存在就跳过解压
    3. 启动里面的 ModelForge.exe，把命令行参数原样传过去

用户看到的是一个 exe，双击即用；实际跑的仍是 onedir 结构，所以窗口正常。
升级时版本号变了，会解压到新目录，不影响正在用的旧版本。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

APP = "ModelForge"


def _frozen_dir() -> pathlib.Path:
    """onefile 运行时 PyInstaller 释放资源的临时目录。"""
    base = getattr(sys, "_MEIPASS", None)
    return pathlib.Path(base) if base else pathlib.Path(__file__).resolve().parent


def runtime_root() -> pathlib.Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "share"
    return root / APP / "runtime"


def version_tag() -> str:
    try:
        import brand
        return f"{brand.VERSION}"
    except Exception:                                  # noqa: BLE001
        return "0"


def bundle_hash() -> str:
    """内嵌运行时的内容摘要。

    **不能用版本号当缓存目录名**：代码改了但版本号没改时，程序会认为缓存有效，
    继续跑上一次解压出来的旧副本。用内容哈希，只要 runtime.zip 变了目录就变，
    永远不会拿到旧的。
    """
    bundle = _frozen_dir() / "runtime.zip"
    if not bundle.is_file():
        return "nobundle"
    h = hashlib.sha1()
    with bundle.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def cache_dir() -> pathlib.Path:
    return runtime_root() / f"{version_tag()}-{bundle_hash()}"


def extract(zip_path: pathlib.Path, dest: pathlib.Path) -> None:
    """解压到临时目录再改名，避免解压一半被中断留下残缺目录。"""
    tmp = dest.with_name(dest.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)


def main() -> int:
    root = cache_dir()
    exe = root / f"{APP}.exe"

    if not exe.is_file():
        bundle = _frozen_dir() / "runtime.zip"
        if not bundle.is_file():
            _fail(f"缺少运行时包：{bundle}")
            return 1
        try:
            extract(bundle, root)
        except Exception as exc:                       # noqa: BLE001
            _fail(f"释放运行时失败：{type(exc).__name__}: {exc}\n目标目录：{root}")
            return 1

    if not exe.is_file():
        _fail(f"运行时里没有 {APP}.exe：{exe}")
        return 1

    # 参数原样转交（打开某个 mdx、--probe 之类都要能用）
    args = [str(exe)] + sys.argv[1:]
    try:
        subprocess.Popen(args, cwd=str(root), close_fds=True)
    except Exception as exc:                           # noqa: BLE001
        _fail(f"启动失败：{type(exc).__name__}: {exc}")
        return 1
    return 0


def _fail(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, f"{APP} 启动器", 0x10)
    except Exception:                                  # noqa: BLE001
        print(msg, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
