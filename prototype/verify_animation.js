// 骨架与蒙皮的数学验证（不需要 WebGL）。
// 直接跑 index.html 里的 applyBone / sampleLinear / sampleQuat 真实现，
// 只把 three 的数学类型和 DOM 桩喂给它。
const fs = require("fs");
const path = require("path");

const THREE = require("./vendor/three.min.js");

/* ---- DOM 桩（与 smoke.js 同款，只求不崩） ---- */
const cache = new Map();
function makeEl(id) {
  const e = {
    id, _html: "", _cls: new Set(), style: {}, dataset: {},
    onclick: null, oninput: null, onchange: null, onmousedown: null,
    textContent: "", value: "", disabled: false, checked: false, className: "",
    insertAdjacentHTML() {}, appendChild() {}, addEventListener() {},
    getContext() { return { setTransform(){}, clearRect(){}, beginPath(){}, moveTo(){},
      lineTo(){}, closePath(){}, fill(){}, stroke(){}, arc(){}, fillText(){} }; },
    querySelector() { return makeEl("dyn"); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { top:0, left:0, width:800, height:400 }; },
    parentElement: null, width: 800, height: 400,
  };
  Object.defineProperty(e, "innerHTML", { get(){return this._html;}, set(v){this._html=String(v);} });
  Object.defineProperty(e, "classList", { value: {
    add: c => e._cls.add(c), remove: c => e._cls.delete(c),
    toggle: (c, f) => { if (f === undefined) e._cls.has(c) ? e._cls.delete(c) : e._cls.add(c); else f ? e._cls.add(c) : e._cls.delete(c); },
    contains: c => e._cls.has(c),
  }});
  return e;
}
global.window = global;
global.document = {
  getElementById(id) { if (!cache.has(id)) cache.set(id, makeEl(id)); return cache.get(id); },
  querySelector(sel) { return makeEl(sel); },
  querySelectorAll() { return []; },
  addEventListener() {}, body: makeEl("body"),
};
global.addEventListener = () => {};
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.requestAnimationFrame = () => 0;
global.devicePixelRatio = 1;

/* ---- 载入数据与脚本 ---- */
const dir = __dirname;
const mjs = fs.readFileSync(path.join(dir, "models/david/model.js"), "utf8");
new Function("window", mjs + "\n;window.__MD=window.MDX_MODEL;")(global);
global.THREE = THREE;

const dataJs = fs.readFileSync(path.join(dir, "models/david/data.js"), "utf8");
new Function("window", dataJs + "\n;window.__DD=window.MDX_DATA;")(global);

const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const blocks = html.match(/<script>([\s\S]*?)<\/script>/g) || [];
const inline = blocks[blocks.length - 1].replace(/^<script>|<\/script>$/g, "")
  // 不要建 WebGL 场景：这里只验证数学
  .replace(/\ninitViewport\(\);/, "\n/* initViewport 由验证脚本自行接管 */");
new Function("require", "module", "exports",
  inline + "\n;globalThis.__R=R;globalThis.__S=S;globalThis.__applyBone=applyBone;" +
  "globalThis.__initTemps=initTemps;globalThis.__sampleLinear=sampleLinear;" +
  "globalThis.__setTime=setTime;globalThis.__TRK=TRK;" +
  "globalThis.__advance=advance;globalThis.__modeOf=modeOf;"
)(require, { exports: {} }, {});

const MD = global.__MD;
const R = global.__R;
const DD = global.__DD;

let failed = 0, passed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  PASS  ${name}`); }
  else { failed++; console.log(`  FAIL  ${name}${detail ? " — " + detail : ""}`); }
}

/* ---- 1. 数据完整性 ---- */
console.log("数据包：");
check("geoset 数量 28", MD.geosets.length === 28, String(MD.geosets.length));
check("顶点总数 17320", MD.stats.verts === 17320, String(MD.stats.verts));
check("三角总数 25023", MD.stats.tris === 25023, String(MD.stats.tris));
check("骨骼 120", MD.stats.bones === 120, String(MD.stats.bones));
check("轨道 148 / 关键帧 31718",
  MD.tracks.length === 148 && MD.stats.keys === 31718,
  `${MD.tracks.length} / ${MD.stats.keys}`);

function dec(str, T) {
  const bin = Buffer.from(str, "base64");
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin[i];
  return new T(u8.buffer);
}

let idxOk = 0, wsumOk = 0, jntOk = 0, totalV = 0;
for (const g of MD.geosets) {
  const idx = dec(g.idx, Uint16Array);
  const wgt = dec(g.wgt, Float32Array);
  const jnt = dec(g.jnt, Uint16Array);
  if (idx.length === g.nt * 3 && Math.max(...idx) < g.nv) idxOk++;
  let bad = 0;
  for (let v = 0; v < g.nv; v++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += wgt[v * 4 + k];
    if (Math.abs(s - 1) > 1e-4) bad++;
  }
  if (bad === 0) wsumOk++;
  if (Math.max(...jnt) < MD.bones.length) jntOk++;
  totalV += g.nv;
}
check("索引全部在顶点范围内", idxOk === 28, `${idxOk}/28`);
check("每顶点权重和 = 1", wsumOk === 28, `${wsumOk}/28`);
check("蒙皮索引全部指向合法骨骼", jntOk === 28, `${jntOk}/28`);
check("顶点数累加一致", totalV === 17320, String(totalV));

/* ---- 2. 骨骼层级 ---- */
console.log("\n骨骼层级：");
const bones = [];
for (const b of MD.bones) {
  const bn = new THREE.Bone();
  bn.name = b.n;
  bn.userData.pv = b.pv;
  bones[b.id] = bn;
}
let parentOk = 0, selfLoop = 0;
for (const b of MD.bones) {
  if (b.p < 0) { parentOk++; continue; }
  if (b.p === b.id) { selfLoop++; continue; }
  if (b.p < MD.bones.length && bones[b.id + 0]) parentOk++;
}
check("父节点全部合法且无自环", parentOk === MD.bones.length && selfLoop === 0,
  `ok=${parentOk} self=${selfLoop}`);
const roots = MD.bones.filter(b => b.p < 0);
check("根骨骼数量合理（多根是合法的：武器/书本各自独立绑骨）",
  roots.length >= 1 && roots.length <= 6, roots.map(r => r.n).join(","));

/* ---- 3. bind pose 必须是单位阵 ---- */
console.log("\n骨骼矩阵：");
const root = new THREE.Object3D();
for (const b of MD.bones) {
  if (b.p >= 0 && bones[b.p]) bones[b.p].add(bones[b.id]);
  else root.add(bones[b.id]);
}
const I = new THREE.Matrix4();
let maxDev = 0;
for (const b of MD.bones) {
  const pv = b.pv;
  const T1 = new THREE.Matrix4().makeTranslation(pv[0], pv[1], pv[2]);
  const T2 = new THREE.Matrix4().makeTranslation(-pv[0], -pv[1], -pv[2]);
  const RS = new THREE.Matrix4().compose(
    new THREE.Vector3(), new THREE.Quaternion(), new THREE.Vector3(1, 1, 1));
  const m = T1.multiply(RS).multiply(T2);
  bones[b.id].matrix.copy(m);
  bones[b.id].matrixAutoUpdate = false;
}
root.updateMatrixWorld(true);
for (const b of MD.bones) {
  const e = bones[b.id].matrixWorld.elements;
  for (let k = 0; k < 16; k++) {
    const want = (k % 5 === 0) ? 1 : 0;
    maxDev = Math.max(maxDev, Math.abs(e[k] - want));
  }
}
check("bind pose 下 120 根骨骼全为单位阵", maxDev < 1e-5, `最大偏差 ${maxDev.toExponential(2)}`);

/* Skeleton 的 boneInverses 必须全为单位阵，否则蒙皮整体错位 */
{
  const sk = new THREE.Skeleton(bones);
  let dev = 0;
  for (const m of sk.boneInverses) {
    const e = m.elements;
    for (let k = 0; k < 16; k++) dev = Math.max(dev, Math.abs(e[k] - (k % 5 === 0 ? 1 : 0)));
  }
  check("Skeleton.boneInverses 全为单位阵", dev < 1e-5, `最大偏差 ${dev.toExponential(2)}`);
}

/* ---- 4. 动画采样：无 NaN、幅度合理 ---- */
console.log("\n动画采样：");
global.__initTemps();
R.bones = bones;
R.group = root;
R.skel = new THREE.Skeleton(bones);

const times = [9200, 10000, 11867, 17000, 22000, 26133, 28133, 28999];
let nanCount = 0, maxCoord = 0, minY = 1e9, maxY = -1e9;
for (const t of times) {
  global.__setTime(t);
  root.updateMatrixWorld(true);
  for (const b of MD.bones) {
    const e = bones[b.id].matrixWorld.elements;
    for (let k = 0; k < 16; k++) {
      if (!Number.isFinite(e[k])) nanCount++;
      if (k === 12 || k === 13 || k === 14) maxCoord = Math.max(maxCoord, Math.abs(e[k]));
    }
  }
}
check("所有时刻矩阵无 NaN/Infinity", nanCount === 0, `${nanCount} 个非法值`);
check("骨骼位移在合理范围（< 500 单位）", maxCoord < 500, `最大 ${maxCoord.toFixed(1)}`);

/* 蒙皮后的顶点是否还在模型尺度内 */
const g0 = MD.geosets[0];
const pos0 = dec(g0.pos, Float32Array);
const jnt0 = dec(g0.jnt, Uint16Array);
const wgt0 = dec(g0.wgt, Float32Array);
let minY2 = 1e9, maxY2 = -1e9, badSkin = 0;
const v4 = new THREE.Vector4();
for (let i = 0; i < g0.nv; i += 7) {
  const p = new THREE.Vector4(pos0[i*3], pos0[i*3+1], pos0[i*3+2], 1);
  const acc = new THREE.Vector4(0, 0, 0, 0);
  for (let k = 0; k < 4; k++) {
    const w = wgt0[i*4+k];
    if (w === 0) continue;
    const m = bones[jnt0[i*4+k]].matrixWorld;
    v4.copy(p).applyMatrix4(m).multiplyScalar(w);
    acc.add(v4);
  }
  for (const c of [acc.x, acc.y, acc.z]) if (!Number.isFinite(c)) badSkin++;
  minY2 = Math.min(minY2, acc.z); maxY2 = Math.max(maxY2, acc.z);
}
check("蒙皮后顶点无非法值", badSkin === 0, `${badSkin}`);
check("蒙皮后高度仍在 0–120（Z 轴）", minY2 > -60 && maxY2 < 180,
  `z ∈ [${minY2.toFixed(1)}, ${maxY2.toFixed(1)}]`);

/* ---- 5. 采样与原始关键帧一致 ---- */
console.log("\n采样精度：");
let exact = 0, sampled = 0;
for (const trk of MD.tracks) {
  if (trk.n < 2) continue;
  const k = dec(trk.k, Float32Array);
  const stride = trk.s || 4;
  const mid = (trk.n / 2) | 0;
  const t = k[mid * stride];
  const out = [0, 0, 0];
  global.__sampleLinear(k, trk.n, stride, t, out);
  sampled++;
  if (Math.abs(out[0] - k[mid*stride+1]) < 1e-4 &&
      Math.abs(out[1] - k[mid*stride+2]) < 1e-4 &&
      Math.abs(out[2] - k[mid*stride+3]) < 1e-4) exact++;
}
check(`关键帧时刻采样必须命中原值（${sampled} 条轨道）`, exact === sampled, `${exact}/${sampled}`);

/* ---- 6. 相机取景：模型必须落在画面内 ---- */
console.log("\n取景：");
{
  const W = 900, H = 430;
  const cam = new THREE.PerspectiveCamera(40, W / H, 0.5, 3000);
  cam.up.set(0, 0, 1);
  const yaw = Math.PI / 2, pitch = 0.12, dist = 260;
  const target = new THREE.Vector3(0, 0, 55);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  cam.position.set(
    target.x + Math.sin(yaw) * cp * dist,
    target.y + Math.cos(yaw) * cp * dist,
    target.z + sp * dist
  );
  cam.lookAt(target);
  cam.updateMatrixWorld();
  cam.updateProjectionMatrix();

  let minX = 9, maxX = -9, minY = 9, maxY = -9, n = 0;
  for (const g of MD.geosets) {
    const pos = dec(g.pos, Float32Array);
    for (let i = 0; i + 2 < pos.length; i += 3 * 53) {
      const v = new THREE.Vector3(pos[i], pos[i + 1], pos[i + 2]).project(cam);
      minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x);
      minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
      n++;
    }
  }
  const hFrac = (maxY - minY) / 2, wFrac = (maxX - minX) / 2;
  console.log(`  NDC x∈[${minX.toFixed(2)}, ${maxX.toFixed(2)}]  y∈[${minY.toFixed(2)}, ${maxY.toFixed(2)}]  采样 ${n} 点`);
  check("模型水平方向在画面内", minX > -1 && maxX < 1, `x∈[${minX.toFixed(2)},${maxX.toFixed(2)}]`);
  check("模型垂直方向在画面内", minY > -1 && maxY < 1, `y∈[${minY.toFixed(2)},${maxY.toFixed(2)}]`);
  check("画面占比合理（高度 25%–100%）", hFrac > 0.25 && hFrac <= 1.0, `占 ${(hFrac*100).toFixed(0)}%`);
  check("模型居中（|中心| < 0.35）",
    Math.abs((minX + maxX) / 2) < 0.35 && Math.abs((minY + maxY) / 2) < 0.35,
    `中心 (${((minX+maxX)/2).toFixed(2)}, ${((minY+maxY)/2).toFixed(2)})`);
}

/* ---- 7. 序列覆盖 ---- */
console.log("\n时间轴：");
const covered = DD.sequences.every(s => s.start < s.end && s.end <= 28999);
check("10 段序列区间合法", covered && DD.sequences.length === 10);
check("序列无重叠", DD.sequences.every((s, i) =>
  i === 0 || s.start >= DD.sequences[i-1].end), "按顺序检查");

/* ---- 8. 播放推进（循环 / 单次 / 往返） ---- */
console.log("\n播放推进：");
{
  const adv = global.__advance;
  const seq = { start: 9200, end: 11867, loop: true };   // Stand 1，dur 2667
  const onceSeq = { start: 7867, end: 9100, loop: false }; // Attack 1

  let r = adv(11800, 100, seq, "loop", 1);
  check("loop：越界回卷到 start+余数", Math.abs(r.t - 9233) < 0.5 && !r.stop,
    `t=${r.t.toFixed(1)} stop=${r.stop}`);

  r = adv(9200, 10000, seq, "loop", 1);
  check("loop：大跨度跨多圈仍落在区间内",
    r.t >= seq.start && r.t <= seq.end, `t=${r.t.toFixed(1)}`);

  r = adv(11800, 100, onceSeq, "once", 1);
  check("once：停在末尾并通知停止", r.t === onceSeq.end && r.stop === true,
    `t=${r.t} stop=${r.stop}`);

  r = adv(11800, 100, seq, "pingpong", 1);
  check("pingpong：撞到末尾后反射且方向翻转",
    Math.abs(r.t - 11834) < 0.5 && r.dir === -1, `t=${r.t.toFixed(1)} dir=${r.dir}`);

  r = adv(9210, 50, seq, "pingpong", -1);
  check("pingpong：撞到起点后反射且方向翻回",
    Math.abs(r.t - 9240) < 0.5 && r.dir === 1, `t=${r.t.toFixed(1)} dir=${r.dir}`);

  r = adv(9200, 0, seq, "loop", 1);
  check("dt=0 不移动", Math.abs(r.t - 9200) < 0.01, `t=${r.t}`);

  /* 帧率抖动：每帧 16ms 连推 200 帧，任何时刻都必须落在序列内 */
  let t = seq.start, bad = 0;
  for (let i = 0; i < 200; i++) {
    const q = adv(t, 16 + (i % 7), seq, "pingpong", 1);
    t = q.t;
    if (t < seq.start - 0.01 || t > seq.end + 0.01) bad++;
  }
  check("pingpong：连续 200 帧不越界", bad === 0, `${bad} 帧越界`);

  /* modeOf：file 模式跟随文件标记 */
  global.__S.loopMode = "file";
  check("modeOf：file + 循环序列 → loop", global.__modeOf(seq) === "loop");
  check("modeOf：file + 单次序列 → once", global.__modeOf(onceSeq) === "once");
  global.__S.loopMode = "pingpong";
  check("modeOf：手动覆盖优先于文件标记", global.__modeOf(seq) === "pingpong");
  global.__S.loopMode = "file";
}

console.log(`\n结论：${passed} 项通过，${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
