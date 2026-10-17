"""抓真实渲染画面，用来判断「糊」是贴图分辨率、光照还是没贴上。

    python diag_ghost.py "..\\Ghost Captain\\vanderdood.mdx"            # 默认（512 → 1024 渐进）
    python diag_ghost.py "..\\Ghost Captain\\vanderdood.mdx" --texup 0  # 关掉升级，只看 512

输出的 PNG 会按内容裁好并放大 3 倍，直接看细节。
"""

from __future__ import annotations

import base64
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


def _metric(png: pathlib.Path) -> tuple[int, float]:
    """返回 (内容框面积, 边缘能量)。边缘能量越高越锐利。"""
    from PIL import Image, ImageFilter

    im = Image.open(png).convert("RGBA")
    alpha = im.split()[-1]
    box = alpha.getbbox() or (0, 0, 1, 1)
    crop = im.crop(box).convert("L")
    edges = crop.filter(ImageFilter.FIND_EDGES)
    px = list(edges.getdata())
    energy = sum(px) / max(1, len(px))
    return (box[2] - box[0]) * (box[3] - box[1]), round(energy, 2)


def main() -> int:
    args = sys.argv[1:]
    texup = "1"
    if "--texup" in args:
        i = args.index("--texup")
        texup = args[i + 1]
        del args[i:i + 2]
    target = pathlib.Path(args[0] if args else
                          "../Ghost Captain/vanderdood.mdx").resolve()
    if not target.is_file():
        print("找不到模型:", target)
        return 1

    srv, _ = server.serve(port=0)
    token = srv.app.token
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    req = urllib.request.Request(
        f"{base}/api/open?token={token}",
        data=json.dumps({"path": str(target)}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        print("打开:", json.loads(r.read())["ok"])

    url = f"{base}/?token={token}&texup={texup}"
    print("页面:", url)

    import webview

    win = webview.create_window("diag", url, width=1500, height=1050)

    def run(w):
        for _ in range(160):
            time.sleep(0.5)
            if w.evaluate_js("document.readyState==='complete' && !!window.MDX_DEBUG"):
                break
        time.sleep(5)

        print("texstat:", w.evaluate_js("JSON.stringify(window.MDX_DEBUG.texstat)"))
        # 等贴图真的铺满（不然截到的是半白模型，看着像"没加载出材质"）
        for _ in range(60):
            st0 = json.loads(w.evaluate_js("JSON.stringify(window.MDX_DEBUG.status)") or "{}")
            if st0.get("meshes") and st0.get("maps") == st0.get("meshes"):
                break
            time.sleep(1)
        print("铺满用时约", " 见下（maps/meshes）")
        if texup != "0":
            for _ in range(90):
                up = json.loads(w.evaluate_js("JSON.stringify(window.MDX_DEBUG.texupgrade)") or "{}")
                if up.get("pending", 1) == 0:
                    break
                time.sleep(1)
            print("高清升级:", up)
        print("status:", w.evaluate_js("JSON.stringify(window.MDX_DEBUG.status)"))

        tag = "512" if texup == "0" else "hi"
        data = w.evaluate_js("window.MDX_DEBUG.snapshot(1200)")
        if isinstance(data, str) and data.startswith("data:image/png;base64,"):
            raw = base64.b64decode(data.split(",", 1)[1])
            out = HERE / f"diag-{tag}.png"
            out.write_bytes(raw)
            print(f"截图: {out}  {len(raw) / 1024:.0f} KB")

            from PIL import Image
            im = Image.open(out).convert("RGBA")
            box = im.split()[-1].getbbox() or (0, 0, 1, 1)
            crop = im.crop(box).convert("RGB")
            crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            crop.save(HERE / f"diag-{tag}-crop.png")
            print(f"裁剪: {crop.size}  面积/边缘能量 = {_metric(out)}")
        else:
            print("截图失败:", str(data)[:120])
        w.destroy()

    webview.start(run, win)
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
