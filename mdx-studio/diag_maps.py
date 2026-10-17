"""查「哪些网格没有贴上贴图」，以及原因。"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "mdx-toolkit"))

import server  # noqa: E402


def main() -> int:
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                          "../Ghost Captain/vanderdood.mdx").resolve()
    srv, _ = server.serve(port=0)
    token = srv.app.token
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    req = urllib.request.Request(f"{base}/api/open?token={token}",
                                 data=json.dumps({"path": str(target)}).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=120):
        pass

    import webview
    win = webview.create_window("diag", f"{base}/?token={token}", width=1200, height=900)

    def run(w):
        for _ in range(160):
            time.sleep(0.5)
            if w.evaluate_js("document.readyState==='complete' && !!window.MDX_DEBUG"):
                break
        time.sleep(2)
        last = None
        for _ in range(30):
            time.sleep(1)
            cur = w.evaluate_js("JSON.stringify(window.MDX_DEBUG.texstat)")
            if cur == last:
                break
            last = cur
        print("texstat:", last)
        st = json.loads(w.evaluate_js("JSON.stringify(window.MDX_DEBUG.status)"))
        print("maps/normalMaps/meshes:", st["maps"], st["normalMaps"], st["meshes"])

        rows = json.loads(w.evaluate_js(
            "JSON.stringify(window.MDX_DEBUG.meshes.map(m=>[m.name,m.tex,m.map,m.normal]))") or "[]")
        bad = [r for r in rows if not r[2]]
        print(f"\n没有贴图的网格 {len(bad)} / {len(rows)}:")
        for name, tex, mp, nm in bad:
            print(f"   geoset name={name!r} tex={tex} map={mp}")
        print("\n全部 tex 索引:", sorted({r[1] for r in rows}, key=lambda v: (v is None, v)))
        w.destroy()

    webview.start(run, win)
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
