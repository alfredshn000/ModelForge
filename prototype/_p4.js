const PORT = Number(process.env.CDP_PORT || 9488);
const URL = process.argv[2];
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });

  // 抓网络失败与 console
  const net = [];
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.method === "Network.loadingFailed") net.push("FAIL " + (m.params.errorText || "") + " " + m.params.type);
    if (m.method === "Network.responseReceived" && /three/.test(m.params.response.url))
      net.push("RESP " + m.params.response.status + " " + m.params.response.mimeType);
  });
  await send("Network.enable");
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: URL });
  await sleep(7000);

  console.log("网络事件:", JSON.stringify(net));

  const r = await send("Runtime.evaluate", {
    expression: `fetch("/static/vendor/three.min.js?token=" + encodeURIComponent(window.MDX_TOKEN))
      .then(r => r.text()).then(t => {
        try { (0, eval)(t); return "eval-ok:" + typeof window.THREE + " rev=" + (window.THREE && window.THREE.REVISION); }
        catch (e) { return "eval-err:" + e.message; }
      })`,
    returnByValue: true, awaitPromise: true,
  });
  console.log("eval 结果:", JSON.stringify(r.result && r.result.result && r.result.result.value));
  ws.close();
})();
