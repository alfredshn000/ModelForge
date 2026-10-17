const PORT = Number(process.env.CDP_PORT || 9484);
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
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: URL });
  await sleep(6000);

  const probe = (expr, awaitP) => send("Runtime.evaluate", {
    expression: expr, returnByValue: true, awaitPromise: !!awaitP,
  }).then(r => ({ ok: r.result && !r.result.exceptionDetails, v: r.result && r.result.result && r.result.result.value,
                  err: r.result && r.result.exceptionDetails && r.result.exceptionDetails.text }));

  // 手动重新加载一次 three，看 onload / onerror
  const a = await probe(`new Promise(res => {
    window.__t3 = "pending";
    const s = document.createElement("script");
    s.src = "/static/vendor/three.min.js?token=" + encodeURIComponent(window.MDX_TOKEN) + "&r=" + Math.random();
    s.onload = () => res("loaded:" + typeof window.THREE);
    s.onerror = () => res("error");
    document.head.appendChild(s);
    setTimeout(() => res("timeout:" + typeof window.THREE), 5000);
  })`, true);
  console.log("手动加载 three →", JSON.stringify(a));

  const b = await probe(`fetch("/static/vendor/three.min.js?token=" + encodeURIComponent(window.MDX_TOKEN))
    .then(r => r.text()).then(t => [t.length, t.slice(0, 60)])`, true);
  console.log("fetch 内容 →", JSON.stringify(b).slice(0, 200));
  ws.close();
})();
