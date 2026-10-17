"""后端自测：跑一遍真实的 HTTP 接口，验证「打开 → 改 → 存」全链路。

跑法：

    python test_server.py

会在临时目录复制一份 David.mdx 来改，**不碰原始测试素材**，也不碰游戏目录。
检查重点不是"接口有没有返回 200"，而是：

1. 保存后文件长度**必须不变**（全是定长字段覆写）
2. 字节差异**必须只落在被改的字段里**（用 mdxcore 的 diff 逐段核对）
3. 备份文件存在且与改前字节一致
4. 重新打开能看到新值，且模型仍能完整解析
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mdx-toolkit"))
sys.path.insert(0, str(HERE))

import server                                                # noqa: E402
from mdxcore import ops, parse                               # noqa: E402
from mdxcore.diff import diff_ranges                         # noqa: E402

ROOT = HERE.parent
passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


class Client:
    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token

    def call(self, path: str, method: str = "GET", body: dict | None = None,
             raw: bool = False):
        url = f"{self.base}{path}"
        data = None
        headers = {"X-MDX-Token": self.token}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
                return (payload, resp.headers.get("Content-Type")) if raw else json.loads(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            if raw:
                return payload, exc.headers.get("Content-Type")
            try:
                return json.loads(payload)
            except Exception:                              # noqa: BLE001
                return {"ok": False, "error": payload.decode("utf-8", "replace")}


def main() -> int:
    src = next((ROOT / "test-models").rglob("David.mdx"))
    work = pathlib.Path(tempfile.mkdtemp(prefix="mdxstudio-"))
    target = work / "David.mdx"
    shutil.copyfile(src, target)
    original = target.read_bytes()

    srv, url = server.serve(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    cli = Client(url.split("/?token=")[0], srv.app.token)
    print(f"服务：{cli.base}\n素材：{target}\n")

    try:
        print("接口与打开：")
        h = cli.call("/api/health")
        check("health 可用", h.get("ok") is True, f"game={h.get('gameExists')}")
        check("health 报告还没打开模型", h.get("hasModel") is False, str(h.get("hasModel")))

        # 没有模型时必须给结构完整的空骨架而不是报错：页面引导脚本一旦收到
        # 失败就会放弃加载 app.js，界面会变成"什么都点不了"。
        blank = cli.call("/api/meta")
        check("无模型时 /api/meta 仍然可用", blank.get("ok") is True, str(blank.get("error")))
        bd = blank.get("data") or {}
        check("空骨架标记为 empty", bd.get("empty") is True, str(bd.get("empty")))
        check("空骨架的列表字段都是空数组",
              all(isinstance(bd.get(k), list) and not bd[k]
                  for k in ("textures", "materials", "sequences", "attachments", "pivots")),
              str({k: type(bd.get(k)).__name__ for k in ("textures", "sequences")}))
        check("空骨架带 model 对象", isinstance(bd.get("model"), dict), str(type(bd.get("model"))))

        br = cli.call("/api/render")
        check("无模型时 /api/render 仍然可用", br.get("ok") is True, str(br.get("error")))
        check("渲染空骨架标记为 empty",
              (br.get("data") or {}).get("empty") is True, str((br.get("data") or {}).get("empty")))

        # 真正需要模型的动作仍然要明确拒绝
        bad = cli.call("/api/revert", "POST", {})
        check("无模型时写操作仍被拒绝",
              bad.get("ok") is False, str(bad.get("error")))

        opened = cli.call("/api/open", "POST", {"path": str(target)})
        check("打开模型成功", opened.get("ok") is True, str(opened.get("error") or ""))
        meta = opened["data"]
        texs = meta["textures"]
        check("贴图条数正确", len(texs) == 88, f"{len(texs)} 条")
        check("材质条数正确", len(meta["materials"]) == 27, f"{len(meta['materials'])} 个")
        check("序列条数正确", len(meta["sequences"]) == 10, f"{len(meta['sequences'])} 段")
        check("模型往返一致", meta["model"]["roundTrip"] is True)

        print("\n渲染数据与贴图：")
        t0 = time.time()
        r = cli.call("/api/render")
        dt = (time.time() - t0) * 1000
        rd = r["data"]
        check("render 返回几何", len(rd["geosets"]) > 0,
              f"{len(rd['geosets'])} geoset / {rd['stats']['verts']:,} 顶点 / {dt:.0f} ms")
        check("骨骼与轨道齐全", len(rd["bones"]) > 100 and len(rd["tracks"]) > 100,
              f"{len(rd['bones'])} 骨骼 / {len(rd['tracks'])} 轨道")

        png, ctype = cli.call("/api/tex?i=2&size=128", raw=True)
        check("贴图返回 PNG", ctype == "image/png" and png[:4] == b"\x89PNG",
              f"{len(png):,} B")

        miss = cli.call("/api/tex?i=999", raw=True)
        check("越界贴图索引给出错误", b"error" in miss[0][:200], miss[1] or "")

        print("\n改字段（只记在内存，不动文件）：")
        before_disk = target.read_bytes()
        old_path = texs[2]["path"]
        new_path = old_path.replace("/", "\\", 1) if "/" in old_path else old_path + ""
        if new_path == old_path:
            new_path = old_path.replace("\\", "/", 1)

        e1 = cli.call("/api/edit", "POST",
                      {"kind": "tex", "payload": {"index": 2, "path": new_path}})
        check("改贴图路径成功", e1.get("ok") is True, str(e1.get("error") or ""))
        check("meta 立刻反映新值",
              e1["data"]["textures"][2]["path"] == new_path,
              e1["data"]["textures"][2]["path"])
        check("未保存时磁盘文件未变", target.read_bytes() == before_disk)

        e2 = cli.call("/api/edit", "POST",
                      {"kind": "layer", "payload": {"mi": 0, "li": 0, "alpha": 0.5}})
        check("改图层 alpha 成功", e2.get("ok") is True, str(e2.get("error") or ""))

        e3 = cli.call("/api/edit", "POST",
                      {"kind": "seq", "payload": {"index": 1, "end": 11800}})
        check("改序列区间成功", e3.get("ok") is True, str(e3.get("error") or ""))

        e4 = cli.call("/api/edit", "POST",
                      {"kind": "atch", "payload": {"index": 0,
                                                   "path": r"war3mapImported\TestSword.mdx"}})
        check("改附着点挂载路径成功", e4.get("ok") is True, str(e4.get("error") or ""))

        ch = e4["data"]["changes"]
        check("改动清单有 4 条", len(ch) == 4,
              "、".join(f"{c['kind']}" for c in ch))

        print("\n非法输入必须被拒绝：")
        bads = [
            ("空路径", "tex", {"index": 2, "path": ""}),
            ("非法字符", "tex", {"index": 2, "path": "a*b.mdx"}),
            ("超长路径", "tex", {"index": 2, "path": "x" * 300 + ".dds"}),
            ("filter 越界", "layer", {"mi": 0, "li": 0, "filter": 9}),
            ("alpha 越界", "layer", {"mi": 0, "li": 0, "alpha": 3}),
            ("序列区间倒置", "seq", {"index": 1, "start": 500, "end": 100}),
        ]
        # 索引必须动态找：不同模型的可替换贴图位置不一样，写死会"合法地"改错一条
        ridx = next((t["index"] for t in texs if t["replaceableId"]), None)
        if ridx is not None:
            bads.append(("可替换贴图", "tex", {"index": ridx, "path": "a.dds"}))
        for label, kind, payload in bads:
            resp = cli.call("/api/edit", "POST", {"kind": kind, "payload": payload})
            check(f"拒绝：{label}", resp.get("ok") is False, str(resp.get("error"))[:48])

        print("\n保存：")
        saved = cli.call("/api/save", "POST", {})
        check("保存成功", saved.get("ok") is True, str(saved.get("error") or ""))
        rep = saved["report"]
        after = target.read_bytes()

        check("文件长度不变", len(after) == len(original),
              f"{len(original):,} → {len(after):,} B")
        check("备份存在", bool(rep.get("backup")) and pathlib.Path(rep["backup"]).is_file(),
              rep.get("backup") or "")
        if rep.get("backup"):
            check("备份与改前字节一致",
                  pathlib.Path(rep["backup"]).read_bytes() == original)

        # 核心断言：差异只落在被改字段内
        mdx = parse(original)
        ranges = diff_ranges(original, after)
        allowed = []
        allowed.append(ops.texture_spans_bulk(mdx, [2])[0])
        allowed.append(_span_abs(mdx, ops.layer_alpha_span(mdx, 0, 0)))
        allowed.append(_span_abs(mdx, ops.sequence_interval_span(mdx, 1)))
        allowed.append(_atch_path_span(mdx, 0))

        outside = [r for r in ranges
                   if not any(lo <= r.offset and r.offset + r.length <= hi
                              for lo, hi in allowed)]
        check("所有差异都落在已改字段内", not outside,
              f"{len(ranges)} 处差异，允许 {len(allowed)} 个范围")
        check("改动字节数合理（<2KB）",
              sum(r.length for r in ranges) < 2048,
              f"{sum(r.length for r in ranges)} B")

        print("\n保存后状态：")
        m2 = parse(after)
        check("模型仍可完整解析", m2.raw == after)
        check("保存后改动清零", rep["data"]["changes"] == [])
        check("回读贴图路径为新值",
              ops.list_textures(m2)[2].path == new_path,
              ops.list_textures(m2)[2].path)
        check("回读 alpha 为 0.5",
              abs(ops.list_materials(m2)[0].layers[0].alpha - 0.5) < 1e-6,
              str(ops.list_materials(m2)[0].layers[0].alpha))
        check("回读序列结束时间为 11800",
              ops.list_sequences(m2)[1].end == 11800,
              str(ops.list_sequences(m2)[1].end))

        print("\n重复保存与放弃：")
        empty = cli.call("/api/save", "POST", {})
        check("没有改动时保存被拒绝", empty.get("ok") is False, str(empty.get("error"))[:40])

        cli.call("/api/edit", "POST",
                 {"kind": "layer", "payload": {"mi": 1, "li": 0, "alpha": 0.25}})
        rv = cli.call("/api/revert", "POST", {})
        check("放弃改动后 dirty=False", rv["data"]["dirty"] is False)
        check("放弃后磁盘未变", target.read_bytes() == after)

    finally:
        srv.shutdown()

    print(f"\n结论：{passed} 项通过，{failed} 项失败")
    if failed == 0:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"（保留现场：{work}）")
    return 0 if failed == 0 else 1


def _span_abs(mdx, pair):
    ci, (lo, hi) = pair
    base = mdx.chunks[ci].data_offset
    return base + lo, base + hi


def _atch_path_span(mdx, index: int):
    from mdxcore.chunks import atch as A
    items = A.parse(mdx.data(mdx.find("ATCH")))
    base = mdx.chunks[[i for i, c in enumerate(mdx.chunks) if c.tag == "ATCH"][0]].data_offset
    a = items[index]
    return base + a.path_offset, base + a.path_offset + A.PATH_LEN


if __name__ == "__main__":
    raise SystemExit(main())
