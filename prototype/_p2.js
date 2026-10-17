const PORT = Number(process.env.CDP_PORT || 9480);
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
  await sleep(7000);
  const r = await send("Runtime.evaluate", {
    expression: `(() => {
      const th = [...document.querySelectorAll('.thumb')];
      const cv = document.getElementById('vp');
      const bb = cv ? cv.getBoundingClientRect() : null;
      return {
        thumbs: th.length,
        firstSrc: th[0] ? th[0].getAttribute('src') : null,
        firstState: th[0] ? [th[0].complete, th[0].naturalWidth] : null,
        okCount: th.filter(t => t.naturalWidth > 0).length,
        canvas: bb ? [Math.round(bb.width), Math.round(bb.height)] : null,
        buf: R && R.renderer ? [R.renderer.domElement.width, R.renderer.domElement.height] : null,
        meshes: R ? R.meshes.length : -1,
        dbg: window.MDX_DEBUG ? window.MDX_DEBUG.status : 'none',
        err: window.__bootErr,
        token: window.MDX_TOKEN,
        threeType: typeof THREE,
        threeGlobal: typeof window.THREE,
        scripts: [...document.querySelectorAll('script[src]')].map(x => x.getAttribute('src')),
      };
    })()`,
    returnByValue: true,
  });
  console.log(JSON.stringify(r.result.result ? r.result.result.value : r, null, 1).slice(0, 1600));
  ws.close();
})();
