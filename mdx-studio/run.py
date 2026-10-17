"""启动入口。

    python run.py                     # 自动挑端口，开窗口（没装 pywebview 则用浏览器）
    python run.py --port 8765         # 固定端口
    python run.py E:/models/hero.mdx  # 启动后直接打开某个模型
    python run.py --headless          # 只起服务，不开界面（给 MCP / 测试用）
    python run.py --probe             # 只打印环境，排查"打不开"用

打包后这个文件就是 exe 的入口，pywebview 窗口挂在同一个进程里。

## 前端的两种外壳

pywebview 窗口和系统浏览器加载的是**同一份页面**（http://127.0.0.1:端口/），
区别只是有没有地址栏。所以界面代码跟打不打包无关，只跟 web/ 目录放在哪有关：

    exe 同目录/web/   → 外置，改完刷新即生效（开发时用）
    内嵌进 exe        → 改一个字都要重打包（发给别人时用）

见 server.resolve_web_dir。

## 崩了去哪看

窗口版没有控制台，出错不会显示任何东西——只会"闪一下就没了"。所以所有未捕获
异常都会写进 %LOCALAPPDATA%\\ModelForge\\logs\\app.log，并弹一个提示框指出日志位置。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time
import traceback
import webbrowser

HERE = pathlib.Path(__file__).resolve().parent

# 源码运行时内核在旁边；打包后内核已经进了 exe，这段路径不存在也无所谓
_KIT = HERE.parent / "mdx-toolkit"
if _KIT.is_dir():
    sys.path.insert(0, str(_KIT))
sys.path.insert(0, str(HERE))                     # 本目录模块

import brand  # noqa: E402
import server  # noqa: E402


# --------------------------------------------------------------------- 日志

def _note(text: str) -> None:
    """写一行日志。失败也不能影响启动。"""
    try:
        with brand.log_file().open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:                                  # noqa: BLE001
        pass


def _setup_debug_log() -> None:
    """MDX_VERBOSE=1 时把三方库的日志也写进 debug.log。

    pywebview 卡住/失败时它自己的报错只走 logging，不会抛到 Python 层，
    所以排查窗口问题必须先打开这个。
    """
    if not os.environ.get("MDX_VERBOSE"):
        return
    import logging

    path = brand.log_file().with_name("debug.log")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    _note(f"详细日志已开启 → {path}")


def _install_crash_handler() -> None:
    """未捕获异常 → 日志 + 提示框。没有这一步，窗口版的崩溃是完全无声的。"""
    def handle(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _note("启动失败：\n" + text)
        _alert(f"{brand.APP_NAME} 启动失败：\n\n{exc_type.__name__}: {exc}\n\n"
               f"日志：{brand.log_file()}")

    sys.excepthook = handle
    threading.excepthook = lambda args: handle(                   # type: ignore[assignment]
        args.exc_type, args.exc_value, args.exc_traceback)


def _alert(msg: str) -> None:
    """弹个原生提示框。用户双击 exe 时看不到任何控制台输出，只能靠这个。"""
    if os.environ.get("MDX_NO_DIALOG"):        # 自动化测试时别卡在弹窗上
        print(msg, file=sys.stderr)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, brand.APP_NAME, 0x10)
    except Exception:                                  # noqa: BLE001
        print(msg, file=sys.stderr)


# --------------------------------------------------------------------- 探测

def _ensure_streams() -> None:
    """windowed 版没有控制台，sys.stdout / sys.stderr 是 None。

    这时候任何一次 print() 都会抛 AttributeError: 'NoneType' object has no attribute 'write'，
    而窗口版崩了是无声的——表现就是双击后闪一下就没了。所以先兜住。
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")   # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = sys.stdout


def _probe() -> int:
    """打印运行环境。打不开时先跑这个，能立刻区分是缺运行时还是缺文件。

    窗口版看不到控制台输出，所以同时写一份进日志。
    """
    import contextlib
    import io
    import platform

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _probe_body(platform)
    text = buf.getvalue()
    print(text, end="")
    _note("环境探测：\n" + text)
    return code


def _probe_body(platform) -> int:
    print(f"产品        : {brand.APP_NAME} {brand.VERSION}  ({brand.AUTHOR})")
    print(f"frozen      : {server._frozen()}")
    print(f"executable  : {sys.executable}")
    print(f"python      : {platform.python_version()}")
    print(f"web 目录    : {server.resolve_web_dir()}")
    print(f"日志        : {brand.log_file()}")
    try:
        import webview                                   # type: ignore
        ver = getattr(webview, "__version__", "")
        print(f"pywebview   : {'ok ' + ver if ver else 'ok'}")
    except Exception as exc:                              # noqa: BLE001
        print(f"pywebview   : 不可用（{type(exc).__name__}: {exc}）→ 会退回系统浏览器")
        return 1
    for name in ("clr", "clr_loader", "webview.platforms.edgechromium"):
        try:
            __import__(name)
            print(f"{name:<12}: ok")
        except Exception as exc:                          # noqa: BLE001
            print(f"{name:<12}: 缺（{type(exc).__name__}）")

    if sys.platform == "win32":
        # 窗口本体是系统里的 WebView2，缺了 pywebview 会直接起不来
        key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
               r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                print(f"WebView2    : {winreg.QueryValueEx(k, 'pv')[0]}")
        except OSError:
            print("WebView2    : 未安装 → 装 Edge WebView2 Runtime，或退回系统浏览器")
    return 0


# --------------------------------------------------------------------- 主流程

def main() -> int:
    _ensure_streams()
    _setup_debug_log()

    # Windows 控制台默认用 GBK 输出，中文路径会变成乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description=f"{brand.APP_NAME} 本地服务")
    ap.add_argument("path", nargs="?", help="启动后直接打开的 mdx")
    ap.add_argument("--port", type=int, default=0, help="0 = 自动挑空闲端口")
    ap.add_argument("--game", default=None, help="WC3 安装目录（读内置贴图用）")
    ap.add_argument("--web-dir", default=None,
                    help="指定前端目录（不填则按 exe 同目录 → 内置 的顺序找）")
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    ap.add_argument("--headless", action="store_true", help="只起服务，不开窗口")
    ap.add_argument("--debug", action="store_true", help="窗口里开 DevTools（F12）")
    ap.add_argument("--probe", action="store_true",
                    help="只打印运行环境（窗口后端是否可用），不开窗口")
    ap.add_argument("--version", action="store_true", help="打印版本后退出")
    args = ap.parse_args()

    if args.version:
        print(f"{brand.APP_NAME} {brand.VERSION} — {brand.TAGLINE}")
        print(f"{brand.COPYRIGHT}")
        return 0

    if args.probe:
        return _probe()

    srv, url = server.serve(port=args.port, casc_path=args.game, web_dir=args.web_dir)
    port = srv.server_address[1]

    _note(f"{brand.APP_NAME} {brand.VERSION} 启动 | frozen={server._frozen()} "
          f"| exe={sys.executable} | web={srv.web_dir} | game={srv.app.casc_path}")

    if args.path:
        try:
            srv.app.open(args.path)
            _note(f"已打开 {args.path}")
            print(f"已打开 {args.path}")
        except Exception as exc:                       # noqa: BLE001
            _note(f"打开失败：{exc}")
            print(f"打开失败：{exc}")

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"服务已启动： http://127.0.0.1:{port}/")
    print(f"界面地址：   {url}")
    print(f"前端目录：   {srv.web_dir}")
    print(f"日志文件：   {brand.log_file()}")

    if args.headless:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        srv.shutdown()
        return 0

    win = _try_pywebview(url, debug=args.debug)
    if win is None:
        if not args.no_browser:
            webbrowser.open(url)
        print("（没装 pywebview，用系统浏览器打开；按 Ctrl+C 退出）")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    srv.shutdown()
    _note("正常退出")
    return 0


class _Bridge:
    """暴露给页面的原生能力。

    浏览器里 `window.pywebview.api` 就是这个对象的方法。前端拿到它就用系统自带的
    文件对话框，不再用自制的"盘符列表"——后者既难看又难用，还会暴露内部路径写法。
    """

    def pick_model(self) -> str:
        """弹系统文件对话框选一个 mdx，返回绝对路径；取消返回空串。"""
        import webview
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return ""
        try:
            picked = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Warcraft III 模型 (*.mdx)", "所有文件 (*.*)"),
            )
        except Exception as exc:                        # noqa: BLE001
            _note(f"文件对话框打开失败：{type(exc).__name__}: {exc}")
            return ""
        if not picked:
            return ""
        return picked[0] if isinstance(picked, (list, tuple)) else str(picked)

    def pick_texture(self) -> str:
        """选一张贴图，返回路径。给贴图替换用。"""
        import webview
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return ""
        try:
            picked = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("图片 (*.blp;*.dds;*.tif;*.tga;*.png)", "所有文件 (*.*)"),
            )
        except Exception:                              # noqa: BLE001
            return ""
        if not picked:
            return ""
        return picked[0] if isinstance(picked, (list, tuple)) else str(picked)


def _try_pywebview(url: str, debug: bool = False):
    """有 pywebview 就用原生窗口（打包后走这条），没有就退回浏览器。

    每一步都记日志：窗口在 PyInstaller 下崩过好几次，都是原生崩溃（没有
    Python 栈），只能靠"最后写进日志的那一行"判断卡在哪一步。
    """
    try:
        import webview                                    # type: ignore
        _note("pywebview 已导入")
    except Exception as exc:                              # noqa: BLE001
        _note(f"pywebview 不可用，改用系统浏览器：{type(exc).__name__}: {exc}")
        return None
    window = webview.create_window(brand.APP_NAME, url,
                                   width=brand.WINDOW_SIZE[0],
                                   height=brand.WINDOW_SIZE[1],
                                   min_size=brand.WINDOW_MIN,
                                   js_api=_Bridge())
    _note("窗口对象已创建，进入 webview.start()")

    # WebView2 要有一个稳定的用户数据目录。不指定的话它会挑一个跟进程绑定的
    # 临时路径，而 onefile 打包时那个路径随进程消失，窗口就永远起不来（表现为
    # 进程活着但没有任何窗口，也没有报错）。
    storage = brand.user_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    _note(f"WebView2 数据目录：{storage}")

    webview.start(debug=debug, storage_path=str(storage))   # 阻塞到窗口关闭
    _note("窗口已关闭")
    return window


if __name__ == "__main__":
    _install_crash_handler()
    raise SystemExit(main())
