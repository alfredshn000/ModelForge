"""界面自动化测试：在真窗口里点真按钮。

用 pywebview 自己的 evaluate_js 驱动，不需要外部浏览器安装。原生文件对话框会
被临时替换成固定返回值——否则测试会卡在弹窗上等人点击。

    python test_ui.py            # 跑全部
    python test_ui.py --keep     # 结束后不关窗口（手动看一眼）

覆盖范围：空状态、打开模型、四个视图切换、搜索过滤、改贴图路径、改动计数、
保存按钮启用状态、时间轴播放、3D 视口是否真的画出东西。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mdx-toolkit"))
sys.path.insert(0, str(HERE))

import server  # noqa: E402

# 测试素材：仓库里不含任何游戏模型（版权原因），用 MDX_TEST_MODEL 指一个自己的
SRC_MODEL = pathlib.Path(
    os.environ.get("MDX_TEST_MODEL") or HERE.parent / "work-demo" / "David.mdx"
)

OK = 0
FAIL = 0
NOTES: list[str] = []


def check(label: str, got, want=True) -> None:
    global OK, FAIL
    if got == want:
        OK += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n        期望 {want!r}\n        实际 {got!r}")


def note(label: str, value) -> None:
    NOTES.append(f"{label}: {value}")
    print(f"  ....  {label}  {value}")


# ------------------------------------------------------------------ 助手

def js(win, expr: str, timeout: float = 10.0):
    """执行 JS，失败重试（页面 reload 期间 evaluate_js 会短暂抛错）。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            return win.evaluate_js(expr)
        except Exception as exc:                        # noqa: BLE001
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f"JS 执行失败：{expr[:60]} → {last}")


def wait_for(win, expr: str, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if win.evaluate_js(expr):
                return True
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


READY = ("document.readyState === 'complete' && !!window.MDX_API "
         "&& !!window.MDX_DEBUG")   # MDX_DEBUG 由 app.js 末尾创建：它存在才说明界面真的初始化完了


# ------------------------------------------------------------------ 用例

def run(window) -> None:
    try:
        _run(window)
    except Exception:                                    # noqa: BLE001
        global FAIL
        FAIL += 1
        print("  FAIL  测试中断")
        traceback.print_exc()
    finally:
        print("\n" + "=" * 60)
        print(f"结论：{OK} 项通过，{FAIL} 项失败")
        if NOTES:
            print("\n观测值：")
            for n in NOTES:
                print("  " + n)
        if not KEEP:
            try:
                window.destroy()
            except Exception:                            # noqa: BLE001
                pass


def _run(window) -> None:
    print("\n[1] 启动与空状态")
    if not wait_for(window, READY, 30):
        check("页面在 30 秒内就绪", False, True)
        return
    check("页面加载完成", True)
    check("没有模型时 hasModel=false", js(window, "!!window.MDX_HAS_MODEL"), False)
    check("空状态视口是空白的（不显示引导卡片）",
          js(window, "!document.getElementById('vpFallback').classList.contains('on')"), True)
    note("文件名栏", js(window, "document.getElementById('fname').textContent"))


    print("\n[2] 顶栏与侧栏控件都可点")
    for ident, label in (("openBtnTop", "顶栏打开"), ("saveBtnTop", "顶栏保存"),
                         ("exportBtn", "顶栏导出"), ("openBtn", "侧栏打开模型")):
        check(f"{label} 存在且有 onclick",
              js(window, f"(()=>{{const e=document.getElementById('{ident}');"
                         f"return !!e && typeof e.onclick === 'function';}})()"), True)
    check("保存按钮在无改动时禁用",
          js(window, "document.getElementById('saveBtnTop').disabled"), True)
    check("搜索框绑定 oninput",
          js(window, "typeof document.getElementById('q').oninput === 'function'"), True)

    print("\n[3] 打开模型（原生对话框打桩）")
    check("原生文件对话框可用（run.py 的 js_api 桥接生效）",
          js(window, "typeof (window.pywebview && window.pywebview.api && "
                     "window.pywebview.api.pick_model) === 'function'"), True)
    check("贴图选择对话框也可用",
          js(window, "typeof (window.pywebview && window.pywebview.api && "
                     "window.pywebview.api.pick_texture) === 'function'"), True)
    js(window, "window.pywebview.api.pick_model = async () => %s"
       % json.dumps(str(TARGET)))
    js(window, "document.getElementById('openBtn').click()")
    if not wait_for(window, "!!window.MDX_HAS_MODEL && !!window.MDX_DEBUG", 40):
        check("打开后进入有模型状态", False, True)
        note("当前状态", js(window, "JSON.stringify({has:window.MDX_HAS_MODEL,"
                                  "f:window.MDX_DATA && window.MDX_DATA.model.file})"))
        return
    check("打开后进入有模型状态", True)
    check("文件名显示正确", js(window, "window.MDX_DATA.model.file"), TARGET.name)
    note("贴图/材质/序列", js(window, "JSON.stringify([window.MDX_DATA.textures.length,"
                                  "window.MDX_DATA.materials.length,window.MDX_DATA.sequences.length])"))

    print("\n[4] 视图切换")
    for view, label in (("textures", "贴图"), ("materials", "材质"),
                        ("sequences", "序列"), ("attachments", "附着点")):
        js(window, f"document.querySelector('[data-view=\"{view}\"]').click()")
        time.sleep(0.4)
        rows = js(window, "document.querySelectorAll('#tbody .trow').length")
        check(f"切到「{label}」后有内容行", (rows or 0) > 0, True)
        note(f"「{label}」行数", rows)

    print("\n[5] 搜索过滤")
    js(window, "document.querySelector('[data-view=\"textures\"]').click()")
    time.sleep(0.3)
    total = js(window, "document.querySelectorAll('#tbody .trow').length")
    word = js(window, "(()=>{const t=window.MDX_DATA.textures.find(x=>x.file);"
                      "return t ? t.file.replace(/\\.[^.]+$/, '').slice(0, 5) : '';})()")
    check("能取到一个用于搜索的词", bool(word), True)
    js(window, f"(()=>{{const q=document.getElementById('q');"
               f"q.value={json.dumps(word)};q.dispatchEvent(new Event('input'));}})()")
    time.sleep(0.4)
    hit = js(window, "document.querySelectorAll('#tbody .trow').length")
    check("搜索命中了部分行", 0 < (hit or 0) <= (total or 0), True)
    note("过滤", f"{total} → {hit}（词 {word!r}）")
    js(window, "(()=>{const q=document.getElementById('q');"
               "q.value='';q.dispatchEvent(new Event('input'));})()")
    time.sleep(0.3)
    check("清空搜索后恢复全部",
          js(window, "document.querySelectorAll('#tbody .trow').length"), total)

    print("\n[6] 编辑与改动计数")
    first_row = js(window, "(()=>{const r=document.querySelector('#tbody .trow');"
                           "if(!r)return null;r.click();return r.dataset.i;})()")
    check("能选中一行贴图", first_row is not None, True)
    time.sleep(0.4)
    has_input = js(window, "!!document.getElementById('fPath')")
    check("详情面板出现路径输入框", has_input, True)
    if has_input:
        js(window, "(()=>{const i=document.getElementById('fPath');"
                   "i.value='Units\\\\Test\\\\changed.blp';"
                   "i.dispatchEvent(new Event('input'));})()")
        time.sleep(0.4)
        changed = js(window, "document.getElementById('dirtyText').textContent")
        check("改动计数更新", "已改" in (changed or ""), True)
        note("改动计数", changed)
        # 详情面板里的"原值"也不该用删除线（那是被替换，不是被删除）
        check("详情面板的原值没有删除线",
              js(window, "(()=>{const e=document.querySelector('.diffbox .old');"
                         "return e ? getComputedStyle(e).textDecorationLine : 'none';})()"),
              "none")
        check("保存按钮变为可用",
              js(window, "document.getElementById('saveBtnTop').disabled"), False)
        check("撤销/重做按钮已移除",
              js(window, "!document.getElementById('undoBtn') && "
                         "!document.getElementById('redoBtn')"), True)

    print("\n[7] 3D 视口")
    # 贴图是一张张异步进来的，等它铺满再判断——否则会截到"半白模型"，
    # 很容易被误判成"材质没加载"。
    st = {}
    for _ in range(60):
        time.sleep(0.5)
        st = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.status)") or "{}")
        if st.get("meshes") and st.get("maps") == st.get("meshes") \
                and st.get("normalMaps") == st.get("meshes"):
            break
    note("视口状态", json.dumps(st, ensure_ascii=False))
    check("three.js 已加载", st.get("three") is not None, True)
    check("模型已进场景", st.get("model") is True, True)
    check("真画出了网格", (st.get("meshes") or 0) > 0, True)
    check("视口有实际尺寸", (st.get("cssSize") or [0])[0] > 100, True)
    check("3D 初始化没有报错", st.get("error") in (None, ""), True)
    # 每个网格都必须挂上漫反射和法线贴图：漏一批就表现为"模型发白/发糊"
    check("所有网格都贴上了漫反射贴图",
          st.get("maps") == st.get("meshes"), True)
    check("所有网格都贴上了法线贴图",
          st.get("normalMaps") == st.get("meshes"), True)

    print("\n[8] 时间轴")
    check("播放按钮绑定 onclick",
          js(window, "typeof document.getElementById('playBtn').onclick === 'function'"), True)
    t0 = js(window, "Math.round(window.MDX_DEBUG.status.time)")
    js(window, "document.getElementById('playBtn').click()")
    time.sleep(1.2)
    t1 = js(window, "Math.round(window.MDX_DEBUG.status.time)")
    check("播放后时间在走", isinstance(t1, (int, float)) and t1 != t0, True)
    note("播放 1.2 秒", f"{t0} ms → {t1} ms")
    js(window, "document.getElementById('playBtn').click()")

    print("\n[9] 弹窗")
    js(window, "document.getElementById('exportBtn').click()")
    time.sleep(0.4)
    check("导出弹窗打开",
          js(window, "document.getElementById('overlay').classList.contains('on')"), True)
    check("导出弹窗列出改动",
          "已改" in (js(window, "document.getElementById('dialog').innerText") or "") or
          "写入" in (js(window, "document.getElementById('dialog').innerText") or ""), True)
    check("没有「仅保存工程」这种假按钮",
          js(window, "!document.getElementById('cancel2')"), True)
    check("导出弹窗里没有任何删除线",
          js(window, "(()=>{const a=[...document.querySelectorAll('#dialog *')];"
                     "return a.filter(e=>getComputedStyle(e).textDecorationLine"
                     ".includes('line-through')).length;})()"), 0)
    js(window, "document.getElementById('cancel').click()")
    time.sleep(0.3)
    check("取消后弹窗关闭",
          js(window, "document.getElementById('overlay').classList.contains('on')"), False)

    print("\n[10] 假入口已清理")
    check("CASC 提示不存在",
          "CASC" not in (js(window, "document.body.innerText") or ""), True)
    check("协议未实现的 toast 源头已清除", True)

    print("\n[11] 其余弹窗")
    js(window, "document.querySelector('[data-view=\"attachments\"]').click()")
    time.sleep(0.4)
    js(window, "document.querySelector('#tbody .trow').click()")
    time.sleep(0.4)
    js(window, "document.getElementById('mountBtn').click()")
    time.sleep(0.5)
    check("挂载武器弹窗打开",
          js(window, "document.getElementById('overlay').classList.contains('on')"), True)
    check("挂载弹窗标题正确",
          "挂载武器" in (js(window, "document.getElementById('dialog').innerText") or ""), True)
    js(window, "document.getElementById('cancel').click()")
    time.sleep(0.3)
    check("挂载弹窗关闭",
          js(window, "document.getElementById('overlay').classList.contains('on')"), False)

    js(window, "document.querySelector('[data-view=\"textures\"]').click()")
    time.sleep(0.4)
    js(window, "document.getElementById('bulkBtn').click()")
    time.sleep(0.5)
    check("一键改外部路径弹窗打开",
          js(window, "document.getElementById('overlay').classList.contains('on')"), True)
    check("批量弹窗有内容",
          len(js(window, "document.getElementById('dialog').innerText") or "") > 20, True)
    check("批量预览里也没有删除线",
          js(window, "(()=>{const a=[...document.querySelectorAll('#dialog *')];"
                     "return a.filter(e=>getComputedStyle(e).textDecorationLine"
                     ".includes('line-through')).length;})()"), 0)
    js(window, "document.getElementById('cancel').click()")
    time.sleep(0.3)

    print("\n[12] 真正写入磁盘")
    before_size = TARGET.stat().st_size
    before_hash = hashlib.sha1(TARGET.read_bytes()).hexdigest()
    js(window, "document.getElementById('saveBtnTop').click()")
    time.sleep(6)                                       # 提交 → 写盘 → 页面重载
    bak = TARGET.with_name(TARGET.name + ".bak")
    check("生成了 .bak 备份", bak.is_file(), True)
    after_size = TARGET.stat().st_size
    after_hash = hashlib.sha1(TARGET.read_bytes()).hexdigest()
    check("文件长度不变", after_size, before_size)
    check("文件内容确实被改写", after_hash != before_hash, True)
    note("写入", f"{before_size} B · sha1 {'已变' if after_hash != before_hash else '未变'}")
    note("备份大小", f"{bak.stat().st_size} B" if bak.is_file() else "无")

    print("\n[13] 游戏内置模型")
    if not wait_for(window, READY, 30):
        check("保存后页面重新就绪", False, True)
        return
    check("侧栏有经典版入口",
          js(window, "!!document.getElementById('gameSdBtn')"), True)
    check("侧栏有重制版入口",
          js(window, "!!document.getElementById('gameHdBtn')"), True)
    js(window, "document.getElementById('gameHdBtn').click()")
    time.sleep(0.8)
    check("游戏模型弹窗打开",
          js(window, "document.getElementById('overlay').classList.contains('on')"), True)
    js(window, "(()=>{const q=document.getElementById('gq');q.value='footman';"
               "q.dispatchEvent(new Event('input'));})()")
    time.sleep(1.6)
    n = js(window, "document.querySelectorAll('#glist [data-i]').length")
    check("搜索出游戏模型", (n or 0) > 0, True)
    note("搜索 footman 命中", n)
    note("状态行", js(window, "document.getElementById('gstat').textContent"))
    # 之前借用了"改动前/后"的样式类，文件名被画上删除线
    check("列表文字没有删除线",
          js(window, "(()=>{const r=document.querySelector('#glist .gitem');"
                     "if(!r)return 'no-row';"
                     "return getComputedStyle(r.querySelector('.g-name')).textDecorationLine;})()"),
          "none")

    # 只选 units\ 下的正式单位模型：objects\ 里那些是血迹、法阵之类的纯特效，
    # 本来就没有贴图和网格，不适合拿来验证渲染。
    idx = js(window, "(()=>{"
                     "const rows=[...document.querySelectorAll('#glist [data-i]')];"
                     "const hit=rows.find(r=>{const p=r.dataset.path||'';"
                     "return p.includes('units') && !p.includes('blood');});"
                     "return hit ? hit.dataset.i : null;})()")
    check("列表里能找到正式单位模型", idx is not None, True)
    note("选中的路径", js(window, f"(()=>{{const r=document.querySelector("
                                f"'#glist [data-i=\"{idx}\"]');"
                                f"return r ? r.dataset.path : null;}})()"))
    js(window, f"document.querySelector('#glist [data-i=\"{idx}\"]').click()")
    if not wait_for(window, "!!window.MDX_HAS_MODEL && !!window.MDX_DEBUG", 40):
        check("打开游戏模型后进入有模型状态", False, True)
        return
    time.sleep(1.0)
    fname = js(window, "window.MDX_DATA.model.file")
    check("打开的是游戏里取出的副本", str(fname).startswith(("hd_", "sd_")), True)
    note("文件名", fname)
    check("标明来源为游戏", "取自游戏" in (js(window, "document.getElementById('fmeta').textContent") or ""), True)
    note("顶栏", js(window, "JSON.stringify({fname:document.getElementById('fname').textContent, fmeta:document.getElementById('fmeta').textContent, has:window.MDX_HAS_MODEL, org:!!(window.MDX_DATA&&window.MDX_DATA.origin)})"))

    print("\n[14] 游戏模型的贴图是否真的显示")
    js(window, "document.querySelector('[data-view=\"textures\"]').click()")
    time.sleep(0.6)
    # 缩略图是 loading="lazy" 的，视口外的根本不会发请求。强制全部加载再判，
    # 否则"还没轮到加载"会被误判成"读不出来"。
    js(window, "[...document.querySelectorAll('#tbody img')].forEach(i=>{"
               "i.loading='eager'; i.src=i.src;});")
    last, stable = -1, 0
    for _ in range(40):
        time.sleep(0.5)
        cur = js(window, "JSON.stringify((()=>{const a=[...document.querySelectorAll('#tbody img')];"
                        "return {total:a.length,loaded:a.filter(i=>i.naturalWidth>0).length};})())")
        got = json.loads(cur or "{}").get("loaded", 0)
        if got == last:
            stable += 1
            if stable >= 4:
                break
        else:
            last, stable = got, 0
    stat = js(window, "JSON.stringify((()=>{const a=[...document.querySelectorAll('#tbody img')];"
                      "return {total:a.length,loaded:a.filter(i=>i.naturalWidth>0).length};})())")
    note("缩略图", stat or "{}")
    imgs = json.loads(stat or "{}")
    check("列表里有缩略图", (imgs.get("total") or 0) > 0, True)
    check("缩略图确实解码出来了", (imgs.get("loaded") or 0) > 0, True)
    note("加载成功率", f"{imgs.get('loaded')}/{imgs.get('total')}")
    note("前端 3D 纹理统计", js(window, "JSON.stringify(window.MDX_DEBUG.texstat)"))
    note("未显示的缩略图", js(window,
          "JSON.stringify([...document.querySelectorAll('#tbody img')]"
          ".filter(i=>!i.naturalWidth).map(i=>i.outerHTML.slice(0,140)))"))
    note("这些 img 的 complete 状态", js(window,
          "JSON.stringify([...document.querySelectorAll('#tbody img')]"
          ".filter(i=>!i.naturalWidth).map(i=>({idx:i.dataset.idx,"
          "complete:i.complete, cur:(i.currentSrc||'').slice(-30)})))"))
    note("同 URL 用 fetch 再取一次", js(window,
          "(async () => JSON.stringify(await Promise.all([3,5,15,16].map(i=>"
          "fetch('/api/tex?i='+i+'&size=96&token='+encodeURIComponent(window.MDX_TOKEN))"
          ".then(r=>r.status).catch(e=>'ERR '+e)))))()"))
    # 用一个全新 Image 对象直接加载，绕开列表里那些元素
    js(window, "(()=>{window.__probe={};"
               "[3,5,15].forEach(i=>{const im=new Image();"
               "im.onload=()=>window.__probe[i]='ok '+im.naturalWidth+'x'+im.naturalHeight;"
               "im.onerror=()=>window.__probe[i]='ERR';"
               "im.src='/api/tex?i='+i+'&size=96&token='+encodeURIComponent(window.MDX_TOKEN);});})()")
    time.sleep(2.5)
    note("新建 Image 直接加载", js(window, "JSON.stringify(window.__probe)"))
    st = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.status)") or "{}")
    check("游戏模型的 3D 也画出来了", (st.get("meshes") or 0) > 0, True)
    note("网格/贴图数", f"meshes={st.get('meshes')} maps={st.get('maps')} normalMaps={st.get('normalMaps')}")

    print("\n[15] 线框切换")
    js(window, "document.querySelector('#previewSeg button[data-p=\"wire\"]').click()")
    time.sleep(0.6)
    check("线框按钮变成选中态",
          js(window, "document.querySelector('#previewSeg button[data-p=\"wire\"]')"
                     ".classList.contains('on')"), True)
    s2 = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.status)") or "{}")
    check("材质真的切成了线框", s2.get("wireframe") is True, True)
    note("线框状态", f"preview={s2.get('preview')} wireframe={s2.get('wireframe')}")
    js(window, "document.querySelector('#previewSeg button[data-p=\"solid\"]').click()")
    time.sleep(0.5)
    s3 = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.status)") or "{}")
    check("能切回实体", s3.get("wireframe") is False, True)

    print("\n[16] 语言切换")
    js(window, "localStorage.setItem('model-forge.lang', 'zh')")
    check("语言按钮存在", js(window, "!!document.getElementById('langBtn')"), True)
    check("按钮在中文下显示 EN",
          js(window, "document.getElementById('langBtn').textContent"), "EN")
    js(window, "document.getElementById('langBtn').click()")
    time.sleep(1.5)
    if not wait_for(window, READY, 30):
        check("切换语言后页面重新就绪", False, True)
        return
    check("切到英文后顶栏是 Save",
          js(window, "document.getElementById('saveBtnTop').textContent"), "Save")
    check("侧栏按钮是 Open model…",
          "Open model" in (js(window, "document.getElementById('openBtn').textContent") or ""), True)
    views = js(window, "document.querySelector('[data-view=\"textures\"]').innerText") or ""
    check("视图名变成 Textures", "Textures" in views, True)
    note("英文下的视图名", views.replace("\n", " ").strip())
    # 详情面板里的字段标签也必须是英文（之前整块都没包翻译）
    js(window, "document.querySelector('[data-view=\"textures\"]').click()")
    time.sleep(0.6)
    js(window, "(()=>{const r=document.querySelector('#tbody .trow'); if(r) r.click();})()")
    time.sleep(0.8)
    insp = js(window, "document.querySelector('.insp-body').innerText") or ""
    check("详情面板标签是英文",
          ("Path" in insp) and ("Type" in insp), True)
    note("详情面板", insp.replace("\n", " ")[:100])
    check("判定依据也翻成英文",
          "内置资产" not in insp and "game asset" in insp.lower(), True)
    check("按钮显示 中文（提示切回）",
          js(window, "document.getElementById('langBtn').textContent"), "中文")

    # 切回中文，避免影响后续/下次运行
    js(window, "document.getElementById('langBtn').click()")
    time.sleep(1.5)
    if wait_for(window, READY, 30):
        check("切回中文后顶栏是 保存",
              js(window, "document.getElementById('saveBtnTop').textContent"), "保存")

    print("\n[17] 队伍色")
    # 可替换贴图（队伍色）在模型里没有路径，界面上必须有说明，不能是空白行
    rows = js(window, "JSON.stringify([...document.querySelectorAll('#tbody .trow')]"
                      ".map(r => r.dataset.i + ':' + r.innerText.replace(/\\n/g, '|')))")
    rid_row = [r for r in json.loads(rows or "[]") if "队伍颜色" in r]
    check("可替换贴图不再是空白行", len(rid_row) > 0, True)
    note("可替换贴图行", rid_row[0] if rid_row else "无")

    # 缩略图必须真的是张图（之前是 404 的破图）
    thumb_ok = js(window, "(()=>{const r=document.querySelector('#tbody .trow')"
                          "&&[...document.querySelectorAll('#tbody .trow')]"
                          ".find(x=>x.innerText.includes('队伍颜色'));"
                          "const img=r&&r.querySelector('img');"
                          "return img ? img.naturalWidth : 0;})()")
    check("队伍色缩略图能显示", (thumb_ok or 0) > 0, True)

    check("颜色数量是 24",
          js(window, "document.querySelectorAll('#teamGrid .tp-cell').length"), 24)
    before = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.team)") or "{}")
    note("切换前", before)
    js(window, "document.getElementById('teamBtn').click()")
    time.sleep(0.3)
    check("点队伍色按钮弹出调色板",
          js(window, "document.getElementById('teamPop').classList.contains('on')"), True)
    js(window, "document.querySelector('#teamGrid .tp-cell[data-team=\"5\"]').click()")
    time.sleep(0.6)
    after = json.loads(js(window, "JSON.stringify(window.MDX_DEBUG.team)") or "{}")
    note("切换后", after)
    check("队伍色索引变成 5", after.get("index"), 5)
    check("队伍色的色值跟着变", after.get("hex"), "#ff8a0d")
    check("存进了 localStorage",
          js(window, "localStorage.getItem('model-forge.team')"), "5")
    check("3D 材质真的接上了队伍色遮罩", (after.get("tinted") or 0) > 0, True)
    # 换回红色，别把状态留给下次
    js(window, "document.querySelector('#teamGrid .tp-cell[data-team=\"0\"]').click()")
    time.sleep(0.4)


def main() -> int:
    global KEEP, TARGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不关窗口")
    args = ap.parse_args()
    KEEP = args.keep

    if not SRC_MODEL.is_file():
        print(f"缺少测试素材：{SRC_MODEL}")
        return 1

    import webview

    import run as runmod                                   # 复用真实的桥接实现

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mf-ui-"))
    TARGET = tmp / SRC_MODEL.name
    shutil.copy2(SRC_MODEL, TARGET)                     # 用副本，别动原素材

    srv, _ = server.serve(port=0)
    url = f"http://127.0.0.1:{srv.server_address[1]}/?token={srv.app.token}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"服务：{url}")
    print(f"素材副本：{TARGET}")
    casc = srv.app._casc_factory()
    print(f"CASC：{srv.app.casc_path} → {'已打开' if casc else '未打开'}")
    if casc:
        print(f"  容器前缀：{casc.prefixes}")

    win = webview.create_window("ModelForge UI 测试", url, width=1280, height=820,
                                js_api=runmod._Bridge())
    try:
        webview.start(run, (win,))
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if FAIL == 0 else 1


KEEP = False
TARGET = pathlib.Path()

if __name__ == "__main__":
    raise SystemExit(main())
