"""验证前端目录的查找优先级（内核内嵌 + 前端外置）。

核心要保证两件事：
  1. 打包后 exe 旁边的 web/ 优先于 exe 内嵌的那份 —— 否则"改前端不用重打包"就不成立
  2. 外置目录残缺（没有 index.html）时不能选它，否则一启动就白屏

跑法： python test_webdir.py
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mdx-toolkit"))
sys.path.insert(0, str(HERE))

import server  # noqa: E402

OK = 0
FAIL = 0


def check(label: str, got, want) -> None:
    global OK, FAIL
    if str(got) == str(want):
        OK += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n        期望 {want}\n        实际 {got}")


def make_web(root: pathlib.Path, tag: str) -> pathlib.Path:
    """造一个能被认出来的最小前端目录。"""
    d = root / "web"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(f"<!-- {tag} -->", encoding="utf-8")
    return d


def main() -> int:
    print("默认情况（源码运行）：")
    check("定位到源码 web/", server.resolve_web_dir(), HERE / "web")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        fake = make_web(tmpdir / "ext", "external")

        print("\n显式参数 / 环境变量：")
        check("--web-dir 生效", server.resolve_web_dir(str(fake)), fake)

        os.environ["MDX_WEB_DIR"] = str(fake)
        check("环境变量生效", server.resolve_web_dir(), fake)
        check("显式参数压过环境变量", server.resolve_web_dir(str(HERE / "web")), HERE / "web")
        del os.environ["MDX_WEB_DIR"]

        print("\n模拟打包后（frozen，exe 旁边放了 web/）：")
        exedir = tmpdir / "dist"
        ext = make_web(exedir, "beside-exe")
        # 内嵌那份在 _MEIPASS 里，内容不同以便区分
        meipass = tmpdir / "_MEI12345"
        embed = make_web(meipass, "embedded")

        old = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None), sys.executable)
        try:
            sys.frozen = True                      # type: ignore[attr-defined]
            sys._MEIPASS = str(meipass)            # type: ignore[attr-defined]
            sys.executable = str(exedir / "MDXStudio.exe")
            check("外置优先于内嵌", server.resolve_web_dir(), ext)
            check("内嵌只做兜底", (embed / "index.html").read_text(encoding="utf-8"),
                  "<!-- embedded -->")

            print("\n外置目录残缺时：")
            (ext / "index.html").unlink()
            check("缺 index.html 的外置目录被跳过", server.resolve_web_dir(),
                  meipass / "web")
        finally:
            sys.frozen, sys._MEIPASS, sys.executable = old  # type: ignore[attr-defined]

        print("\n服务实例带上 web 目录：")
        os.environ["MDX_TOKEN"] = "webtest"
        srv, url = server.serve(port=0, web_dir=str(fake))
        try:
            check("Server.web_dir 指向外置目录", srv.web_dir, fake)
            check("静态目录可读", (srv.web_dir / "index.html").is_file(), True)

            # 真起一个 HTTP 请求：找到目录不等于能把它发出去
            import json
            import threading
            import urllib.request

            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"

            with urllib.request.urlopen(f"{base}/api/health?token=webtest", timeout=5) as r:
                health = json.loads(r.read())
            check("/api/health 报告的目录", health["web"], fake)
            check("/api/health 的 frozen 标志", health["frozen"], False)

            req = urllib.request.Request(f"{base}/static/index.html?token=webtest")
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read().decode("utf-8")
                check("外置目录的页面真的被服务出去", body.strip(), "<!-- external -->")
                check("Content-Type 是 html", r.headers["Content-Type"],
                      "text/html; charset=utf-8")

            # 换个不存在的文件，确认没变成 200
            try:
                urllib.request.urlopen(f"{base}/static/nope.js?token=webtest", timeout=5)
                check("不存在的静态文件返回 404", "200", "404")
            except urllib.error.HTTPError as exc:
                check("不存在的静态文件返回 404", exc.code, 404)
        finally:
            srv.shutdown()
            srv.server_close()

    print(f"\n结论：{OK} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
