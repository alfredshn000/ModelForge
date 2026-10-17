/* 端到端：在真实页面上改一条贴图路径 → 点导出 → 点写入，然后由 Node 检查磁盘。
   用法：CDP_PORT=9xxx node test_e2e.js <url> <mdx路径> */
const fs = require("fs");
const PORT = Number(process.env.CDP_PORT || 9490);
const URL = process.argv[2];
const MDX = process.argv[3];
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const before = fs.readFileSync(MDX);
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: URL });
  await sleep(7000);

  const ev = async (expr, awaitP) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: !!awaitP });
    if (r.result && r.result.exceptionDetails) return "EXC:" + r.result.exceptionDetails.text;
    return r.result && r.result.result ? r.result.result.value : null;
  };

  console.log("初始 dirty =", await ev("window.MDX_DATA.dirty"));

  // 1) 选中贴图 10（David_Helm_Diffuse），改路径
  const edit = await ev(`(async () => {
    const rows = document.querySelectorAll('.trow');
    const row = rows[10];
    if (!row) return "no-row";
    row.click();
    await new Promise(r => setTimeout(r, 300));
    const inp = document.getElementById('fPath');
    if (!inp) return "no-input";
    inp.value = "war3mapImported\\\\David_Helm_Diffuse.dds";
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise(r => setTimeout(r, 300));
    return [inp.value, S.changes.size];
  })()`, true);
  console.log("改动后 =", JSON.stringify(edit));

  // 2) 打开导出对话框
  const dlg = await ev(`(async () => {
    document.getElementById("exportBtn").click();
    await new Promise(r => setTimeout(r, 400));
    const b = document.getElementById("apply");
    return b ? b.textContent : "no-apply";
  })()`, true);
  console.log("导出对话框 =", JSON.stringify(dlg));

  // 3) 点写入
  const clicked = await ev(`(async () => {
    const b = document.getElementById("apply");
    if (!b) return "no-btn";
    b.click();
    await new Promise(r => setTimeout(r, 2500));
    return document.getElementById("toast").textContent;
  })()`, true);
  console.log("写入结果 toast =", JSON.stringify(clicked));

  await sleep(500);
  const after = fs.readFileSync(MDX);
  const changed = !before.equals(after);
  console.log(`磁盘文件：${before.length} → ${after.length} B，${changed ? "已改变" : "未改变"}`);

  // 统计差异范围
  let diff = 0, first = -1, last = -1;
  for (let i = 0; i < Math.min(before.length, after.length); i++) {
    if (before[i] !== after[i]) { diff++; if (first < 0) first = i; last = i; }
  }
  console.log(`字节差异 ${diff} 处，范围 ${first}–${last}`);

  // 备份是否存在
  const bak = MDX + ".bak";
  console.log("备份存在 =", fs.existsSync(bak),
    fs.existsSync(bak) ? `（${fs.statSync(bak).size} B，与原文件一致=${fs.readFileSync(bak).equals(before)}）` : "");
  ws.close();
})();
