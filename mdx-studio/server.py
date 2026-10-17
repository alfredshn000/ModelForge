"""本地 HTTP 服务：前端与内核之间唯一的通道。

只监听 127.0.0.1，启动时生成一次性 token，所有请求都要带。
不做任何外部网络请求，关掉窗口进程就退出。

## 为什么用 HTTP 而不是直接 IPC

前端跑在 WebView 里（浏览器环境），能跟 Python 通信的方式只有 HTTP / WebSocket。
反过来「把 Python 编译成 WASM 塞进浏览器」虽然更"干净"，但没有文件系统访问，
打不开用户的 mdx、写不了盘、读不了游戏 CASC——核心能力全没了。

## 接口

    GET  /                      前端页面（带 ?token=）
    GET  /static/<file>         静态资源
    GET  /api/health            探活
    POST /api/open   {path}     打开模型 → 返回 meta
    GET  /api/meta   ?sid=      当前界面数据（含未保存改动）
    GET  /api/render ?sid=      渲染数据（顶点/骨骼/动画，gzip）
    GET  /api/tex    ?sid=&i=   贴图 PNG
    POST /api/edit   {kind,...} 记录一条改动
    POST /api/revert {sid}      放弃全部改动
    POST /api/save   {sid}      校验 → 备份 → 写盘
    GET  /api/browse ?dir=      列目录（前端的简易文件选择器用）
    GET  /api/recent            最近打开过的模型
"""

from __future__ import annotations

import copy
import gzip
import json
import mimetypes
import os
import pathlib
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brand
import session as sessionmod
import textures as texmod

ROOT = pathlib.Path(__file__).resolve().parent


def _frozen() -> bool:
    """当前是不是 PyInstaller 打出来的可执行文件。"""
    return bool(getattr(sys, "frozen", False))


def _log(text: str) -> None:
    """服务端自己的日志。打包后没有控制台，出问题只能写文件。"""
    try:
        import time
        with brand.log_file().open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:                                  # noqa: BLE001
        pass


def _exe_dir() -> pathlib.Path | None:
    """exe 所在目录；源码运行时没有这个概念，返回 None。"""
    if not _frozen():
        return None
    try:
        return pathlib.Path(sys.executable).resolve().parent
    except OSError:
        return None


def resolve_web_dir(explicit: str | None = None) -> pathlib.Path:
    """定位前端目录（内核内嵌、前端外置）。

    Python 逻辑打进 exe，web/ 留在 exe 旁边。这样改界面只是换文件 + 刷新，
    不必重新打包；只有动内核（mdxcore / server.py）才需要重打包。

    查找顺序：
        1. --web-dir 显式指定
        2. MDX_WEB_DIR 环境变量
        3. exe 同目录/web                ← 发布版走这条（外置）
        4. exe 同目录/_internal/web      ← onedir 的资源默认落点
        5. 源码目录/web                  ← 开发期
        6. PyInstaller 解压目录/web      ← 内嵌兜底（--bundle-web 时才有）

    判定标准是目录里真的有 index.html——外置目录被删了一半时不会挑到空壳。
    """
    cands: list[pathlib.Path] = []
    if explicit:
        cands.append(pathlib.Path(explicit))
    env = os.environ.get("MDX_WEB_DIR")
    if env:
        cands.append(pathlib.Path(env))
    exe = _exe_dir()
    if exe is not None:
        cands.append(exe / "web")
        cands.append(exe / "_internal" / "web")
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        cands.append(pathlib.Path(bundle) / "web")   # 内嵌的那份（打包后 __file__ 也在这）
    cands.append(ROOT / "web")                       # 源码运行

    for cand in cands:
        try:
            if (cand / "index.html").is_file():
                return cand.resolve()
        except OSError:
            continue
    return (cands[0] if cands else ROOT / "web")


WEB = resolve_web_dir()

GAME_DIRS = [
    r"D:/Games/War3 2.0",
    r"C:/Program Files (x86)/Warcraft III",
    r"C:/Program Files/Warcraft III",
]

# 还没打开模型时给前端的空骨架。
#
# 以前这里直接报 400「还没有打开模型」，而页面的引导脚本一遇到失败就 return，
# 结果 app.js 根本没被加载——界面骨架画出来了，但所有按钮都没绑事件，
# 表现就是"窗口开着，什么都点不了"。改成返回结构完整的空数据，
# 让界面照常进入可用状态，用户点「打开模型…」再走 /api/open。
EMPTY_META = {
    "model": {"file": "", "path": "", "bytes": 0, "version": 0,
              "chunks": [], "pivotNote": "", "bones": 0, "keys": 0},
    "textures": [],
    "materials": [],
    "sequences": [],
    "attachments": [],
    "pivots": [],
    "changes": [],
    "dirty": False,
    "empty": True,
}

EMPTY_RENDER = {
    "ver": 0,
    "bytes": 0,
    "bones": [],
    "geosets": [],
    "tracks": [],
    "particles": [],
    "sequences": [],
    "bounds": {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]},
    "stats": {"bones": 0, "keys": 0, "tris": 0, "verts": 0},
    "empty": True,
}


class App:
    """进程内共享状态：一个打开中的模型 + 一个贴图库。"""

    def __init__(self, casc_path: str | None = None):
        self.sess: sessionmod.ModelSession | None = None
        self.recent: list[str] = []
        self.lock = threading.Lock()
        self.casc_path = pathlib.Path(casc_path or os.environ.get("MDX_CASC_PATH")
                                      or GAME_DIRS[0])
        self._casc = None
        self._casc_tried = False
        self._model_cache: dict[str, list[str]] = {}     # sd/hd 的模型清单
        self._team_colors: list[str] | None = None
        self.last_origin: dict | None = None             # 当前模型从哪来
        self.store = texmod.TextureStore(self._roots(), self._casc_factory)
        # MDX_TOKEN 只给自动化测试用，正常启动是随机的一次性 token
        self.token = os.environ.get("MDX_TOKEN") or secrets.token_urlsafe(16)

    def _roots(self) -> list[pathlib.Path]:
        out = []
        test = ROOT.parent / "test-models"
        if test.is_dir():
            out.append(test)                    # 开发期的测试素材
        return out

    def _casc_factory(self):
        """懒打开 CASC。App 和 TextureStore 共用同一个实例——重复打开要几秒。"""
        if self._casc is not None:
            return self._casc
        from mdxcore.casc import deepest_install, open_casc
        if not self.casc_path.exists():
            return None
        try:
            self._casc = open_casc(deepest_install(self.casc_path))
        except Exception as exc:                        # noqa: BLE001
            _log(f"CASC 打开失败：{type(exc).__name__}: {exc}")
            return None
        return self._casc

    # ------------------------------------------------------------ 游戏资源

    # 队伍色从游戏里读，不写死：模组改过这些贴图时会跟着变。
    # 顺序就是 WC3 的 00–23，和 `Player(i).setTeamColor` 的编号一致。
    FALLBACK_TEAM_COLORS = [
        "#ff0303", "#0042ff", "#1ce6b9", "#540081", "#fffc01", "#fe8a0e",
        "#20c000", "#e55bb0", "#959697", "#7ebff1", "#106246", "#4e2a04",
        "#9b0000", "#0000c3", "#00eaff", "#be00fe", "#ebcd87", "#f8a48b",
        "#bfff80", "#dcb9eb", "#4f4f55", "#ebf0ff", "#00781d", "#a46f33",
    ]

    def team_colors(self) -> list[str]:
        """24 个玩家颜色（从 ReplaceableTextures\\TeamColor\\TeamColorNN.blp 读平均色）。"""
        if self._team_colors is not None:
            return self._team_colors
        out = list(self.FALLBACK_TEAM_COLORS)
        casc = self._casc_factory()
        if casc is not None:
            from mdxcore import dds
            ok = 0
            for i in range(24):
                try:
                    raw = casc.read(f"ReplaceableTextures\\TeamColor\\TeamColor{i:02d}.blp")
                    w, h, rgba = dds.decode(raw, 0)
                    n = max(1, w * h)
                    r = round(sum(rgba[0::4]) / n)
                    g = round(sum(rgba[1::4]) / n)
                    b = round(sum(rgba[2::4]) / n)
                    out[i] = f"#{r:02x}{g:02x}{b:02x}"
                    ok += 1
                except Exception:                      # noqa: BLE001
                    continue
            _log(f"队伍色：从游戏读到 {ok}/24")
        self._team_colors = out
        return out

    # 游戏里模型分两套：war3.w3mod 是经典（SD），war3.w3mod\_hd.w3mod 是重制版。
    # 两边是**同名的不同模型**，必须分开列，否则用户分不清打开的是哪一套。
    CASC_ROOTS = {
        "sd": "war3.w3mod",
        "hd": r"war3.w3mod\_hd.w3mod",
    }

    def casc_models(self, hd: bool, query: str = "", limit: int = 400) -> dict:
        """列游戏内置模型。query 不区分大小写，匹配整条路径。"""
        casc = self._casc_factory()
        if casc is None:
            return {"ok": False, "error": f"打不开游戏数据：{self.casc_path}"}

        key = "hd" if hd else "sd"
        if key not in self._model_cache:
            root = self.CASC_ROOTS[key]
            files = casc.list_all(root)
            self._model_cache[key] = sorted(f for f in files if f.lower().endswith(".mdx"))
        all_models = self._model_cache[key]

        q = (query or "").strip().lower()
        hits = [m for m in all_models if q in m.lower()] if q else all_models
        return {"ok": True, "total": len(all_models), "matched": len(hits),
                "hd": hd, "items": hits[:limit], "truncated": len(hits) > limit}

    def open_from_casc(self, rel: str, hd: bool = False) -> dict:
        """把游戏里的模型取出来，落到本地工作目录再打开。

        **绝不直接改游戏文件**：游戏资源是只读素材，改动写进取出来的副本。
        """
        casc = self._casc_factory()
        if casc is None:
            raise sessionmod.SessionError(f"打不开游戏数据：{self.casc_path}")
        rel = (rel or "").replace("/", "\\").lstrip("\\")
        if not rel or ".." in rel:
            raise sessionmod.SessionError("模型路径不合法")
        root = self.CASC_ROOTS["hd" if hd else "sd"]
        full = f"{root}\\{rel}"
        try:
            data = casc.read(full)
        except Exception as exc:                        # noqa: BLE001
            raise sessionmod.SessionError(f"游戏里读不到这个模型：{rel}") from exc

        target = self.exports_dir() / (("hd_" if hd else "") + pathlib.Path(rel).name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        meta = self.open(str(target))
        self.last_origin = {"from": "casc", "source": full, "copy": str(target)}
        return meta

    def exports_dir(self) -> pathlib.Path:
        """从游戏里取出来的副本放这儿。用户改动、保存都落在这一份上。"""
        base = os.environ.get("LOCALAPPDATA")
        root = pathlib.Path(base) if base else pathlib.Path.home()
        d = root / "ModelForge" / "models"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def open(self, path: str) -> dict:
        sess = sessionmod.ModelSession.open(path, self.store)
        self.sess = sess
        p = str(pathlib.Path(path).resolve())
        if p in self.recent:
            self.recent.remove(p)
        self.recent.insert(0, p)
        del self.recent[12:]
        return sess.meta()

    def need(self) -> sessionmod.ModelSession:
        if self.sess is None:
            raise sessionmod.SessionError("还没有打开模型")
        return self.sess

    # 只读接口用这两个：没模型时给空骨架，界面上照样能点

    def meta_or_empty(self) -> dict:
        return self.sess.meta() if self.sess is not None else copy.deepcopy(EMPTY_META)

    def render_or_empty(self) -> dict:
        return self.sess.render() if self.sess is not None else copy.deepcopy(EMPTY_RENDER)


class Handler(BaseHTTPRequestHandler):
    server_version = f"{brand.APP_NAME}/{brand.VERSION}"
    # HTTP/1.0 下浏览器会把请求复用到服务器已关闭的预连接上，
    # 表现是「script 请求 fetch 得到内容、但 onload 永不触发」。
    # 所有响应都带 Content-Length，所以 1.1 的 keep-alive 是安全的。
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- 基础

    def log_message(self, fmt, *args):
        pass                                  # 控制台保持安静，错误自己报

    @property
    def app(self) -> App:
        return self.server.app                      # type: ignore[attr-defined]

    def _query(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def _authed(self, q: dict[str, str]) -> bool:
        token = self.headers.get("X-MDX-Token") or q.get("token")
        return bool(token) and secrets.compare_digest(token, self.app.token)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:                          # noqa: BLE001
            raise sessionmod.SessionError("请求体不是合法 JSON")

    def _send(self, data: bytes, ctype: str, status: int = 200, gz: bool = False):
        if gz and len(data) > 2048 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data = gzip.compress(data, 6)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status: int = 200):
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(payload, "application/json; charset=utf-8", status, gz=True)

    def _fail(self, msg: str, status: int = 400):
        self._json({"ok": False, "error": str(msg)}, status)

    # ---------------------------------------------------------------- 路由

    def do_GET(self) -> None:                     # noqa: N802
        q = self._query()
        path = self._path()
        # 图标由 <link>/<img> 直接请求，带不上 token，而且本身不含任何信息
        if path in ("/favicon.ico", "/logo.png"):
            if path == "/favicon.ico":
                return self._static("logo.ico")
            return self._static("logo-128.png")
        if path in ("/", "/index.html"):
            if not self._authed(q):
                return self._fail("token 无效，请从启动器打开的窗口访问", 403)
            return self._static("index.html")
        if path.startswith("/static/"):
            if not self._authed(q):
                return self._fail("token 无效", 403)
            return self._static(path[len("/static/"):])

        if not self._authed(q):
            return self._fail("token 无效", 403)
        try:
            if path == "/api/health":
                return self._json({"ok": True, "version": brand.VERSION,
                                   "app": brand.APP_NAME,
                                   "game": str(self.app.casc_path),
                                   "gameExists": self.app.casc_path.exists(),
                                   "web": str(self.server.web_dir),
                                   "frozen": _frozen(),
                                   "hasModel": self.app.sess is not None})
            if path == "/api/meta":
                data = self.app.meta_or_empty()
                if self.app.last_origin and isinstance(data, dict):
                    data["origin"] = self.app.last_origin   # 标明这份副本从哪来
                return self._json({"ok": True, "data": data})
            if path == "/api/render":
                return self._json({"ok": True, "data": self.app.render_or_empty()})
            if path == "/api/tex":
                return self._texture(q)
            if path == "/api/browse":
                return self._json({"ok": True, "entries": self._browse(q.get("dir", ""))})
            if path == "/api/casc/models":
                hd = q.get("hd", "0") in ("1", "true", "yes")
                limit = max(1, min(2000, int(q.get("limit", "400") or 400)))
                return self._json(self.app.casc_models(hd, q.get("q", ""), limit))
            if path == "/api/recent":
                return self._json({"ok": True, "items": self.app.recent})
            if path == "/api/teamcolors":
                return self._json({"ok": True, "colors": self.app.team_colors()})
            return self._fail(f"未知接口 {path}", 404)
        except sessionmod.SessionError as exc:
            return self._fail(exc)
        except Exception as exc:                       # noqa: BLE001
            return self._fail(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:                    # noqa: N802
        q = self._query()
        if not self._authed(q):
            return self._fail("token 无效", 403)
        path = self._path()
        try:
            body = self._body()
            if path == "/api/open":
                return self._json({"ok": True, "data": self.app.open(body.get("path", ""))})
            if path == "/api/casc/open":
                return self._json({"ok": True, "data": self.app.open_from_casc(
                    body.get("path", ""), bool(body.get("hd"))),
                    "origin": self.app.last_origin})
            if path == "/api/edit":
                sess = self.app.need()
                sess.edit(body.get("kind", ""), body.get("payload") or {})
                return self._json({"ok": True, "data": sess.meta()})
            if path == "/api/apply":
                # 批量改动：整体校验通过才生效，避免一半成功一半失败
                sess = self.app.need()
                edits = body.get("edits") or []
                if not isinstance(edits, list):
                    raise sessionmod.SessionError("edits 必须是数组")
                snapshot = copy.deepcopy(sess.pending)
                try:
                    for e in edits:
                        sess.edit(e.get("kind", ""), e.get("payload") or {})
                except Exception:
                    sess.pending = snapshot
                    raise
                return self._json({"ok": True, "data": sess.meta()})
            if path == "/api/revert":
                self.app.need().revert()
                return self._json({"ok": True, "data": self.app.need().meta()})
            if path == "/api/save":
                sess = self.app.need()
                report = sess.save(body.get("dest"))
                report["data"] = sess.meta()
                return self._json({"ok": True, "report": report})
            return self._fail(f"未知接口 {path}", 404)
        except sessionmod.SessionError as exc:
            return self._fail(exc)
        except Exception as exc:                       # noqa: BLE001
            return self._fail(f"{type(exc).__name__}: {exc}", 500)

    # ---------------------------------------------------------------- 静态

    # Windows 的 mimetypes 会读注册表，`.js` 常被判成 text/plain，
    # 而 Chrome 拒绝执行非 JS MIME 的 <script>——表现是"脚本请求 200 但没执行"。
    STATIC_TYPES = {
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }

    def _static(self, rel: str) -> None:
        root = pathlib.Path(getattr(self.server, "web_dir", WEB)).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)          # 挡掉 ../ 穿越
        except ValueError:
            return self._fail(f"找不到 {rel}", 404)
        if not target.is_file():
            return self._fail(f"找不到 {rel}", 404)
        ctype = self.STATIC_TYPES.get(target.suffix.lower()) \
            or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(target.read_bytes(), ctype)

    # ---------------------------------------------------------------- 贴图

    def _texture(self, q: dict[str, str]) -> None:
        sess = self.app.need()
        try:
            index = int(q.get("i", "-1"))
            size = max(32, min(2048, int(q.get("size", "512"))))
            team = max(0, min(23, int(q.get("team", "0") or 0)))
        except ValueError:
            return self._fail("参数不合法")
        payload, info = sess.texture(index, size, team)
        if payload is None:
            _log(f"/api/tex 无图 i={index} size={size}：{info.get('origin')}")
            return self._json({"ok": False, "error": info.get("origin", "找不到"),
                               "info": info}, 404)
        self._send(payload, "image/png")
        info["cache"] = "miss"

    # ---------------------------------------------------------------- 目录

    @staticmethod
    def _browse(dir_path: str) -> list[dict]:
        if not dir_path:
            # Windows 下给盘符列表
            items = []
            for letter in "CDEFGH":
                root = pathlib.Path(f"{letter}:/")
                if root.exists():
                    items.append({"name": f"{letter}:\\", "path": str(root), "dir": True})
            items.append({"name": "工作目录", "path": str(ROOT.parent), "dir": True})
            return items
        p = pathlib.Path(dir_path)
        if not p.is_dir():
            raise sessionmod.SessionError(f"不是目录：{dir_path}")
        out = [{"name": "..", "path": str(p.parent), "dir": True}]
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir or e.suffix.lower() == ".mdx":
                out.append({"name": e.name, "path": str(e), "dir": is_dir,
                            "size": 0 if is_dir else e.stat().st_size})
        return out[:500]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, app: App, web_dir: pathlib.Path | str | None = None):
        self.app = app
        self.web_dir = pathlib.Path(web_dir) if web_dir else WEB
        super().__init__(addr, Handler)


def serve(host: str = "127.0.0.1", port: int = 0, casc_path: str | None = None,
          web_dir: str | None = None):
    """启动服务，返回 (server, url)。port=0 表示自动挑空闲端口。"""
    app = App(casc_path)
    srv = Server((host, port), app, web_dir=resolve_web_dir(web_dir))
    actual = srv.server_address[1]
    url = f"http://{host}:{actual}/?token={app.token}"
    return srv, url
