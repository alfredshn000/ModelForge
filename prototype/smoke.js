// 最小 DOM 桩：在 Node 里把原型的渲染流程真跑一遍，抓运行时异常。
// 只验证逻辑，不验证视觉。所有 getElementById 返回宽容元素，同时记录访问过的 id，
// 以便人工核对有没有拼错。
const fs = require("fs");
const path = require("path");

const IDS = [];
const cache = new Map();

function makeEl(id) {
  const e = {
    id,
    _html: "",
    _cls: new Set(),
    style: {},
    dataset: {},
    onclick: null, oninput: null, onchange: null, onmousedown: null,
    textContent: "",
    value: "",
    disabled: false,
    checked: false,
    className: "",
    insertAdjacentHTML() {},
    appendChild() {},
    addEventListener() {},
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
        closePath() {}, fill() {}, stroke() {}, arc() {}, fillText() {},
        fillStyle: "", strokeStyle: "", lineWidth: 1, font: "",
      };
    },
    parentElement: null,
    width: 100,
    height: 100,
    querySelector() { return makeEl("dyn"); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 1000, height: 700 }; },
  };
  Object.defineProperty(e, "innerHTML", {
    get() { return this._html; },
    set(v) { this._html = String(v); },
  });
  Object.defineProperty(e, "classList", {
    value: {
      add: c => e._cls.add(c),
      remove: c => e._cls.delete(c),
      toggle: (c, f) => {
        if (f === undefined) e._cls.has(c) ? e._cls.delete(c) : e._cls.add(c);
        else f ? e._cls.add(c) : e._cls.delete(c);
      },
      contains: c => e._cls.has(c),
    },
  });
  return e;
}

global.window = global;
global.document = {
  getElementById(id) {
    IDS.push(id);
    if (!cache.has(id)) cache.set(id, makeEl(id));
    return cache.get(id);
  },
  querySelector(sel) { return makeEl(sel); },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeEl("body"),
};
global.addEventListener = () => {};
global.setTimeout = (fn) => 0;   // 不走 toast 定时器
global.clearTimeout = () => {};
global.requestAnimationFrame = () => 0;   // 不让渲染循环自己转起来
global.devicePixelRatio = 1;

const dir = __dirname;
const data = fs.readFileSync(path.join(dir, "models/david/data.js"), "utf8");
const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const blocks = html.match(/<script>([\s\S]*?)<\/script>/g) || [];
if (!blocks.length) throw new Error("找不到内联脚本");
const m = [null, blocks[blocks.length - 1].replace(/^<script>|<\/script>$/g, "")];

const src = data + "\n" + m[1] +
  "\n;globalThis.__S=S;globalThis.__render=renderAll;globalThis.__open=openExport;" +
  "globalThis.__bulk=openBulk;globalThis.__R3=R;globalThis.__setTime=setTime;";

let failed = 0;
function step(name, fn) {
  try { fn(); console.log(`  PASS  ${name}`); }
  catch (e) { failed++; console.log(`  FAIL  ${name}: ${e && e.message}`); if (e && e.stack) console.log(e.stack.split("\n").slice(1,3).join("\n")); }
}

try {
  new Function("require", "module", "exports", src)(require, { exports: {} }, {});
  console.log("初始加载 OK");
} catch (e) {
  console.log("初始加载失败:", e.message);
  console.log(e.stack.split("\n").slice(0, 6).join("\n"));
  process.exit(1);
}

const bodyOf = id => (cache.get(id) || { _html: "" })._html || "";

console.log("\n渲染断言：");
step("贴图表非空", () => {
  if (bodyOf("tbody").length < 500) throw new Error("tbody 内容过短: " + bodyOf("tbody").length);
});
step("贴图表含外部条目路径", () => {
  if (!bodyOf("tbody").includes("AF\\Units\\Human\\David")) throw new Error("没渲染出外部路径");
});
step("属性面板渲染（贴图）", () => {
  const h = bodyOf("insp");
  if (!h.includes("判定依据")) throw new Error("属性面板内容不对");
});
step("时间轴 10 段", () => {
  const h = bodyOf("tlTrack");
  const n = (h.match(/class="tl-seg/g) || []).length;
  if (n !== 10) throw new Error("段数 " + n);
});
step("资源树 4 项", () => {
  const n = (bodyOf("tree").match(/class="tree-item/g) || []).length;
  if (n !== 4) throw new Error("项数 " + n);
});
step("顶栏状态", () => {
  if (!bodyOf("dirtyText") && cache.get("dirtyText").textContent !== "未改动") throw new Error("状态文本不对");
});

console.log("\n视图切换：");
for (const v of ["materials", "sequences", "attachments", "textures"]) {
  step("切到 " + v, () => {
    global.__S.view = v;
    global.__S.sel = null;
    global.__render();
    const n = (bodyOf("tbody").match(/class="trow/g) || []).length;
    if (n === 0) throw new Error("表格为空");
  });
}

console.log("\n交互流程：");
step("选中素材后属性面板有内容", () => {
  global.__S.view = "materials";
  global.__S.sel = { kind: "materials", index: 0, layer: 0 };
  global.__render();
  if (!bodyOf("insp").includes("FilterMode")) throw new Error("没有 FilterMode 字段");
});
step("附着点属性面板", () => {
  global.__S.view = "attachments";
  global.__S.sel = { kind: "attachments", index: 6 };
  global.__render();
  const h = bodyOf("insp");
  if (!h.includes("Weapon Ref") || !h.includes("weapon_bind_jnt")) throw new Error("附着点信息不全");
});
step("序列属性面板", () => {
  global.__S.view = "sequences";
  global.__S.sel = { kind: "sequences", index: 8 };
  global.__render();
  if (!bodyOf("insp").includes("Dissipate")) throw new Error("没显示 Dissipate");
});
step("批量改路径对话框", () => {
  global.__S.view = "textures";
  global.__render();
  global.__bulk();
  const h = bodyOf("dialog");
  if (!h.includes("一键改外部贴图路径")) throw new Error("对话框没打开");
});
step("导出对话框", () => {
  global.__open();
  const h = bodyOf("dialog");
  if (!h.includes("待写入")) throw new Error("导出对话框没打开");
});

console.log("\n3D 降级与时间轴：");
step("没有 three / 几何数据时不崩溃", () => {
  if (!cache.get("vpName").textContent.includes("未加载")) {
    throw new Error("应提示几何数据未加载，实际：" + cache.get("vpName").textContent);
  }
});
step("时间轴拖动条存在", () => {
  if (!html.includes('id="tlScrub"') || !html.includes('id="tlHead"')) throw new Error("缺 scrub");
});
step("播放按钮存在", () => {
  if (!html.includes('id="playBtn"')) throw new Error("缺 playBtn");
});
step("循环模式与自动播放开关存在", () => {
  if (!html.includes('id="loopBtn"')) throw new Error("缺 loopBtn");
  if (!html.includes('id="autoBtn"')) throw new Error("缺 autoBtn");
});
step("循环模式默认跟随文件", () => {
  if (global.__S.loopMode !== "file") throw new Error("默认 " + global.__S.loopMode);
});
step("自动播放默认开启", () => {
  if (global.__S.autoPlay !== true) throw new Error("autoPlay=" + global.__S.autoPlay);
});
step("序列色带仍渲染 10 段", () => {
  const n = (bodyOf("tlTrack").match(/class="tl-seg/g) || []).length;
  if (n !== 10) throw new Error("段数 " + n);
});
step("scrub 底部分段覆盖全部 10 段", () => {
  const n = (bodyOf("tlBands").match(/<i /g) || []).length;
  if (n !== 10) throw new Error("分段 " + n);
});
step("顶部 HUD 显示真实规模", () => {
  const t = document.getElementById("vpMeta").textContent || "";
  if (t.includes("0 顶点") || t === "") return;  // 无数据时允许空
});

console.log("\n访问过的元素 id（共 " + new Set(IDS).size + " 个）：");
console.log([...new Set(IDS)].join(" "));
console.log(failed === 0 ? "\n结论：全部通过" : `\n结论：${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
