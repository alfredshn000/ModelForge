"""打包成独立 exe。

    python build.py              # 开发版：dist/ModelForge/（前端外置，改完即生效）
    python build.py --zip        # 上面那份 + dist/ModelForge-win64.zip（发给别人用这个）
    python build.py --console    # 保留控制台窗口（看启动日志）
    python build.py --check      # 只检查环境，不打包

## 为什么默认是文件夹而不是单文件

`--onefile`（单个 exe）**在这个项目里不可用**：实测打包后进程能启动、服务也正常，
但 pywebview 的窗口永远不出现——卡在 `webview.start()` 里，没有异常也没有日志。
同样的代码打成 onedir 就 5 秒内正常出窗口。这是 PyInstaller 单文件模式解压到
临时目录后，WebView2 / .NET 桥接加载托管程序集的问题，不是业务代码的错。

所以交付形态是「文件夹 + zip」：解压即用，双击里面的 ModelForge.exe。

**注意 exe 不能单独拷走**：`_internal/` 里是 Python 运行时，缺了它 PyInstaller 的
引导器会直接报 `Failed to load Python DLL` 然后退出——看起来就是"双击闪退"。

## 开发版 vs 发布版的区别

- **开发版**：Python 内核打进 exe，`web/` 放在 exe 旁边。改界面只要换掉 `web/` 里的
  文件，连重启都不用（静态资源 no-store + 每次读盘），不必等一次完整打包。
- **打包分发**：`--zip` 会连 `web/` 一起压进压缩包，收到的人解压就能用。

## 打包前需要装的东西

    python -m pip install pyinstaller pywebview

pyinstaller 是纯打包工具；pywebview 提供原生窗口（不装会退回系统浏览器，
功能一样但会露出地址栏）。图标由 `make_icons.py` 从原始 logo 生成。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import brand  # noqa: E402

KIT = HERE.parent / "mdx-toolkit"
WEB = HERE / "web"
ICON = HERE / "assets" / "model-forge.ico"
NAME = brand.APP_ID
OUT = HERE / "build-out"


def _version_tuple() -> tuple[int, int, int, int]:
    parts = []
    for chunk in brand.VERSION.split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], 0)


def write_version_file(path: pathlib.Path) -> pathlib.Path:
    """生成 PyInstaller 的版本资源，写进 exe 的「属性 → 详细信息」里。"""
    v = _version_tuple()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', '{brand.AUTHOR}'),
        StringStruct('FileDescription', '{brand.DESCRIPTION}'),
        StringStruct('FileVersion', '{brand.VERSION}'),
        StringStruct('InternalName', '{NAME}'),
        StringStruct('LegalCopyright', '{brand.COPYRIGHT}'),
        StringStruct('OriginalFilename', '{NAME}.exe'),
        StringStruct('ProductName', '{NAME}'),
        StringStruct('ProductVersion', '{brand.VERSION}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""", encoding="utf-8")
    return path


def check_env() -> list[str]:
    """返回缺失依赖的提示，空列表代表可以开工。"""
    missing = []
    try:
        import PyInstaller  # noqa: F401
    except Exception:  # noqa: BLE001
        missing.append("pyinstaller  →  python -m pip install pyinstaller")
    try:
        import webview  # noqa: F401
    except Exception:  # noqa: BLE001
        missing.append("pywebview    →  python -m pip install pywebview"
                       "（不装也能打包，但会退回系统浏览器）")
    if not (WEB / "index.html").is_file():
        missing.append(f"前端缺失     →  {WEB / 'index.html'} 不存在")
    if not (KIT / "mdxcore").is_dir():
        missing.append(f"内核缺失     →  {KIT / 'mdxcore'} 不存在")
    if not ICON.is_file():
        missing.append(f"图标缺失     →  {ICON} 不存在（跑一次 make_icons.py）")
    return missing


def _retire(path: pathlib.Path) -> None:
    """把上一次的产物挪进 _old/，不删除任何东西。

    PyInstaller 重建 onedir 时会在内部递归删掉旧目录（一次上百个文件），
    某些机器上的安全策略会把这种批量删除拦下来导致打包中断。移动没这个问题。
    """
    trash = OUT / "_old"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / path.name
    if target.exists():
        target = trash / f"{path.name}-{int(time.time())}"
    path.rename(target)
    print(f"上一次的产物已移到：{target}（确认没用可以手动删掉）")


def make_zip(src: pathlib.Path, dst: pathlib.Path) -> pathlib.Path:
    """把产物文件夹压成 zip。解压后是 `ModelForge\\...`，不套多层目录。"""
    if dst.exists():
        dst.unlink()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src.parent))
    return dst


def build_single(console: bool) -> int:
    """打成「一个 exe 双击就能用」。

    做法不是直接把主程序 onefile（那样 pywebview 窗口起不来），而是：
        1. 先按正常 onedir 打出完整运行时
        2. 压成 runtime.zip
        3. 用一个不含 pywebview 的小启动器包住它
    用户拿到的是一个 exe；首次运行释放到 %LOCALAPPDATA%\\ModelForge\\runtime\\<版本>\\，
    之后直接复用。实际跑的还是 onedir 结构，所以窗口、CASC、贴图都正常。
    """
    print("\n[1/3] 打运行时（onedir）")
    rc = build(onefile=False, bundle_web=False, console=console, make_zip_flag=False)
    if rc != 0:
        return rc

    app_dir = OUT / "dist" / NAME
    if not (app_dir / f"{NAME}.exe").is_file():
        print(f"运行时目录不完整：{app_dir}")
        return 1

    print("\n[2/3] 压缩运行时")
    runtime_zip = OUT / "spec" / "runtime.zip"
    runtime_zip.parent.mkdir(parents=True, exist_ok=True)
    if runtime_zip.exists():
        runtime_zip.unlink()
    with zipfile.ZipFile(runtime_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(app_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(app_dir))     # 解压后直接是 ModelForge.exe 那一层
    mb = runtime_zip.stat().st_size / 1024 / 1024
    print(f"  {runtime_zip}  {mb:.1f} MB")

    print("\n[3/3] 打启动器")
    single_dir = OUT / "dist" / "single"
    stale = single_dir / f"{NAME}.exe"
    if stale.exists():
        _retire(stale)
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--onefile",
        "--icon", str(ICON),
        "--version-file", str(write_version_file(OUT / "spec" / "version_info.txt")),
        "--add-data", f"{runtime_zip}{os.pathsep}.",
        "--hidden-import", "brand",
        "--paths", str(HERE),
        "--distpath", str(single_dir),
        "--workpath", str(OUT / "build-launcher"),
        "--specpath", str(OUT / "spec"),
    ]
    cmd.append("--console" if console else "--windowed")
    cmd.append(str(HERE / "launcher.py"))
    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"\n启动器打包失败，退出码 {rc}")
        return rc

    exe = single_dir / f"{NAME}.exe"
    size = exe.stat().st_size / 1024 / 1024 if exe.is_file() else 0
    print(f"\n单文件：{exe}   {size:.1f} MB")
    print("  拷这一个文件就能用；首次启动会释放运行时（约 2–4 秒），之后走缓存。")
    print(f"  运行时缓存：%LOCALAPPDATA%\\{NAME}\\runtime\\<版本>-<内容哈希>\\"
          "（内容变了自动换目录，不会跑到旧副本）")
    return 0


def build(onefile: bool, bundle_web: bool, console: bool, make_zip_flag: bool) -> int:
    dist = OUT / "dist"
    work = OUT / "build"
    spec = OUT / "spec"

    version_file = write_version_file(spec / "version_info.txt")

    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--paths", str(KIT),                       # 让分析器找得到 mdxcore
        "--collect-submodules", "mdxcore",         # chunk 解析器一个都别漏
        "--collect-all", "webview",                # 窗口后端 + 它带的注入脚本
        "--collect-all", "pythonnet",              # .NET 桥接的托管程序集
        "--collect-all", "clr_loader",             # 运行时的加载器
        "--hidden-import", "clr",                  # pywebview 走 .NET 时要
        "--icon", str(ICON),
        "--version-file", str(version_file),
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(spec),
    ]
    cmd.append("--console" if console else "--windowed")
    if onefile:
        cmd.append("--onefile")
    if bundle_web:
        cmd += ["--add-data", f"{WEB}{os.pathsep}web"]

    cmd.append(str(HERE / "run.py"))

    stale = (dist / f"{NAME}.exe") if onefile else (dist / NAME)
    if stale.exists():
        _retire(stale)

    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"\n打包失败，退出码 {rc}")
        return rc

    # 前端外置：复制到 exe 旁边
    if not bundle_web:
        target = (dist / "web") if onefile else (dist / NAME / "web")
        if target.exists():
            _retire(target)
        shutil.copytree(WEB, target)
        print(f"\n前端已外置：{target}")

    exe = (dist / f"{NAME}.exe") if onefile else (dist / NAME / f"{NAME}.exe")
    size = exe.stat().st_size / 1024 / 1024 if exe.is_file() else 0
    print(f"\n可执行文件：{exe}   {size:.1f} MB")

    if onefile:
        print("警告：单文件模式实测窗口起不来（见文件开头说明），仅用于排查。")
    else:
        print("交付方式：整个文件夹一起拷走（或解压 zip）。")
        print("  · 只拷 exe 出去会因为缺 _internal 而闪退")
        print("  · 改界面 → 换掉 web/ 里的文件即可，不用重新打包")
        print("  · 改内核 → 重新运行本脚本")

    if make_zip_flag and not onefile:
        src = dist / NAME
        zpath = OUT / f"{NAME}-win64.zip"
        size_zip = 0
        if src.is_dir():
            out = make_zip(src, zpath)
            size_zip = out.stat().st_size / 1024 / 1024
            print(f"\n压缩包：{out}   {size_zip:.1f} MB（发给别人用这个）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"打包 {brand.APP_NAME}")
    ap.add_argument("--zip", action="store_true",
                    help="打包完再压一个 zip（发给别人用这个）")
    ap.add_argument("--single", action="store_true",
                    help="打成单文件 exe（自解压运行时，双击即用）")
    ap.add_argument("--onefile", action="store_true",
                    help="单文件 exe（实测窗口起不来，仅用于排查）")
    ap.add_argument("--bundle-web", action="store_true", help="把前端也内嵌进 exe")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（看日志）")
    ap.add_argument("--check", action="store_true", help="只检查环境")
    args = ap.parse_args()

    missing = check_env()
    if missing:
        print("环境检查：")
        for m in missing:
            print("  ✗ " + m)
        if args.check or any("pyinstaller" in m for m in missing):
            return 1
    else:
        print("环境检查：全部就绪")

    if args.check:
        return 0
    if args.single:
        return build_single(args.console)
    return build(args.onefile, args.bundle_web, args.console, args.zip)


if __name__ == "__main__":
    raise SystemExit(main())
