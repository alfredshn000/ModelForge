// UI 出问题时的诊断工具：抓 console / 异常 / 诊断状态 / 截图。
//
// 用法（Chrome 必须与诊断在同一条命令里启动，否则后台进程会被回收）：
//   rm -rf /c/temp/cdp; \
//   "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" \
//     --remote-debugging-port=9377 --user-data-dir="C://temp//cdp" \
//     --headless=new --enable-unsafe-swiftshader about:blank &
//   sleep 6 && node _diag.js
//
// 端口要跟上面一致（改下面 PORT）。
const http = require("http");
const fs = require("fs");

const PORT = Number(process.env.CDP_PORT || 9403);
const URL = process.argv[2] || "file:///C:/Users/alfre/Desktop/TEST%20APP/prototype/index.html";

function get(path) {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port: PORT, path }, r => {
      let d = "";
      r.on("data", c => (d += c));
      r.on("end", () => res(d));
    }).on("error", rej);
  });
}

(async () => {
  const list = JSON.parse(await get("/json/list"));
  const page = list.find(t => t.type === "page");
  if (!page) throw new Error("没有 page target");

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const logs = [];

  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) p.rej(new Error(JSON.stringify(m.error)));
      else p.res(m.result);
      return;
    }
    if (m.method === "Runtime.consoleAPICalled") {
      logs.push(`[${m.params.type}] ` + m.params.args.map(a =>
        a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" "));
    }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      logs.push(`[EXCEPTION] ${d.text} ${d.exception ? d.exception.description : ""}`);
    }
    if (m.method === "Log.entryAdded") {
      logs.push(`[log:${m.params.entry.level}] ${m.params.entry.text}`);
    }
  });

  await new Promise(r => ws.addEventListener("open", r));
  await send("Runtime.enable");
  await send("Log.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: URL });

  await new Promise(r => setTimeout(r, 6000));

  const st = await send("Runtime.evaluate", {
    expression: "JSON.stringify(window.MDX_DEBUG ? window.MDX_DEBUG.status : {noDebug:true})",
    returnByValue: true,
  });
  console.log("=== 诊断状态 ===");
  console.log(st.result.value);

  const fb = await send("Runtime.evaluate", {
    expression: "(document.getElementById('vpFallback')||{}).textContent || ''",
    returnByValue: true,
  });
  console.log("=== fallback 提示 ===");
  console.log(JSON.stringify(fb.result.value));

  console.log("=== console / 异常 ===");
  console.log(logs.join("\n") || "(无)");

  /* 自动播放验证：点第 2 段（Stand 1，循环），看时间是否推进 */
  const t = async () => {
    const r = await send("Runtime.evaluate", {
      expression: "window.MDX_DEBUG ? window.MDX_DEBUG.status.time : -1",
      returnByValue: true,
    });
    return r.result.value;
  };
  await send("Runtime.evaluate", {
    expression: "document.querySelectorAll('.tl-seg')[1].click()",
  });
  await new Promise(r => setTimeout(r, 250));
  const t1 = await t();
  await new Promise(r => setTimeout(r, 900));
  const t2 = await t();
  console.log("=== 自动播放 ===");
  console.log(`切到 Stand 1 后：${t1} → ${t2}  ${t2 > t1 ? "推进中 OK" : "未推进 FAIL"}`);

  /* 选中一张有缩略图的贴图，便于检查属性面板的大图 */
  await send("Runtime.evaluate", {
    expression: "(function(){var r=document.querySelectorAll('#tbody .trow');" +
      "if(r[10]) r[10].click();})()",
  });
  await new Promise(r => setTimeout(r, 600));
  /* 等贴图与缩略图加载完（87 张 256² PNG，首次约 1–2 秒） */
  await new Promise(r => setTimeout(r, 2500));

  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(__dirname + "/_shot.png", Buffer.from(shot.data, "base64"));
  console.log("截图: _shot.png", fs.statSync(__dirname + "/_shot.png").size, "B");

  ws.close();
  process.exit(0);
})().catch(e => { console.error("DIAG FAIL:", e.message); process.exit(1); });
