"use strict";
const tr = (window.MF_I18N && window.MF_I18N.t) || (s => s);
const trReason = (window.MF_I18N && window.MF_I18N.reason) || (s => s);
window.__bootErr = null;
function showFatal(msg, detail) {
  const box = document.getElementById("vpFallback");
  if (!box) return;
  box.classList.add("on");
  box.classList.remove("banner");
  box.innerHTML = '<div class="fb-card"><b>' + msg + "</b>" +
    (detail ? "<span>" + detail + "</span>" : "") + "</div>";
}
window.addEventListener("error", e => {
  window.__bootErr = (e.message || "unknown") + " @ " + (e.filename || "") + ":" + (e.lineno || 0);
  showFatal(tr("3D 初始化失败"), window.__bootErr);
});
/* 还没打开模型时的空状态：给一条明确的下一步，而不是一片黑 */
const D = window.MDX_DATA;
const HAS_MODEL = !!window.MDX_HAS_MODEL && !D.empty;
const TOTAL = D.sequences.length ? Math.max(...D.sequences.map(s => s.end)) : 0;

/* ---------------- 状态 ---------------- */
const S = {
  view: "textures",
  filter: "all",
  query: "",
  sel: null,            // {kind, index, layer?}
  changes: new Map(),   // key -> {scope, label, from, to, chunk}
  preview: "solid",
  loopMode: "file",      // file | loop | once | pingpong
  autoPlay: true,        // 切换动画后自动播放
  seq: 1,
  locks: new Set(),
  fast: { },
  /* 队伍色：WC3 的 24 个玩家颜色，00 红 / 01 蓝 …。存在 localStorage，
     下次打开还是这个颜色——调模型时基本固定看一个色。 */
  team: (() => {
    const v = parseInt(localStorage.getItem("model-forge.team") || "0", 10);
    return Number.isFinite(v) && v >= 0 && v < 24 ? v : 0;
  })(),
};
const TEAM_COLORS = [
  "#ff0303", "#0042ff", "#1ce6b9", "#540081", "#fffc01", "#fe8a0e",
  "#20c000", "#e55bb0", "#959697", "#7ebff1", "#106246", "#4e2a04",
  "#9b0000", "#0000c3", "#00eaff", "#be00fe", "#ebcd87", "#f8a48b",
  "#bfff80", "#dcb9eb", "#4f4f55", "#ebf0ff", "#00781d", "#a46f33",
];

const FILTERS = ["None","Transparent","Blend","Additive","AddAlpha","Modulate","Modulate2x"];
const CLS_LABEL = { builtin:tr("内置"), external:tr("外部"), replaceable:tr("可替换") };
const CLS_CLASS = { builtin:"blt", external:"ext", replaceable:"rpl" };

const el = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtB = n => n > 1048576 ? (n/1048576).toFixed(2)+" MB" : (n/1024).toFixed(0)+" KB";
const texturePath = i => (D.textures[i] && D.textures[i].path) || "";
const textureOf = i => D.textures[i] || null;

/* ---------------- 改动记录 ---------------- */
function setChange(key, scope, label, from, to, chunk) {
  if (String(from) === String(to)) S.changes.delete(key);
  else S.changes.set(key, { scope, label, from, to, chunk });
  renderStatus(); renderTable(); renderInspector(); renderTimeline();
}
function changedKeysFor(kind) {
  return [...S.changes.values()].filter(c => c.scope === kind).length;
}

/* ---------------- 顶栏 ---------------- */
function renderStatus() {
  const n = S.changes.size;
  const chip = el("dirtyChip"), txt = el("dirtyText");
  chip.classList.toggle("zero", n === 0);
  txt.textContent = n === 0 ? tr("未改动") : tr("已改 {n} 处", { n: n });
  /* 没有改动时保存没有意义，直接禁用，避免点了报错 */
  if (el("saveBtnTop")) el("saveBtnTop").disabled = n === 0;
}

/* ---------------- 资源树 ---------------- */
function renderTree() {
  const items = [
    ["textures", tr("贴图"), "▦", D.textures.length],
    ["materials", tr("材质"), "◧", D.materials.length],
    ["sequences", tr("序列"), "▤", D.sequences.length],
    ["attachments", tr("附着点"), "◇", D.attachments.length],
  ];
  el("tree").innerHTML = items.map(([k, label, ico, n]) => {
    const ch = changedKeysFor(k);
    return `<div class="tree-item ${S.view===k?"on":""}" data-view="${k}">
      <span class="ico">${ico}</span><span class="lbl">${label}</span>
      <span class="cnt">${n}${ch?`·${ch}`:""}</span></div>`;
  }).join("");
  el("tree").querySelectorAll(".tree-item").forEach(node => {
    node.onclick = () => { S.view = node.dataset.view; S.sel = null; S.filter="all"; S.query=""; el("q").value=""; renderAll(); };
  });
}

/* ---------------- 3D 视口：three.js 真几何 + 骨骼动画 ---------------- */
const T3 = window.THREE;
const MD = window.MDX_MODEL;

const R = {
  renderer: null, scene: null, camera: null, group: null,
  bones: [], skel: null, meshes: [],
  yaw: Math.PI / 2, pitch: 0.12, dist: 260, panX: 0, panY: 0,
  target: null, time: 0, playing: false, dir: 1, dirty: true,
  distMin: 90, distMax: 900, dist0: 260,
  drag: null, needResize: true,
};

/* 解码 base64 → 类型化数组 */
function dec64(str, Type) {
  const bin = atob(str);
  const n = bin.length;
  const buf = new Uint8Array(n);
  for (let i = 0; i < n; i++) buf[i] = bin.charCodeAt(i);
  return new Type(buf.buffer);
}

/* ---- 动画轨道预处理：按骨骼归组 ---- */
const TRK = [];          // TRK[boneIndex] = {0:transl 1:rot 2:scale}
if (MD) {
  for (const t of MD.tracks) {
    (TRK[t.b] || (TRK[t.b] = {}))[t.t] = {
      k: dec64(t.k, Float32Array), n: t.n, s: t.s || 4,
    };
  }
}

function sampleLinear(k, n, stride, t, out) {
  const last = (n - 1) * stride;
  if (t <= k[0]) { out[0] = k[1]; out[1] = k[2]; out[2] = k[3]; return out; }
  if (t >= k[last]) { out[0] = k[last+1]; out[1] = k[last+2]; out[2] = k[last+3]; return out; }
  let lo = 0, hi = n - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (k[mid * stride] <= t) lo = mid; else hi = mid;
  }
  const a = lo * stride, b = hi * stride;
  const t0 = k[a], t1 = k[b];
  const f = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
  out[0] = k[a+1] + (k[b+1] - k[a+1]) * f;
  out[1] = k[a+2] + (k[b+2] - k[a+2]) * f;
  out[2] = k[a+3] + (k[b+3] - k[a+3]) * f;
  return out;
}

let _qa = null, _qb = null;
function sampleQuat(k, n, stride, t, out) {
  const last = (n - 1) * stride;
  if (t <= k[0]) { out.set(k[1], k[2], k[3], k[4]); return safeNorm(out); }
  if (t >= k[last]) { out.set(k[last+1], k[last+2], k[last+3], k[last+4]); return safeNorm(out); }
  let lo = 0, hi = n - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (k[mid * stride] <= t) lo = mid; else hi = mid;
  }
  const a = lo * stride, b = hi * stride;
  const t0 = k[a], t1 = k[b];
  const f = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
  // MDX 的 KGRT 是 (x,y,z,w)
  _qa.set(k[a+1], k[a+2], k[a+3], k[a+4]);
  _qb.set(k[b+1], k[b+2], k[b+3], k[b+4]);
  return safeNorm(out.copy(_qa).slerp(_qb, f));
}

/* 数据异常（零四元数）会让矩阵塌成 0，这里退回单位四元数 */
function safeNorm(q) {
  const len = Math.hypot(q.x, q.y, q.z, q.w);
  if (!(len > 1e-6)) { q.set(0, 0, 0, 1); return q; }
  return q.normalize();
}

/* ---- 单根骨骼的局部矩阵：T(pivot+trans) · R · S · T(-pivot) ----
   依据 Retera 的 MathUtils.fromRotationTranslationScaleOrigin（m30 = v.x+pivot.x − R·pivot）*/
let _mRS = null, _mT1 = null, _mT2 = null, _vZero = null, _vScale = null, _q = null;
const _tr = [0, 0, 0], _sc = [1, 1, 1];

function applyBone(i, timeMs) {
  const bone = R.bones[i];
  const src = TRK[i];
  _tr[0] = _tr[1] = 0; _tr[2] = 0;
  _sc[0] = _sc[1] = _sc[2] = 1;
  _q.set(0, 0, 0, 1);
  if (src) {
    if (src[0]) sampleLinear(src[0].k, src[0].n, src[0].s, timeMs, _tr);
    if (src[1]) sampleQuat(src[1].k, src[1].n, src[1].s, timeMs, _q);
    if (src[2]) sampleLinear(src[2].k, src[2].n, src[2].s, timeMs, _sc);
  }
  const pv = bone.userData.pv;
  _mT1.makeTranslation(pv[0] + _tr[0], pv[1] + _tr[1], pv[2] + _tr[2]);
  _vScale.set(_sc[0], _sc[1], _sc[2]);
  _mRS.compose(_vZero, _q, _vScale);
  _mT2.makeTranslation(-pv[0], -pv[1], -pv[2]);
  bone.matrix.copy(_mT1).multiply(_mRS).multiply(_mT2);
  bone.matrixAutoUpdate = false;
}

function setTime(ms) {
  R.time = Math.max(0, Math.min(TOTAL, ms));
  if (!R.group) return;
  for (let i = 0; i < R.bones.length; i++) applyBone(i, R.time);
  R.group.updateMatrixWorld(true);
  if (R.skel) R.skel.update();
  R.dirty = true;
  updateTimeHud();
}

function updateTimeHud() {
  const t = Math.round(R.time);
  el("tlTime").textContent = t.toLocaleString() + " ms";
  const head = el("tlHead");
  if (head) head.style.left = (t / TOTAL * 100).toFixed(3) + "%";
  const seq = D.sequences[S.seq];
  el("playBtn").textContent = R.playing ? "❚❚" : "▶";
  if (seq) el("playBtn").title = (R.playing ? tr("暂停") : tr("播放")) + " " + seq.name + tr("（空格）");

  const m = modeOf(seq);
  const icon = m === "once" ? "→" : m === "pingpong" ? "⇄" : "↻";
  const lb = el("loopBtn");
  if (lb) {
    lb.textContent = icon;
    lb.title = tr("循环：跟随文件 / 循环 / 单次 / 往返（点一下切换）");
    lb.classList.toggle("on", m !== "once");
  }
  const ab = el("autoBtn");
  if (ab) ab.classList.toggle("on", S.autoPlay);
}

/* 贴图按需异步加载。同一张只请求一次，多个材质共享同一个 Texture。
   加载失败（例如用 file:// 打开时浏览器禁止把本地图片送进 WebGL）不报错，
   材质保持平均色——看起来是t("低模")，而不是一片白。 */
/* 贴图走本地接口，索引就是 TEXS 里的序号。size 决定解码后缩到多大。
   两级：先要 512 让模型立刻有皮，随后偷偷换 1024——HD 模型的源图普遍 2048，
   直接等 1024 会卡好几秒，而只给 512 又明显糊。 */
const TEX_SIZE_FAST = 512;
const TEX_SIZE_FULL = 1024;

function texUrl(idx, size) {
  return "/api/tex?i=" + idx + "&size=" + (size || TEX_SIZE_FAST) +
         "&team=" + S.team +
         "&token=" + encodeURIComponent(window.MDX_TOKEN || "");
}
const TEXCACHE = {};
const TEXSTAT = { ok: 0, fail: 0, blocked: 0 };
let TEXNEED = 0;                    // 这个模型要加载多少张漫反射贴图
let fileModeWarned = false;

function warnFileMode() {
  if (fileModeWarned) return;
  fileModeWarned = true;
  showFatal(tr("本地文件模式：贴图不可用"),
    "浏览器禁止 file:// 页面读取本地图片。模型与动画正常，贴图用平均色代替。\n" +
    tr("在 prototype 目录执行 python -m http.server 后访问即可看到完整贴图。"));
  el("vpFallback").classList.add("banner");
  setTimeout(() => {
    const b = el("vpFallback");
    if (b) b.classList.remove("on");
  }, 8000);
}

function loadTexture(idx, cb, noUpgrade) {
  const e = (window.MDX_TEX && window.MDX_TEX.entries[idx]) || null;
  if (!e) { if (cb) cb(null); return; }

  const hit = TEXCACHE[idx];
  if (hit) {
    if (hit.ok) { if (cb) cb(hit.tex); }
    else if (hit.waiting) hit.waiting.push(cb);
    return;
  }

  /* file:// 下 WebGL 一定拒绝本地图片（SecurityError），直接降级，别发请求 */
  if (location.protocol === "file:") {
    TEXCACHE[idx] = { tex: null, ok: false };
    TEXSTAT.blocked++;
    if (cb) cb(null);
    updateTexHud();
    warnFileMode();
    return;
  }

  const slot = { tex: null, ok: false, waiting: cb ? [cb] : [] };
  TEXCACHE[idx] = slot;

  const img = new Image();
  img.crossOrigin = "anonymous";       // 跨域图床没有 CORS 头时能提前失败，而不是等到上传时炸
  img.onload = () => {
    const tex = new T3.Texture(img);
    tex.flipY = false;                  // WC3 的 UV 原点在左上
    tex.anisotropy = 4;
    if ("sRGBEncoding" in T3) tex.encoding = T3.sRGBEncoding;
    tex.needsUpdate = true;
    slot.tex = tex;
    slot.ok = true;
    TEXSTAT.ok++;
    slot.waiting.forEach(f => f(tex));
    slot.waiting = [];
    R.dirty = true;
    updateTexHud();
    if (!noUpgrade) queueTexUpgrade(idx);   // 先有皮，再悄悄换高清
  };
  img.onerror = () => {
    slot.ok = false;
    TEXSTAT.fail++;
    slot.waiting.forEach(f => f(null));
    slot.waiting = [];
    updateTexHud();
    if (TEXSTAT.fail === 1) {
      showFatal(tr("部分贴图未加载"),
        tr("对应部位用该贴图的平均色代替。检查 tex/ 下的文件是否存在，或用 start.bat 以本地服务打开。"));
      setTimeout(() => el("vpFallback").classList.remove("on"), 6000);
    }
  };
  img.src = texUrl(idx, TEX_SIZE_FAST);
}

/* ---- 高清贴图的后台升级 ----
   HD 模型的源图普遍 1024–2048，只给 512 会糊。但 1024 的 DXT 解码要 0.5 秒，
   打开模型时一次性要十几张就得等好几秒。折中：先用 512 把模型画出来，
   再限制两个并发，在后台逐张换 1024。用户看到的是"越来越清楚"。 */
const TEXCACHE_HI = {};   // undefined=没排、null=排队中、false=源图不大、Texture=已换
const TEX_UP_QUEUE = [];
let TEX_UP_BUSY = 0;
const TEX_UP_OFF = /(?:^|[?&])texup=0(?:&|$)/.test(location.search);

function queueTexUpgrade(idx) {
  if (TEX_UP_OFF) return;                  // ?texup=0 关掉，自动化对比 512 与 1024 用
  if (idx in TEXCACHE_HI) return;
  TEXCACHE_HI[idx] = null;
  TEX_UP_QUEUE.push(idx);
  pumpTexUpgrades();
}

function pumpTexUpgrades() {
  while (TEX_UP_BUSY < 2 && TEX_UP_QUEUE.length) {
    const idx = TEX_UP_QUEUE.shift();
    TEX_UP_BUSY++;
    const fast = TEXCACHE[idx] && TEXCACHE[idx].tex;
    const fastArea = fast && fast.image ? fast.image.width * fast.image.height : 1;
    const img = new Image();
    img.onload = () => {
      TEX_UP_BUSY--;
      /* 只有真的变大了才换。1024x2048 的源图在 size=1024 下产出 512x1024，
         宽还是 512——不能拿"宽 > 512"当判据。 */
      if (img.naturalWidth * img.naturalHeight > fastArea * 1.1) {
        const tex = new T3.Texture(img);
        tex.flipY = false;
        tex.anisotropy = 8;             // 斜着看贴图边缘不容易糊
        if ("sRGBEncoding" in T3) tex.encoding = T3.sRGBEncoding;
        tex.needsUpdate = true;
        TEXCACHE_HI[idx] = tex;
        applyHiTex(idx, tex);
      } else {
        TEXCACHE_HI[idx] = false;       // 源图本来就不大，白跑一趟
      }
      pumpTexUpgrades();
    };
    img.onerror = () => {
      TEXCACHE_HI[idx] = false;
      TEX_UP_BUSY--;
      pumpTexUpgrades();
    };
    img.src = texUrl(idx, TEX_SIZE_FULL);
  }
}

function applyHiTex(idx, tex) {
  let hit = 0;
  for (const m of R.meshes) {
    if (!m.userData || m.userData.texIndex !== idx) continue;
    m.material.map = tex;
    m.material.needsUpdate = true;
    hit++;
  }
  if (hit) R.dirty = true;
}

/* ---------------- 队伍色 ----------------
   Reforged 的模型不在贴图上涂色，而是把"该涂玩家色"的地方记在 ORM 的 alpha 通道里
   （1 = 涂色，0 = 不涂）。所以换颜色不是换贴图，是改一个 uniform —— 即时生效，
   不用重新请求任何东西。经典模型则直接把队伍色贴图当漫反射用，那条路走 texUrl 的 team 参数。 */
function teamColorHex() {
  return TEAM_COLORS[S.team] || TEAM_COLORS[0];
}

function applyTeamTint(mat, ormTex) {
  mat.userData.teamOrm = ormTex;
  mat.onBeforeCompile = shader => {
    shader.uniforms.uOrmMap = { value: ormTex };
    shader.uniforms.uTeamColor = { value: new T3.Color(teamColorHex()) };
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>",
        "#include <common>\nuniform sampler2D uOrmMap;\nuniform vec3 uTeamColor;")
      .replace("#include <map_fragment>",
        "#include <map_fragment>\n" +
        "#ifdef USE_MAP\n" +
        "  float teamMask = texture2D(uOrmMap, vUv).a;\n" +
        "  diffuseColor.rgb = mix(diffuseColor.rgb, uTeamColor, teamMask);\n" +
        "#endif");
    mat.userData.shader = shader;
  };
  /* 不缓存着色器程序：否则换了一个材质仍可能复用旧的编译结果 */
  mat.customProgramCacheKey = () => "mf-team";
  mat.needsUpdate = true;
}

function refreshTeamColor() {
  const hex = teamColorHex();
  for (const m of R.meshes) {
    const ud = m.material && m.material.userData;
    if (ud && ud.shader && ud.shader.uniforms.uTeamColor) {
      ud.shader.uniforms.uTeamColor.value.set(hex);
    }
  }
  R.dirty = true;
}
/* FilterMode → three 混合状态。映射依据 Retera 的 PerspectiveViewport.bindLayer()。
   None(0) 关混合；Transparent(1) 用 alpha 测试 0.75；其余是各种 blend 因子组合。 */
function applyFilter(mat, filter, alpha) {
  mat.transparent = false;
  mat.alphaTest = 0;
  if (filter === "Transparent") {
    mat.alphaTest = 0.75;
  } else if (filter === "Blend") {
    mat.transparent = true;
    mat.blending = T3.NormalBlending;
  } else if (filter === "Additive" || filter === "AddAlpha") {
    mat.transparent = true;
    mat.blending = T3.CustomBlending;
    mat.blendSrc = T3.SrcAlphaFactor;
    mat.blendDst = T3.OneFactor;
    mat.blendEquation = T3.AddEquation;
  } else if (filter === "Modulate") {
    mat.transparent = true;
    mat.blending = T3.CustomBlending;
    mat.blendSrc = T3.ZeroFactor;
    mat.blendDst = T3.SrcColorFactor;
    mat.blendEquation = T3.AddEquation;
  } else if (filter === "Modulate2x") {
    mat.transparent = true;
    mat.blending = T3.CustomBlending;
    mat.blendSrc = T3.DstColorFactor;
    mat.blendDst = T3.SrcColorFactor;
    mat.blendEquation = T3.AddEquation;
  }
  mat.depthWrite = !mat.transparent;
  if (alpha < 0.999) { mat.opacity = alpha; mat.transparent = true; }
  mat.needsUpdate = true;
}

function initTemps() {
  _qa = new T3.Quaternion(); _qb = new T3.Quaternion();
  _mRS = new T3.Matrix4(); _mT1 = new T3.Matrix4(); _mT2 = new T3.Matrix4();
  _vZero = new T3.Vector3(0, 0, 0); _vScale = new T3.Vector3(1, 1, 1);
  _q = new T3.Quaternion();
}

function buildScene() {
  initTemps();
  const canvas = el("vp");
  R.renderer = new T3.WebGLRenderer({ canvas, antialias: true, alpha: true });
  R.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  if ("sRGBEncoding" in T3) R.renderer.outputEncoding = T3.sRGBEncoding;
  R.scene = new T3.Scene();
  R.camera = new T3.PerspectiveCamera(40, 1, 0.5, 3000);
  R.camera.up.set(0, 0, 1);          // WC3 是 Z-up
  R.target = new T3.Vector3(0, 0, 55);
  frameModel();

  R.scene.add(new T3.AmbientLight(0xffffff, 0.62));
  const key = new T3.DirectionalLight(0xffffff, 0.82);
  key.position.set(0.7, -0.9, 1.4);
  R.scene.add(key);
  const fill = new T3.DirectionalLight(0xa8c6e0, 0.42);
  fill.position.set(-1.2, 0.9, 0.4);
  R.scene.add(fill);
  const rim = new T3.DirectionalLight(0x9fb8cc, 0.30);
  rim.position.set(0, 0.6, -1.2);
  R.scene.add(rim);

  /* 骨骼层级 */
  const bones = [];
  for (const b of MD.bones) {
    const bone = new T3.Bone();
    bone.name = b.n;
    bone.userData.pv = b.pv;
    bones[b.id] = bone;
  }
  const root = new T3.Object3D();
  root.matrixAutoUpdate = false;
  for (const b of MD.bones) {
    if (b.p >= 0 && bones[b.p]) bones[b.p].add(bones[b.id]);
    else root.add(bones[b.id]);
  }
  R.bones = bones;

  /* 建 Skeleton 之前必须处于 bind pose：局部矩阵 = T(pv)·I·T(−pv) = I。
     否则 Skeleton 会把t("动画第一帧")当成绑定姿态，蒙皮整体错位。 */
  for (const b of bones) {
    if (!b) continue;
    b.matrix.identity();
    b.matrixAutoUpdate = false;
  }
  root.updateMatrixWorld(true);
  R.skel = new T3.Skeleton(bones);

  /* 关键：这个 group 必须是 identity。
     骨骼与 mesh 同在这一层，父级变换会被算两次——
     皮肤管线是 view·meshWorld·bindInv·boneWorld·bind·pos，
     而 boneWorld 里已经含 group 变换。之前在这里加 0.01 缩放，
     等于缩放平方（1e-4），模型只有一厘米大，肉眼就是t("没有模型")。
     Z-up 交给相机（camera.up = +Z）处理。 */
  R.group = new T3.Group();
  R.group.matrixAutoUpdate = false;
  R.group.matrix.identity();
  R.group.add(root);

  /* 每个 geoset 一个 SkinnedMesh，共用同一套骨骼 */
  const needTex = new Set();        // 需要加载的漫反射贴图数，用来在 HUD 上显示进度
  const normalJobs = [];            // 法线贴图延后加载，先让颜色铺满
  const ormJobs = [];               // ORM 更靠后：它只用于队伍色遮罩
  for (const g of MD.geosets) {
    const geo = new T3.BufferGeometry();
    geo.setAttribute("position", new T3.BufferAttribute(dec64(g.pos, Float32Array), 3));
    geo.setAttribute("normal", new T3.BufferAttribute(dec64(g.nrm, Float32Array), 3));
    geo.setAttribute("uv", new T3.BufferAttribute(dec64(g.uv, Float32Array), 2));
    geo.setAttribute("skinIndex", new T3.Uint16BufferAttribute(dec64(g.jnt, Uint16Array), 4));
    geo.setAttribute("skinWeight", new T3.BufferAttribute(dec64(g.wgt, Float32Array), 4));
    geo.setIndex(new T3.BufferAttribute(dec64(g.idx, Uint16Array), 1));
    geo.computeBoundingSphere();

    /* 找这个 geoset 的漫反射贴图：优先 HD 合并槽，退回历史 textureId */
    const mdef = D.materials[g.mat];
    const lay = mdef && mdef.layers && mdef.layers[0];
    const tid = lay
      ? (lay.hd && lay.hd.Diffuse !== undefined ? lay.hd.Diffuse : lay.textureId)
      : -1;
    const info = window.MDX_TEX && window.MDX_TEX.entries[tid];

    /* Lambert 不支持 normalMap，改用 Phong 并把高光压到 0，视觉上等价于 Lambert */
    const mat = new T3.MeshPhongMaterial({
      /* 贴图还没到位时先给中性灰：纯白会让未加载的部位像挖了个洞，
         灰的看起来像"还在上色"，观感差别很大 */
      color: info ? 0x8f959c : new T3.Color(g.col),
      side: T3.DoubleSide,
      shininess: 2,
      specular: 0x000000,
    });
    if (info) {
      needTex.add(tid);
      loadTexture(tid, tex => {
        if (!tex) return;
        mat.map = tex;
        mat.color.setHex(0xffffff);
        mat.needsUpdate = true;
        R.dirty = true;
      });
      applyFilter(mat, (lay && lay.filter) || "None", (lay && lay.alpha) || 1);

      /* 法线贴图：由后端统一给出（HD 模型的合并槽 / SD 模型的第 2 层）。
         ATI2/BC5 解码后 R,G 就是切线空间 xy。
         它排在漫反射之后加载——先把颜色铺满，再补表面起伏。 */
      const nid = mdef && mdef.normalTex !== undefined ? mdef.normalTex : -1;
      if (nid >= 0) {
        normalJobs.push([nid, mat]);
      }

      /* 自发光：灯笼、符文这类靠它亮，没有的话暗部会糊成一片 */
      const eid = mdef && mdef.emissiveTex !== undefined ? mdef.emissiveTex : -1;
      if (eid >= 0 && eid !== tid) {
        loadTexture(eid, tex => {
          if (!tex) return;
          mat.emissiveMap = tex;
          mat.emissive.setHex(0xffffff);
          mat.emissiveIntensity = 1;
          mat.needsUpdate = true;
          R.dirty = true;
        }, true);
      }

      /* 队伍色：ORM 的 alpha 是"该处涂玩家色"的遮罩，只有存在队伍色槽位时才值得加载 */
      const oid = mdef && mdef.teamTex >= 0 && mdef.ormTex !== undefined
        ? mdef.ormTex : -1;
      if (oid >= 0) ormJobs.push([oid, mat]);
    }
    const mesh = new T3.SkinnedMesh(geo, mat);
    mesh.bind(R.skel, new T3.Matrix4());
    /* detached：bindMatrixInverse 由 bindMatrix 求逆（identity），
       不受 group 的 0.01 缩放干扰 */
    mesh.bindMode = "detached";
    mesh.frustumCulled = false;
    mesh.userData = { geoset: g.i, mat: g.mat, tris: g.nt, verts: g.nv, texIndex: tid };
    R.meshes.push(mesh);
    R.group.add(mesh);
  }

  R.scene.add(R.group);
  R.group.updateMatrixWorld(true);
  TEXNEED = needTex.size;
  updateTexHud();
  /* 法线贴图排在漫反射之后：一口气发 46 个请求会把浏览器的 6 个并发占满，
     颜色部分反而最后才出来。让路 80 ms，够漫反射的请求先排上队。 */
  if (normalJobs.length) {
    setTimeout(() => {
      for (const [nid, mat] of normalJobs) {
        loadTexture(nid, tex => {
          if (!tex) return;
          tex.encoding = T3.LinearEncoding;   // 法线数据不能做 sRGB 转换
          tex.needsUpdate = true;
          mat.normalMap = tex;
          mat.normalScale.set(1, 1);
          mat.needsUpdate = true;
          R.dirty = true;
        }, true);
      }
    }, 80);
  }
  /* ORM 排在最后：它的 alpha 只用于队伍色遮罩，晚一点不影响初见效果 */
  if (ormJobs.length) {
    setTimeout(() => {
      for (const [oid, mat] of ormJobs) {
        loadTexture(oid, tex => {
          if (!tex) return;
          tex.encoding = T3.LinearEncoding;   // 遮罩不是颜色，不能做 sRGB 转换
          tex.needsUpdate = true;
          applyTeamTint(mat, tex);
        }, true);
      }
    }, 260);
  }
  buildParticles();
  setTime(D.sequences[1] ? D.sequences[1].start : 0);
}

/* 按模型包围盒定相机：建筑和英雄尺寸差三倍，硬编码一定取不全 */
function frameModel() {
  const b = MD && MD.bounds;
  if (!b) return;
  const cx = (b.min[0] + b.max[0]) / 2;
  const cy = (b.min[1] + b.max[1]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  const size = Math.max(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]);
  if (!isFinite(size) || size <= 0 || size > 1e6) {
    /* 纯粒子模型没有几何，包围盒是空的 */
    R.target.set(0, 0, 80);
    R.dist0 = 420; R.dist = R.dist0;
    R.distMin = 120; R.distMax = 1800;
    R.camera.near = 0.5; R.camera.far = 4000;
    R.camera.updateProjectionMatrix();
    return;
  }
  R.target.set(cx, cy, cz);
  R.dist0 = Math.max(20, size * 1.9);
  R.dist = R.dist0;
  R.distMin = R.dist0 * 0.28;
  R.distMax = R.dist0 * 4.5;
  R.camera.near = Math.max(0.1, R.dist0 / 600);
  R.camera.far = R.dist0 * 40;
  R.camera.updateProjectionMatrix();
}

/* ---------------- 粒子（PRE2） ---------------- */
const PARTS = [];
let SPRITE = null;

/* 粒子贴图常是 rows×cols 的翻页纹理（一张图里排着多帧）。
   Points 没办法逐粒子改 UV，所以裁出第一帧单独用——至少不会显示成网格。 */
function cropFrame(tex, rows, cols) {
  const img = tex.image;
  if (!img || !img.width) return tex;
  if (rows <= 1 && cols <= 1) return tex;
  const w = Math.floor(img.width / cols), h = Math.floor(img.height / rows);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d").drawImage(img, 0, 0, w, h, 0, 0, w, h);
  const t = new T3.CanvasTexture(c);
  if ("sRGBEncoding" in T3) t.encoding = T3.sRGBEncoding;
  return t;
}

/* 贴图缺失时的柔光圆点，避免粒子显示成方块 */
function particleSprite() {
  if (SPRITE) return SPRITE;
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.45, "rgba(255,255,255,0.55)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  SPRITE = new T3.CanvasTexture(c);
  return SPRITE;
}

function spawnParticle(sys, i) {
  const p = sys.def;
  const st = sys.st;
  const o = i * 7;
  const src = p.obj >= 0 && MD.pivots && MD.pivots[p.obj] ? MD.pivots[p.obj].xyz : [0, 0, 80];
  st[o] = src[0];
  st[o + 1] = src[1];
  st[o + 2] = src[2];
  const sp = p.speed * (1 - (p.var || 0) / 2 + Math.random() * (p.var || 0));
  const theta = Math.random() * Math.PI * 2;
  const maxPhi = Math.max(0.05, (p.lat || 0) / 180 * Math.PI);
  const phi = Math.random() * maxPhi;
  st[o + 3] = sp * Math.sin(phi) * Math.cos(theta);
  st[o + 4] = sp * Math.sin(phi) * Math.sin(theta);
  st[o + 5] = sp * Math.cos(phi);
  st[o + 6] = 0;
}

function buildParticles() {
  if (!MD.particles || !MD.particles.length) return;
  for (const p of MD.particles) {
    const n = Math.max(16, Math.min(600, Math.ceil(p.rate * p.life)));
    const posArr = new Float32Array(n * 3);
    const colArr = new Float32Array(n * 3);
    const geo = new T3.BufferGeometry();
    geo.setAttribute("position", new T3.BufferAttribute(posArr, 3));
    geo.setAttribute("color", new T3.BufferAttribute(colArr, 3));
    geo.boundingSphere = new T3.Sphere(new T3.Vector3(0, 0, 80), 2000);

    const mat = new T3.PointsMaterial({
      size: Math.max(4, p.wid || 30),
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      sizeAttenuation: true,
      blending: p.filt === 1 ? T3.NormalBlending : T3.AdditiveBlending,
    });
    mat.map = particleSprite();          // 先用柔光圆点，贴图到了再换
    /* 先建材质再挂贴图：loadTexture 命中缓存时会同步回调 */
    if (p.tex >= 0) {
      loadTexture(p.tex, t => {
        if (!t) return;
        mat.map = cropFrame(t, p.rows || 1, p.cols || 1);
        mat.needsUpdate = true;
        R.dirty = true;
      });
    }
    const pts = new T3.Points(geo, mat);
    pts.frustumCulled = false;
    R.group.add(pts);

    const st = new Float32Array(n * 7);
    const sys = { def: p, pts, geo, pos: posArr, col: colArr, st, n };
    for (let i = 0; i < n; i++) {
      spawnParticle(sys, i);
      st[i * 7 + 6] = Math.random() * p.life;   // 错开年龄，避免一开场全部同时出生
    }
    PARTS.push(sys);
  }
}

function updateParticles(dt) {
  if (!PARTS.length) return;
  for (const sys of PARTS) {
    const p = sys.def, st = sys.st, pos = sys.pos, col = sys.col, n = sys.n;
    const c0 = p.col[0], c1 = p.col[1], c2 = p.col[2];
    for (let i = 0; i < n; i++) {
      const o = i * 7;
      st[o + 6] += dt;
      if (st[o + 6] >= p.life) spawnParticle(sys, i);
      st[o] += st[o + 3] * dt;
      st[o + 1] += st[o + 4] * dt;
      st[o + 2] += st[o + 5] * dt;
      if (p.grav) st[o + 5] -= p.grav * dt;

      const t = Math.min(1, st[o + 6] / p.life);
      const a = t < 0.5 ? c0 : c1, b = t < 0.5 ? c1 : c2;
      const f = t < 0.5 ? t * 2 : (t - 0.5) * 2;
      const al = p.alpha[0] + (p.alpha[2] - p.alpha[0]) * t;
      pos[i * 3] = st[o];
      pos[i * 3 + 1] = st[o + 1];
      pos[i * 3 + 2] = st[o + 2];
      col[i * 3] = (a[0] + (b[0] - a[0]) * f) * al;
      col[i * 3 + 1] = (a[1] + (b[1] - a[1]) * f) * al;
      col[i * 3 + 2] = (a[2] + (b[2] - a[2]) * f) * al;
    }
    sys.geo.attributes.position.needsUpdate = true;
    sys.geo.attributes.color.needsUpdate = true;
  }
  R.dirty = true;
}

function updateCamera() {
  if (!R.camera) return;
  const cp = Math.cos(R.pitch), sp = Math.sin(R.pitch);
  R.camera.position.set(
    R.target.x + Math.sin(R.yaw) * cp * R.dist + R.panX,
    R.target.y + Math.cos(R.yaw) * cp * R.dist + R.panY,
    R.target.z + sp * R.dist
  );
  R.camera.lookAt(R.target);
  R.camera.updateMatrixWorld();
}

function resizeVP() {
  if (!R.renderer) return;
  const cv = el("vp");
  const rect = cv.getBoundingClientRect();
  const w = Math.max(1, rect.width), h = Math.max(1, rect.height);
  R.renderer.setSize(w, h, false);
  R.camera.aspect = w / h;
  R.camera.updateProjectionMatrix();
  R.dirty = true;
}

let lastFrame = 0;
const LOOP_LABEL = { file: tr("跟随文件"), loop: tr("循环"), once: tr("单次"), pingpong: tr("往返") };

/* 当前序列实际使用的循环方式：file 模式直接照 SEQS 里的 non_looping 标记 */
function modeOf(seq) {
  if (!seq) return "once";
  if (S.loopMode !== "file") return S.loopMode;
  return seq.loop ? "loop" : "once";
}

/* 推进一帧。纯函数，边界情况可单独测。 */
function advance(t, dt, seq, mode, dir) {
  const start = seq.start, end = seq.end;
  const dur = Math.max(1, end - start);
  let nt = t + dt * dir;
  let nd = dir, stop = false;
  if (mode === "pingpong") {
    if (nt > end) { nt = end - (nt - end); nd = -1; }
    else if (nt < start) { nt = start + (start - nt); nd = 1; }
    nt = Math.max(start, Math.min(end, nt));
  } else if (nt > end) {
    if (mode === "loop") nt = start + ((nt - end) % dur);
    else { nt = end; stop = true; }
  } else if (nt < start) {
    nt = start;
  }
  return { t: nt, dir: nd, stop: stop };
}

function frame(now) {
  const dt = lastFrame ? Math.min(80, now - lastFrame) : 0;
  lastFrame = now;
  if (PARTS.length) updateParticles(Math.min(0.05, dt / 1000));
  if (R.playing) {
    const seq = D.sequences[S.seq] || D.sequences[0];
    if (!seq) {
      R.playing = false;
      updateTimeHud();
    } else {
      const r = advance(R.time, dt, seq, modeOf(seq), R.dir || 1);
      R.dir = r.dir;
      if (r.stop) { R.playing = false; updateTimeHud(); }
      setTime(r.t);
    }
  }
  if (R.dirty) {
    try {
      R.renderer.render(R.scene, R.camera);
    } catch (err) {
      /* 渲染一旦抛异常必须掐掉当前脏标记并继续排下一帧，
         否则 requestAnimationFrame 断掉，画面会永久冻住 */
      if (!R.renderErr) {
        R.renderErr = String((err && err.message) || err);
        showFatal(tr("渲染出错"), R.renderErr);
        if (window.console) console.error(err);
      }
    }
    R.dirty = false;
  }
  requestAnimationFrame(frame);
}

function initViewport() {
  if (!T3 || !MD) { el("vpName").textContent = tr("几何数据未加载"); return; }
  buildScene();
  resizeVP();
  updateCamera();
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => resizeVP()).observe(el("vp").parentElement);
  } else {
    window.addEventListener("resize", resizeVP);
  }

  const cv = el("vp");
  cv.addEventListener("mousedown", e => {
    R.drag = { btn: e.button, x: e.clientX, y: e.clientY };
    e.preventDefault();
  });
  cv.addEventListener("contextmenu", e => e.preventDefault());
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    R.dist = Math.max(R.distMin, Math.min(R.distMax, R.dist * (1 + e.deltaY * 0.0012)));
    updateZoomHud(); updateCamera(); R.dirty = true;
  }, { passive: false });
  cv.addEventListener("dblclick", () => resetCamera());
  window.addEventListener("mousemove", e => {
    if (!R.drag) return;
    const dx = e.clientX - R.drag.x, dy = e.clientY - R.drag.y;
    R.drag.x = e.clientX; R.drag.y = e.clientY;
    if (R.drag.btn === 0) {
      R.yaw -= dx * 0.0075;
      R.pitch = Math.max(-1.35, Math.min(1.35, R.pitch + dy * 0.0075));
    } else {
      R.panX += dx * 0.0013 * R.dist;
      R.panY -= dy * 0.0013 * R.dist;
    }
    updateCamera(); R.dirty = true;
  });
  window.addEventListener("mouseup", () => { R.drag = null; });

  el("previewSeg").querySelectorAll("button").forEach(b => {
    b.onclick = () => {
      S.preview = b.dataset.p;
      el("previewSeg").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      renderViewport();
    };
  });

  /* 时间轴拖动条 */
  const scrub = el("tlScrub");
  let scrubbing = false;
  const seek = e => {
    const r = scrub.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)));
    R.playing = false;
    setTime(f * TOTAL);
    const seq = D.sequences.find(s => R.time >= s.start && R.time <= s.end);
    if (seq) S.seq = seq.i;
    renderTimeline();
  };
  scrub.addEventListener("mousedown", e => { scrubbing = true; seek(e); e.preventDefault(); });
  window.addEventListener("mousemove", e => { if (scrubbing) seek(e); });
  window.addEventListener("mouseup", () => { scrubbing = false; });

  el("playBtn").onclick = () => togglePlay();

  el("loopBtn").onclick = () => {
    const order = ["file", "loop", "once", "pingpong"];
    S.loopMode = order[(order.indexOf(S.loopMode) + 1) % order.length];
    updateTimeHud();
    toast(tr("循环：") + tr(LOOP_LABEL[S.loopMode]));
  };

  el("autoBtn").onclick = () => {
    S.autoPlay = !S.autoPlay;
    updateTimeHud();
    toast(S.autoPlay ? tr("切换动画后会自动播放") : tr("切换动画后不自动播放"));
  };

  updateZoomHud();
  requestAnimationFrame(frame);
}

function togglePlay(force) {
  if (!R.group) return;
  const want = force === undefined ? !R.playing : !!force;
  if (want === R.playing) { updateTimeHud(); return; }
  if (want) {
    const seq = D.sequences[S.seq];
    if (seq && (R.time < seq.start || R.time > seq.end - 1)) {
      R.time = seq.start;
      setTime(R.time);
    }
    R.dir = 1;
    R.playing = true;
  } else {
    R.playing = false;
  }
  updateTimeHud();
}

function resetCamera() {
  R.yaw = Math.PI / 2; R.pitch = 0.12; R.dist = R.dist0 || 260; R.panX = 0; R.panY = 0;
  updateZoomHud(); updateCamera(); R.dirty = true;
}

function updateZoomHud() {
  const pct = Math.round(((R.dist0 || 260) / R.dist) * 100);
  el("zoomTxt").textContent = pct + "%";
  const bar = el("zoomBar");
  if (bar) bar.style.width = Math.max(6, Math.min(100, pct * 0.62)) + "%";
}

function texHudText() {
  const total = window.MDX_TEX
    ? Object.values(window.MDX_TEX.entries).filter(e => e.used).length : 0;
  if (TEXSTAT.blocked) return tr("贴图不可用");
  if (TEXSTAT.ok === 0 && TEXSTAT.fail === 0) return tr("贴图加载中");
  /* 模型刚打开时贴图是一张张进来的，先把进度说清楚，
     否则看到半白半彩的样子会以为没加载出来。 */
  if (TEXNEED && TEXSTAT.ok < TEXNEED) {
    return tr("贴图 {a}/{b}", { a: TEXSTAT.ok, b: TEXNEED });
  }
  return tr("{a} 张在用 · 共 {b} 条", { a: TEXSTAT.ok, b: total });
}

function updateTexHud() {
  if (!MD) return;
  const h = el("vpMeta");
  if (!h) return;
  const st = MD.stats;
  h.textContent =
    tr("{v} 顶点 · {f} 三角 · {b} 骨骼 · {t}", { v: st.verts.toLocaleString(), f: st.tris.toLocaleString(), b: st.bones, t: texHudText() });
}

function renderViewport() {
  const wire = S.preview === "wire";
  const st = MD ? MD.stats : { verts: 0, tris: 0, bones: 0 };
  if (MD) el("vpName").textContent = MD.file;
  if (MD) {
    /* 和 updateTexHud 用同一个模板——这里原来又硬编码了一遍，会把翻译覆盖回中文 */
    el("vpMeta").textContent = tr("{v} 顶点 · {f} 三角 · {b} 骨骼 · {t}", {
      v: st.verts.toLocaleString(), f: st.tris.toLocaleString(),
      b: st.bones, t: texHudText(),
    });
  }
  for (const m of R.meshes) m.material.wireframe = wire;
  R.dirty = true;
}

/* 贴图缩略图。没导出成功就显示虚线占位——一眼能看出"这张贴图缺了"。
   注意：<img> 在 file:// 下也能正常显示，只有上传到 WebGL 才受限。 */
/* 缩略图加载失败自动重试。

   WebView2 同时发很多图片请求时，个别请求会被取消（服务端明明返回了 200），
   表现就是列表里有几格永远空着。重试一次基本都能回来，不必重新解码。 */
function retryTex(img) {
  const n = +(img.dataset.retry || 0);
  if (n >= 2) {
    img.classList.add("thumb-none");
    img.removeAttribute("src");
    return;
  }
  img.dataset.retry = n + 1;
  const url = img.dataset.src || "";
  setTimeout(() => { img.src = url + "&retry=" + (n + 1); }, 350 * (n + 1));
}

function texThumb(idx) {
  const e = window.MDX_TEX && window.MDX_TEX.entries[idx];
  if (!e) return '<div class="thumb-none">无</div>';
  const url = texUrl(idx, 96);
  /* 不用 loading="lazy"：WebView2 对视口外元素会直接不请求，滚动到才补，
     看起来就像t("这些贴图读不出来")。缩略图是本地服务、平均十几 KB，
     一次全取反而更快更稳。 */
  return `<img class="thumb" src="${url}" data-src="${url}"
               data-idx="${idx}" alt="" onerror="retryTex(this)">`;
}

/* 可替换贴图（队伍色 / 队伍光晕）在模型里本来就没有路径，
   显示空白会让人以为文件丢了——这里给它一个说得清的名字。 */
function texPathLabel(t) {
  if (!t) return "";
  if (t.replaceableId === 1) return tr("队伍颜色（运行时替换）");
  if (t.replaceableId === 2) return tr("队伍光晕（运行时替换）");
  if (t.replaceableId) return tr("可替换贴图 {n}", { n: t.replaceableId });
  return t.path;
}

function texBig(idx) {
  const e = window.MDX_TEX && window.MDX_TEX.entries[idx];
  if (!e) return '<div class="tex-big-none">该贴图未能找到（本地与游戏数据都没有）</div>';
  return `<img class="tex-big" src="${texUrl(idx, 512)}" alt="">`;
}

/* ---------------- 表：贴图 ---------------- */
function texRows() {
  return D.textures.filter(t => {
    if (S.filter !== "all" && t.cls !== S.filter) return false;
    if (S.query && !t.path.toLowerCase().includes(S.query.toLowerCase())) return false;
    return true;
  });
}
function matRows() {
  return D.materials.filter(m => {
    if (!S.query) return true;
    return m.layers.some(l => (texturePath(l.textureId) + " " + l.filter).toLowerCase().includes(S.query.toLowerCase()));
  });
}
function seqRows() {
  return D.sequences.filter(s => !S.query || s.name.toLowerCase().includes(S.query.toLowerCase()));
}
function atcRows() {
  return D.attachments.filter(a => !S.query ||
    (a.name + " " + a.parentName + " " + a.mount).toLowerCase().includes(S.query.toLowerCase()));
}

function renderTable() {
  const isTex = S.view === "textures";

  const tabsBox = el("tabs");
  if (isTex) {
    const counts = { all: D.textures.length, external: 0, builtin: 0, replaceable: 0 };
    D.textures.forEach(t => counts[t.cls]++);
    tabsBox.innerHTML = [["all",tr("全部")],["external",tr("外部")],["builtin",tr("内置")],["replaceable",tr("可替换")]]
      .map(([k, l]) => `<button data-f="${k}" class="${S.filter===k?"on":""}">${l}<span class="n">${counts[k]}</span></button>`).join("");
    tabsBox.querySelectorAll("button").forEach(b => b.onclick = () => { S.filter = b.dataset.f; renderTable(); });
    el("twActions").innerHTML = `<button class="btn sm accent" id="bulkBtn">${tr("一键改外部路径")}</button>`;
    el("bulkBtn").onclick = openBulk;
  } else {
    tabsBox.innerHTML = "";
    el("twActions").innerHTML = S.view === "attachments"
      ? `<button class="btn sm accent" id="mountBtn">${tr("挂载武器")}</button>`
      : "";
    if (el("mountBtn")) {
      el("mountBtn").onclick = () => { if (S.sel && S.sel.kind === "attachments") openMount(S.sel.index); };
    }
  }

  const head = el("thead"), body = el("tbody");
  if (isTex) {
    head.className = "thead grid-tex";
    head.innerHTML = `<div>#</div><div></div><div>${tr("路径")}</div><div>${tr("类型")}</div><div></div>`;
    const rows = texRows();
    body.innerHTML = rows.map(t => {
      const key = `tex:${t.index}:path`;
      const st = S.changes.get(key);
      const locked = S.locks.has(t.index);
      return `<div class="trow grid-tex ${S.sel&&S.sel.kind==="textures"&&S.sel.index===t.index?"on":""} ${st?"changed":""}"
        data-i="${t.index}">
        <div class="cell-dim num">${t.index}</div>
        <div>${texThumb(t.index)}</div>
        <div class="cell-path" title="${esc(t.path)}">${esc(st ? st.to : texPathLabel(t))}</div>
        <div><span class="lb ${CLS_CLASS[t.cls]}">${CLS_LABEL[t.cls]}</span></div>
        <div><span class="lockbtn ${locked?"on":""}" data-lock="${t.index}">${locked?"■":"□"}</span></div>
      </div>`;
    }).join("");
    body.querySelectorAll(".trow").forEach(r => {
      r.onclick = e => {
        if (e.target.dataset.lock !== undefined) {
          const i = +e.target.dataset.lock;
          if (S.locks.has(i)) S.locks.delete(i); else S.locks.add(i);
          renderTable(); return;
        }
        S.sel = { kind:"textures", index:+r.dataset.i }; renderAll();
      };
    });
  } else if (S.view === "materials") {
    head.className = "thead grid-mat";
    head.innerHTML = `<div>#</div><div>${tr("图层")}</div><div>${tr("FilterMode")}</div><div>${tr("Alpha")}</div>`;
    const rows = matRows();
    body.innerHTML = rows.map(m => m.layers.map(l => {
      const key = `mat:${m.index}:${l.index}`;
      const st = S.changes.get(key);
      const sel = S.sel && S.sel.kind === "materials" && S.sel.index === m.index && S.sel.layer === l.index;
      const diff = st ? st.to.split("|") : null;
      return `<div class="trow grid-mat ${sel?"on":""} ${st?"changed":""}" data-m="${m.index}" data-l="${l.index}">
        <div class="cell-dim num">${m.index}</div>
        <div class="cell-path">${tr("层")} ${l.index} · ${esc((textureOf(l.textureId)||{}).file || tr("无贴图"))}</div>
        <div class="${diff?"":""}" style="${diff?"color:#8fd08a":""}">${esc(diff ? diff[0] : l.filter)}</div>
        <div class="num" style="${diff?"color:#8fd08a":""}">${(st ? parseFloat(diff[1]) : l.alpha).toFixed(2)}</div>
      </div>`;
    }).join("")).join("");
    body.querySelectorAll(".trow").forEach(r => {
      r.onclick = () => { S.sel = { kind:"materials", index:+r.dataset.m, layer:+r.dataset.l }; renderAll(); };
    });
  } else if (S.view === "sequences") {
    head.className = "thead grid-seq";
    head.innerHTML = `<div>#</div><div>${tr("名称")}</div><div>${tr("起 (ms)")}</div><div>${tr("止 (ms)")}</div><div>${tr("时长")}</div><div>${tr("循环")}</div>`;
    const rows = seqRows();
    body.innerHTML = rows.map(s => {
      const key = `seq:${s.index}`;
      const st = S.changes.get(key);
      const v = st ? st.to.split("|") : [s.start, s.end, s.loop ? "1" : "0"];
      return `<div class="trow grid-seq ${S.sel&&S.sel.kind==="sequences"&&S.sel.index===s.index?"on":""} ${st?"changed":""}"
        data-i="${s.index}">
        <div class="cell-dim num">${s.index}</div>
        <div class="cell-path">${esc(s.name)}</div>
        <div class="num ${st?"":""}" style="${st?"color:#8fd08a":""}">${v[0]}</div>
        <div class="num" style="${st?"color:#8fd08a":""}">${v[1]}</div>
        <div class="cell-dim num">${(+v[1] - +v[0]).toLocaleString()}</div>
        <div class="cell-dim">${v[2] === "1" ? tr("循环") : tr("单次")}</div>
      </div>`;
    }).join("");
    body.querySelectorAll(".trow").forEach(r => {
      r.onclick = () => { S.sel = { kind:"sequences", index:+r.dataset.i }; S.seq = +r.dataset.i; renderAll(); };
    });
  } else {
    head.className = "thead grid-atc";
    head.innerHTML = `<div>#</div><div>${tr("名称")}</div><div>${tr("父节点")}</div><div>${tr("编号")}</div><div>${tr("挂载模型")}</div>`;
    const rows = atcRows();
    body.innerHTML = rows.map(a => {
      const key = `atc:${a.index}:mount`;
      const st = S.changes.get(key);
      return `<div class="trow grid-atc ${S.sel&&S.sel.kind==="attachments"&&S.sel.index===a.index?"on":""} ${st?"changed":""}"
        data-i="${a.index}">
        <div class="cell-dim num">${a.index}</div>
        <div class="cell-path">${esc(a.name)}</div>
        <div class="cell-dim cell-path">${esc(a.parentName)}</div>
        <div class="cell-dim num">${a.objectId}</div>
        <div class="cell-path ${st?"":""}" style="${st?"color:#8fd08a":""}">${esc(st ? st.to : (a.mount || "—"))}</div>
      </div>`;
    }).join("");
    body.querySelectorAll(".trow").forEach(r => {
      r.onclick = () => { S.sel = { kind:"attachments", index:+r.dataset.i }; renderAll(); };
    });
  }
}

/* ---------------- 属性面板 ---------------- */
function renderInspector() {
  const box = el("insp");
  if (!S.sel) {
    box.innerHTML = `<div class="empty">未选中</div>
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("文件")}</span><span class="v">${esc(D.model.file)}</span></div>
      <div class="kv"><span class="k">${tr("大小")}</span><span class="v num">${fmtB(D.model.bytes)}</span></div>
      <div class="kv"><span class="k">${tr("格式版本")}</span><span class="v num">${D.model.version}</span></div>
      <div class="kv"><span class="k">${tr("骨骼 / 关键帧")}</span><span class="v num">${D.model.bones} / ${D.model.keys.toLocaleString()}</span></div>
      <div class="divider"></div>
      <div class="kv"><span class="k">Chunk</span><span class="v">${D.model.chunks.length} 个</span></div>
      <div class="chips">${D.model.chunks.map(c=>`<span class="chip">${c}</span>`).join("")}</div>`;
    return;
  }

  if (S.sel.kind === "textures") {
    const t = D.textures[S.sel.index];
    if (!t) { box.innerHTML = '<div class="empty">未选中</div>'; return; }
    const key = `tex:${t.index}:path`;
    const st = S.changes.get(key);
    const cur = st ? st.to : t.path;
    const refs = D.materials.flatMap(m => m.layers).filter(l =>
      l.textureId === t.index || Object.values(l.hd).includes(t.index)).length;
    box.innerHTML = `
      ${texBig(t.index)}
      ${window.MDX_TEX && window.MDX_TEX.entries[t.index]
        ? `<div class="kv"><span class="k">${tr("来源")}</span><span class="v">${
            t.replaceableId
              ? tr("游戏按玩家颜色即时生成，模型里没有这个文件")
              : tr("本地文件或游戏数据")}</span></div>`
        : ""}
      ${t.replaceableId
        ? `<div class="kv"><span class="k">${tr("路径")}</span>
             <span class="v">${esc(texPathLabel(t))}</span></div>`
        : `<div class="field"><label>${tr("路径")}</label>
             <input id="fPath" class="mono" value="${esc(cur)}"></div>`}
      ${st ? `<div class="diffbox"><div class="row"><span class="k" style="color:#7d8794">${tr("改动前")}</span>
        <span class="old">${esc(st.from)}</span></div></div>` : ""}
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("类型")}</span>
        <span class="v"><span class="lb ${CLS_CLASS[t.cls]}">${CLS_LABEL[t.cls]}</span></span></div>
      <div class="kv"><span class="k">${tr("判定依据")}</span><span class="v">${esc(trReason(t.reason))}</span></div>
      ${t.replaceableId ? `<div class="kv"><span class="k">replaceableId</span><span class="v num">${t.replaceableId}</span></div>` : ""}
      <div class="kv"><span class="k">${tr("被引用")}</span><span class="v num">${refs} ${tr("处")}</span></div>
      <div class="kv"><span class="k">${tr("写入块")}</span><span class="v">TEXS · ${tr("定长 256 B")}</span></div>
      <div class="divider"></div>
      <div class="btn-row">
        <button class="btn sm" id="lockBtn">${S.locks.has(t.index) ? tr("取消锁定") : tr("锁定不改")}</button>
      </div>`;
    if (el("fPath")) {
      el("fPath").oninput = e =>
        setChange(key, "textures", t.path, t.path, e.target.value, "TEXS");
    }
    el("lockBtn").onclick = () => {
      if (S.locks.has(t.index)) S.locks.delete(t.index); else S.locks.add(t.index);
      renderAll();
    };
  }

  else if (S.sel.kind === "materials") {
    const m = D.materials[S.sel.index];
    if (!m) { box.innerHTML = '<div class="empty">未选中</div>'; return; }
    const l = m.layers[S.sel.layer];
    if (!l) { box.innerHTML = '<div class="empty">未选中</div>'; return; }
    const key = `mat:${m.index}:${l.index}`;
    const st = S.changes.get(key);
    const cur = st ? st.to.split("|") : [l.filter, l.alpha];
    const hd = Object.entries(l.hd);
    box.innerHTML = `
      <div class="kv"><span class="k">${tr("材质 / 图层")}</span><span class="v num">#${m.index} · ${tr("层")} ${l.index}</span></div>
      <div class="kv"><span class="k">priorityPlane</span><span class="v num">${m.priorityPlane}</span></div>
      <div class="divider"></div>
      <div class="field"><label>FilterMode</label>
        <select id="fFilter">${FILTERS.map(f => `<option ${f===cur[0]?"selected":""}>${f}</option>`).join("")}</select></div>
      <div class="field"><label>Alpha</label>
        <input id="fAlpha" type="number" min="0" max="1" step="0.05" value="${(+cur[1]).toFixed(2)}"></div>
      ${st ? `<div class="diffbox">
        <div class="row"><span class="k" style="color:#7d8794">${tr("改动前")}</span>
          <span class="old">${esc(st.from.split("|")[0])} / α ${esc(st.from.split("|")[1])}</span></div>
        <div class="row"><span class="k" style="color:#7d8794">新值</span>
          <span class="new">${esc(cur[0])} / α ${(+cur[1]).toFixed(2)}</span></div></div>` : ""}
      <div class="divider"></div>
      <div class="field"><label>${tr("漫反射")}</label>
        <input value="${esc((textureOf(l.textureId)||{}).path || "—")}" readonly class="mono" style="color:#7d8794"></div>
      ${hd.map(([k, v]) => `<div class="field"><label>${esc(k)}</label>
        <input value="${esc((textureOf(v)||{}).path || "—")}" readonly class="mono" style="color:#7d8794"></div>`).join("")}
      ${l.hdTrusted ? "" : `<div class="chip ext">HD 槽不可信</div>`}
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("两面")}</span><span class="v">${l.twoSided?tr("是"):tr("否")}</span></div>
      <div class="kv"><span class="k">${tr("无光照")}</span><span class="v">${l.unshaded?tr("是"):tr("否")}</span></div>`;
    const commit = () => {
      const f = el("fFilter").value;
      const a = Math.max(0, Math.min(1, parseFloat(el("fAlpha").value) || 0));
      setChange(key, "materials", `材质 ${m.index} 层 ${l.index}`,
        `${l.filter}|${l.alpha}`, `${f}|${a}`, "MTLS");
    };
    el("fFilter").onchange = commit;
    el("fAlpha").oninput = commit;
  }

  else if (S.sel.kind === "sequences") {
    const s = D.sequences[S.sel.index];
    if (!s) { box.innerHTML = '<div class="empty">未选中</div>'; return; }
    const key = `seq:${s.index}`;
    const st = S.changes.get(key);
    const v = st ? st.to.split("|") : [s.start, s.end, s.loop ? "1" : "0"];
    box.innerHTML = `
      <div class="field"><label>${tr("名称")}</label><input value="${esc(s.name)}" readonly style="color:#7d8794"></div>
      <div class="row3" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="field"><label>${tr("起 (ms)")}</label><input id="fStart" type="number" value="${v[0]}"></div>
        <div class="field"><label>${tr("止 (ms)")}</label><input id="fEnd" type="number" value="${v[1]}"></div>
      </div>
      <div class="kv"><span class="k">${tr("时长")}</span><span class="v num">${(+v[1] - +v[0]).toLocaleString()} ms</span></div>
      <div class="field"><label><input type="checkbox" id="fLoop" ${v[2]==="1"?"checked":""} style="width:auto"> ${tr("循环")}</label></div>
      ${st ? `<div class="diffbox"><div class="row"><span class="k" style="color:#7d8794">${tr("改动前")}</span>
        <span class="old">${s.start}–${s.end}${s.loop?" "+tr("循环"):" "+tr("单次")}</span></div>
        <div class="row"><span class="k" style="color:#7d8794">新值</span>
        <span class="new">${v[0]}–${v[1]}${v[2]==="1"?" "+tr("循环"):" "+tr("单次")}</span></div></div>` : ""}
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("写入块")}</span><span class="v">SEQS · ${tr("定长 132 B")}</span></div>
      <div class="btn-row"><button class="btn sm" id="goto">${tr("跳到该段")}</button></div>`;
    const commit = () => {
      const a = parseInt(el("fStart").value) || 0, b = parseInt(el("fEnd").value) || 0;
      setChange(key, "sequences", s.name, `${s.start}|${s.end}|${s.loop?1:0}`,
        `${a}|${b}|${el("fLoop").checked?1:0}`, "SEQS");
    };
    el("fStart").oninput = commit; el("fEnd").oninput = commit;
    el("fLoop").onchange = commit;
    el("goto").onclick = () => { S.seq = s.index; renderTimeline(); };
  }

  else {
    const a = D.attachments[S.sel.index];
    if (!a) { box.innerHTML = '<div class="empty">未选中</div>'; return; }
    const key = `atc:${a.index}:mount`;
    const st = S.changes.get(key);
    const cur = st ? st.to : a.mount;
    box.innerHTML = `
      <div class="field"><label>${tr("名称")}</label><input id="fName" value="${esc(a.name)}"></div>
      <div class="field"><label>${tr("父节点")}</label>
        <input value="${esc(a.parentName)}  #${a.parentId}" readonly style="color:#7d8794" class="mono"></div>
      <div class="row3" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="field"><label>${tr("编号")}</label><input value="${a.objectId}" readonly style="color:#7d8794"></div>
        <div class="field"><label>attachmentId</label><input value="${a.attachmentId}" readonly style="color:#7d8794"></div>
      </div>
      <div class="field"><label>${tr("挂载模型")}</label><input id="fMount" class="mono" value="${esc(cur||"")}"></div>
      ${st ? `<div class="diffbox"><div class="row"><span class="k" style="color:#7d8794">${tr("改动前")}</span>
        <span class="old">${esc(st.from || tr("（空）"))}</span></div></div>` : ""}
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("写入块")}</span><span class="v">ATCH · ${tr("定长 256 B")}</span></div>
      <div class="btn-row">
        <button class="btn sm accent" id="mountPick">选武器</button>
        <button class="btn sm" id="focusBtn">定位</button>
      </div>`;
    el("fMount").oninput = e => setChange(key, "attachments", a.name, a.mount, e.target.value, "ATCH");
    el("mountPick").onclick = () => openMount(a.index);
    el("focusBtn").onclick = () => toast(`视口已定位到 ${a.name}`);
  }
}

/* ---------------- 时间轴 ---------------- */
function renderTimeline() {
  const track = el("tlTrack");
  if (!D.sequences || !D.sequences.length) {
    track.innerHTML = '<div style="padding:10px 0;color:#525c68;font-size:11px">'
      + tr("该模型没有动画序列") + '</div>';
    if (el("tlCur")) el("tlCur").textContent = "—";
    if (el("tlBands")) el("tlBands").innerHTML = "";
    return;
  }
  if (!(S.seq >= 0 && S.seq < D.sequences.length)) S.seq = 0;
  track.innerHTML = D.sequences.map(s => {
    const left = (s.start / TOTAL) * 100, w = ((s.end - s.start) / TOTAL) * 100;
    const st = S.changes.get(`seq:${s.index}`);
    const on = S.seq === s.index;
    return `<div class="tl-seg ${on?"on":""}" data-i="${s.index}"
      style="left:${left}%;width:calc(${w}% - 3px)">
      <span class="nm">${esc(s.name)}</span>${s.loop?'<span class="lp">↻</span>':""}
      ${st?'<span class="lp" style="color:#8fd08a">●</span>':""}
    </div>`;
  }).join("");

  const keys = D.sequences.map((s, i) => {
    const n = Math.max(3, Math.round((s.end - s.start) / 420));
    let h = "";
    for (let k = 0; k < n; k++) {
      const seed = Math.sin((i * 37 + k * 91)) * 10000;
      const hh = 3 + (Math.abs(seed) % 10);
      const x = (s.start / TOTAL) * 100 + ((s.end - s.start) / TOTAL) * 100 * (k / n);
      h += `<i style="position:absolute;left:${x}%;bottom:0;width:1px;height:${hh}px;background:#33414e"></i>`;
    }
    return h;
  }).join("");
  track.insertAdjacentHTML("beforeend", `<div class="tl-keys">${keys}</div>`);

  track.querySelectorAll(".tl-seg").forEach(node => {
    node.onclick = () => {
      S.seq = +node.dataset.i;
      S.sel = { kind: "sequences", index: S.seq };
      const sq = D.sequences[S.seq];
      R.playing = false;
      R.dir = 1;
      setTime(sq.start);
      renderAll();
      if (S.autoPlay && R.group) togglePlay(true);
    };
  });

  const bands = el("tlBands");
  if (bands) {
    bands.innerHTML = D.sequences.map(s =>
      `<i class="${S.seq === s.index ? "on" : ""}" style="width:${((s.end - s.start) / TOTAL * 100).toFixed(3)}%"></i>`
    ).join("");
  }

  const cur = D.sequences[S.seq];
  el("tlCur").innerHTML = `<b>${esc(cur.name)}</b> ${cur.start}–${cur.end} ms · ` +
    `${(cur.end - cur.start).toLocaleString()} ms · ${cur.loop ? tr("循环") : tr("单次")}`;
  updateTimeHud();
}

/* ---------------- 批量改路径 ---------------- */
function openBulk() {
  const ext = D.textures.filter(t => t.cls === "external" && !S.locks.has(t.index));
  const skips = D.textures.filter(t => t.cls !== "external" || S.locks.has(t.index)).length;
  el("overlay").classList.add("on");
  el("dialog").className = "dialog wide";
  el("dialog").innerHTML = `
    <div class="dlg-head">一键改外部贴图路径</div>
    <div class="dlg-body">
      <div class="seg" id="modeSeg" style="align-self:flex-start">
        <button data-m="prefix" class="on">统一前缀</button>
        <button data-m="replace">查找替换</button>
      </div>
      <div class="field" id="p1"><label>${tr("前缀")}</label>
        <input id="prefixIn" class="mono" value="MyMod\\"></div>
      <div class="field" id="p2" style="display:none"><label>${tr("查找")}</label>
        <input id="findIn" class="mono" value="AF\\">
        <label style="margin-top:6px">替换为</label>
        <input id="replIn" class="mono" value="MyMod\\"></div>
      <div class="field"><label><input type="checkbox" id="flatIn" checked style="width:auto"> 压平子目录（只留文件名）</label></div>
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("命中")}</span><span class="v" id="hitN">—</span></div>
      <div class="kv"><span class="k">${tr("跳过")}</span><span class="v num">${skips} ${tr("条（内置 / 可替换 / 已锁定）")}</span></div>
      <div class="chg-list" id="previewList"></div>
    </div>
    <div class="dlg-foot">
      <div class="spacer"></div>
      <button class="btn" id="cancel">取消</button>
      <button class="btn accent" id="apply">应用</button>
    </div>`;

  const recalc = () => {
    const mode = el("modeSeg").querySelector(".on").dataset.m;
    const prefix = el("prefixIn").value;
    const find = el("findIn").value, repl = el("replIn").value;
    const flat = el("flatIn").checked;
    const out = [];
    ext.forEach(t => {
      let p;
      if (mode === "prefix") {
        const base = flat ? t.file : pTail(t.path);
        p = prefix.replace(/\\+$/,"") + "\\" + base;
      } else {
        p = t.path.split(find).join(repl);
      }
      if (p !== t.path) out.push([t, p]);
    });
    el("hitN").textContent = `${out.length} 条`;
    el("previewList").innerHTML = out.slice(0, 40).map(([t, p]) =>
      `<div class="chg-item"><span class="p">#${t.index}</span>
        <span class="o" title=tr("改动前")>${esc(t.path)}</span>
        <span class="arrow">→</span>
        <span class="a" title=tr("改动后")>${esc(p)}</span></div>`).join("") ||
      `<div class="chg-item" style="color:#525c68">无命中</div>`;
    return out;
  };
  const pTail = p => p.split("\\").slice(1).join("\\") || p;

  el("modeSeg").querySelectorAll("button").forEach(b => b.onclick = () => {
    el("modeSeg").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    el("p1").style.display = b.dataset.m === "prefix" ? "" : "none";
    el("p2").style.display = b.dataset.m === "replace" ? "" : "none";
    recalc();
  });
  ["prefixIn","findIn","replIn","flatIn"].forEach(id => el(id).oninput = recalc);
  recalc();

  el("cancel").onclick = closeDialog;
  el("apply").onclick = () => {
    const out = recalc();
    out.forEach(([t, p]) => S.changes.set(`tex:${t.index}:path`,
      { scope:"textures", label:t.path, from:t.path, to:p, chunk:"TEXS" }));
    closeDialog();
    renderAll();
    toast(`已应用 ${out.length} 条改动（尚未写入文件）`);
  };
}

/* ---------------- 挂载武器 ---------------- */
function openMount(idx) {
  const a = D.attachments[idx];
  el("overlay").classList.add("on");
  el("dialog").className = "dialog";
  el("dialog").innerHTML = `
    <div class="dlg-head">挂载武器 → ${esc(a.name)}</div>
    <div class="dlg-body">
      <div class="field"><label>${tr("武器来源")}</label>
        <div class="seg" id="srcSeg" style="align-self:flex-start">
          <button data-s="game" class="on">游戏自带</button>
          <button data-s="local">本地文件</button>
        </div></div>
      <div class="field"><label>${tr("模型")}</label>
        <input id="wPath" class="mono" value="abilities\\weapons\\axe\\axemissile.mdx"></div>
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("附着点")}</span><span class="v">${esc(a.name)}</span></div>
      <div class="kv"><span class="k">${tr("父节点")}</span><span class="v">${esc(a.parentName)} #${a.parentId}</span></div>
      <div class="kv"><span class="k">${tr("写入")}</span><span class="v">ATCH path · 256 B</span></div>
      <div class="row"></div>
    </div>
    <div class="dlg-foot">
      <div class="spacer"></div>
      <button class="btn" id="cancel">取消</button>
      <button class="btn accent" id="apply">挂载</button>
    </div>`;
  el("cancel").onclick = closeDialog;
  el("srcSeg").querySelectorAll("button").forEach(b => b.onclick = () => {
    el("srcSeg").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    el("wPath").value = b.dataset.s === "game"
      ? "abilities\\weapons\\axe\\axemissile.mdx" : "war3mapImported\\Sword.mdx";
  });
  el("apply").onclick = () => {
    S.changes.set(`atc:${a.index}:mount`, {
      scope:"attachments", label:a.name, from:a.mount, to:el("wPath").value, chunk:"ATCH" });
    closeDialog(); renderAll();
    toast(`已挂载到 ${a.name}`);
  };
}

/* ---------------- 导出 ---------------- */
/* 把前端的改动表翻译成后端操作。
   key 的格式是固定的：tex:<i>:path / mat:<mi>:<li> / seq:<i> / atc:<i>:mount，
   值里拼着具体字段（mat 是 `filter|alpha`，seq 是 `start|end|loop`）。 */
function collectEdits() {
  const out = [];
  for (const [key, c] of S.changes) {
    const p = key.split(":");
    try {
      if (p[0] === "tex") {
        out.push({ kind: "tex", payload: { index: +p[1], path: String(c.to) } });
      } else if (p[0] === "mat") {
        const [f, a] = String(c.to).split("|");
        const payload = { mi: +p[1], li: +p[2] };
        const fi = FILTERS.indexOf(f);
        if (fi >= 0) payload.filter = fi;
        const af = parseFloat(a);
        if (!Number.isNaN(af)) payload.alpha = af;
        out.push({ kind: "layer", payload });
      } else if (p[0] === "seq") {
        const [st, en, lp] = String(c.to).split("|");
        out.push({ kind: "seq", payload: {
          index: +p[1], start: +st, end: +en, loop: lp === "1" } });
      } else if (p[0] === "atc") {
        out.push({ kind: "atch", payload: { index: +p[1], path: String(c.to) } });
      }
    } catch (err) {
      /* 单条解析失败就跳过，不阻塞整体保存 */
      if (window.console) console.warn(tr("跳过无法识别的改动"), key, err);
    }
  }
  return out;
}

/* ---------------- 游戏内置模型 ----------------
   直接列 CASC 里的模型。打开时由服务端读出来、复制到本机工作目录再载入，
   游戏目录始终只读——改动保存的是副本。 */

async function openGameBrowser(hd) {
  el("overlay").classList.add("on");
  el("dialog").className = "dialog wide";
  el("dialog").innerHTML = `
    <div class="dlg-head">游戏内置模型 · ${hd ? tr("重制版 HD") : tr("经典版 SD")}</div>
    <div class="dlg-body">
      <div class="field"><label>${tr("搜索")}</label>
        <input class="mono" id="gq" placeholder=tr("输入路径片段，例如 footman / heropaladin / townhall")></div>
      <div class="chg-list" id="glist" style="max-height:400px;overflow:auto">
        <div class="empty">读取游戏数据…</div>
      </div>
      <div class="hint" id="gstat"></div>
    </div>
    <div class="dlg-foot">
      <div class="hint">打开后复制到本机工作目录，改动不会写进游戏</div>
      <div class="spacer"></div>
      <button class="btn" id="cancel">取消</button>
    </div>`;
  el("cancel").onclick = closeDialog;

  let timer = null;
  el("gq").oninput = e => {
    clearTimeout(timer);
    timer = setTimeout(() => loadGameList(hd, e.target.value), 260);
  };
  await loadGameList(hd, "");
}

async function loadGameList(hd, q) {
  const box = el("glist"), stat = el("gstat");
  if (!box) return;
  box.innerHTML = '<div class="empty">读取中…</div>';
  let res;
  try {
    res = await (await window.MDX_API("/api/casc/models?hd=" + (hd ? 1 : 0) +
      "&q=" + encodeURIComponent(q || "") + "&limit=400")).json();
  } catch (err) {
    box.innerHTML = '<div class="empty">读取失败：' + esc(String(err.message || err)) + '</div>';
    return;
  }
  if (!res.ok) {
    box.innerHTML = '<div class="empty">' + esc(res.error || tr("读取失败")) + '</div>';
    return;
  }
  if (!res.items.length) {
    box.innerHTML = '<div class="empty">没有匹配的模型</div>';
    if (stat) stat.textContent = `共 ${res.total} 个模型，当前搜索无结果`;
    return;
  }
  box.innerHTML = res.items.map((p, i) => {
    const parts = p.split("\\");
    const name = parts.pop();
    const where = parts.slice(-2).join("\\");
    return `<div class="gitem" data-i="${i}" data-path="${esc(p)}" title="${esc(p)}">
      <span class="g-dir">${esc(parts[parts.length - 1] || "?")}</span>
      <span class="g-name">${esc(name)}</span>
      <span class="g-where">${esc(where)}</span>
    </div>`;
  }).join("");
  if (stat) {
    stat.textContent = `共 ${res.total} 个模型，匹配 ${res.matched} 个` +
      (res.truncated ? "，只显示前 400 个——再输入几个字缩小范围" : "");
  }
  box.querySelectorAll("[data-i]").forEach(node => {
    node.onclick = () => openGameModel(res.items[+node.dataset.i], hd);
  });
}

async function openGameModel(rel, hd) {
  el("dialog").innerHTML =
    `<div class="dlg-body"><div class="empty">从游戏数据里取出 ${esc(rel)}…</div></div>`;
  try {
    const j = await (await window.MDX_API("/api/casc/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: rel, hd: !!hd }),
    })).json();
    if (!j.ok) throw new Error(j.error || tr("打开失败"));
    location.reload();
  } catch (err) {
    toast("打开失败：" + (err.message || err));
    closeDialog();
  }
}

/* 打开文件
   窗口里（pywebview）直接调系统自带的文件对话框——这才是用户期望的"正常文件选择器"。
   纯浏览器环境拿不到本地完整路径，才退回服务端列目录。 */
function nativeApi() {
  return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
}

async function openPath(path) {
  el("overlay").classList.add("on");
  el("dialog").className = "dialog";
  el("dialog").innerHTML = '<div class="dlg-body"><div class="empty">正在打开…</div></div>';
  try {
    const j = await (await window.MDX_API("/api/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    })).json();
    if (!j.ok) throw new Error(j.error || tr("打开失败"));
    location.reload();
  } catch (err) {
    toast("打开失败：" + (err.message || err));
    closeDialog();
  }
}

async function openModelPicker(dir) {
  const api = nativeApi();
  if (api && api.pick_model) {
    const path = await api.pick_model();
    if (path) return openPath(path);
    return;                                  /* 用户取消了，什么都不做 */
  }

  /* 浏览器模式下的退路：没有本地路径权限，只能让服务端列目录 */
  el("overlay").classList.add("on");
  el("dialog").className = "dialog wide";
  el("dialog").innerHTML = '<div class="dlg-body"><div class="empty">读取目录…</div></div>';
  let res;
  try {
    res = await (await window.MDX_API("/api/browse" +
      (dir ? "?dir=" + encodeURIComponent(dir) : ""))).json();
  } catch (err) {
    toast("列目录失败：" + (err.message || err));
    return closeDialog();
  }
  if (!res.ok) { toast(res.error || tr("列目录失败")); return closeDialog(); }

  el("dialog").innerHTML = `
    <div class="dlg-head">打开模型</div>
    <div class="dlg-body">
      <div class="field"><label>${tr("位置")}</label>
        <input class="mono" value="${esc(dir || tr("此电脑"))}" readonly></div>
      <div class="chg-list" id="browseList" style="max-height:380px;overflow:auto">
        ${res.entries.map((e, i) => `
          <div class="gitem" data-i="${i}">
            <span class="g-dir">${e.dir ? tr("目录") : tr("模型")}</span>
            <span class="g-name">${esc(e.name)}</span>
            <span class="g-where">${e.dir ? "›" : fmtB(e.size)}</span>
          </div>`).join("")}
      </div>
    </div>
    <div class="dlg-foot">
      <div class="hint">只列出文件夹与 .mdx 文件</div>
      <div class="spacer"></div>
      <button class="btn" id="cancel">取消</button>
    </div>`;
  el("cancel").onclick = closeDialog;

  el("browseList").querySelectorAll("[data-i]").forEach(node => {
    node.onclick = () => {
      const e = res.entries[+node.dataset.i];
      if (e.dir) return openModelPicker(e.path);
      openPath(e.path);
    };
  });
}

/* 真正落盘：提交改动 → 校验写入 → 刷新页面。顶栏「保存」和导出的「写入文件」共用。 */
async function saveToDisk() {
  if (!S.changes.size) { toast("没有需要保存的改动"); return false; }
  const btn = el("saveBtnTop");
  if (btn) { btn.disabled = true; btn.textContent = tr("保存中…"); }
  try {
    const edits = collectEdits();
    if (edits.length) {
      const aj = await (await window.MDX_API("/api/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edits: edits }),
      })).json();
      if (!aj.ok) throw new Error(aj.error || tr("改动被拒绝"));
    }
    const sj = await (await window.MDX_API("/api/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })).json();
    if (!sj.ok) throw new Error(sj.error || tr("保存失败"));
    const r = sj.report;
    S.changes.clear();
    renderAll();
    toast(`已写入 ${r.changes.length} 处 · 长度 ${r.bytes_before} → ${r.bytes_after} B` +
          (r.backup ? " · 已备份" : ""));
    setTimeout(() => location.reload(), 1200);
    return true;
  } catch (err) {
    toast("写入失败：" + (err.message || err));
    if (btn) { btn.disabled = false; btn.textContent = tr("保存"); }
    return false;
  }
}

function openExport() {
  const list = [...S.changes.entries()];
  const groups = {};
  list.forEach(([k, c]) => (groups[c.chunk] = groups[c.chunk] || []).push([k, c]));
  const structural = Object.keys(groups).some(g => g !== "TEXS" && g !== "MTLS" && g !== "SEQS" && g !== "ATCH");
  el("overlay").classList.add("on");       /* 少了这句，弹窗内容写好了但根本不显示 */
  el("dialog").className = "dialog wide";
  el("dialog").innerHTML = `
    <div class="dlg-head">导出 · 待写入 ${list.length} 处</div>
    <div class="dlg-body">
      ${list.length === 0 ? `<div class="empty">没有改动，将透传原始字节。</div>` : ""}
      <div class="chg-list">
        ${Object.entries(groups).map(([chunk, items]) => `
          <div class="chg-group"><span>${chunk}</span>
            <span>${items.length} 处 · ${chunk==="ATCH"||chunk==="TEXS"?tr("定长覆写"):tr("字段覆写")}</span></div>
          ${items.map(([k, c]) => `<div class="chg-item"><span class="p">${chunk}</span>
            <span class="o" title=tr("改动前")>${esc(c.from || "—")}</span>
            <span class="arrow">→</span>
            <span class="a" title=tr("改动后")>${esc(c.to || "—")}</span></div>`).join("")}`).join("")}
      </div>
      <div class="divider"></div>
      <div class="kv"><span class="k">${tr("文件长度")}</span><span class="v num">${fmtB(D.model.bytes)} → ${fmtB(D.model.bytes)}（不变）</span></div>
      <div class="kv"><span class="k">${tr("校验")}</span><span class="v" style="color:#3fb950">长度不变 · 差异全部落在目标字段</span></div>
      <div class="kv"><span class="k">${tr("备份")}</span><span class="v">${esc(D.model.file)}.bak</span></div>
    </div>
    <div class="dlg-foot">
      <div class="hint">写入前自动备份</div>
      <div class="spacer"></div>
      <button class="btn" id="cancel">取消</button>
      <button class="btn accent" id="apply">写入文件</button>
    </div>`;
  el("cancel").onclick = closeDialog;
  el("apply").onclick = () => { closeDialog(); saveToDisk(); };
}

/* ---------------- 通用 ---------------- */
function openDialog(html) { el("dialog").innerHTML = html; el("overlay").classList.add("on"); }
function closeDialog() { el("overlay").classList.remove("on"); }
let toastTimer = null;
function toast(msg) {
  const t = el("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 2200);
}

function renderAll() {
  renderTree(); renderViewport(); renderTable(); renderInspector();
  renderTimeline(); renderStatus();
}

el("exportBtn").onclick = openExport;
/* 上下分栏拖动 */
(() => {
  const center = document.querySelector(".center");
  let drag = false;
  el("splitter").addEventListener("mousedown", e => { drag = true; e.preventDefault();
    document.body.style.cursor = "row-resize"; });
  window.addEventListener("mousemove", e => {
    if (!drag) return;
    const r = center.getBoundingClientRect();
    let pct = ((e.clientY - r.top) / r.height) * 100;
    pct = Math.max(16, Math.min(80, pct));
    center.style.gridTemplateRows = pct + "% 6px 1fr";
  });
  window.addEventListener("mouseup", () => { drag = false; document.body.style.cursor = ""; });
})();
el("dirtyChip").onclick = openExport;
if (el("openBtn")) el("openBtn").onclick = () => openModelPicker("");
if (el("openBtnTop")) el("openBtnTop").onclick = () => openModelPicker("");
if (el("gameSdBtn")) el("gameSdBtn").onclick = () => openGameBrowser(false);
if (el("gameHdBtn")) el("gameHdBtn").onclick = () => openGameBrowser(true);
if (el("langBtn")) {
  const isZh = window.MF_I18N.currentLang() === "zh";
  el("langBtn").textContent = isZh ? "EN" : "中文";
  el("langBtn").onclick = () => window.MF_I18N.setLang(isZh ? "en" : "zh");
}
if (el("saveBtnTop")) el("saveBtnTop").onclick = () => saveToDisk();

/* ---------------- 队伍色 ---------------- */
function setTeamColor(i) {
  S.team = Math.max(0, Math.min(23, i));
  localStorage.setItem("model-forge.team", String(S.team));
  if (el("teamDot")) el("teamDot").style.background = teamColorHex();
  document.querySelectorAll("#teamGrid .tp-cell").forEach(c =>
    c.classList.toggle("on", +c.dataset.team === S.team));

  /* 1) 遮罩式（Reforged）：只改 uniform，立刻生效，不重新请求任何贴图 */
  refreshTeamColor();

  /* 2) 经典模型直接把队伍色贴图当漫反射：那张图的缓存键变了，得换一张 */
  let swapped = 0;
  for (const m of R.meshes) {
    const tid = m.userData && m.userData.texIndex;
    const t = tid >= 0 ? D.textures[tid] : null;
    if (!t || !t.replaceableId) continue;
    delete TEXCACHE[tid];
    delete TEXCACHE_HI[tid];
    loadTexture(tid, tex => {
      if (!tex) return;
      m.material.map = tex;
      m.material.color.setHex(0xffffff);
      m.material.needsUpdate = true;
      R.dirty = true;
    });
    swapped++;
  }
  /* 3) 列表缩略图和大图都带 team 参数，重渲染一次就换成新颜色 */
  renderTable();
  renderInspector();
  if (!swapped) R.dirty = true;
}

function buildTeamPicker(colors) {
  const grid = el("teamGrid");
  if (!grid) return;
  grid.innerHTML = colors.map((c, i) =>
    `<div class="tp-cell ${i === S.team ? "on" : ""}" data-team="${i}"
          style="background:${c}" title="${i}"></div>`).join("");
  grid.querySelectorAll(".tp-cell").forEach(c => {
    c.onclick = () => { setTeamColor(+c.dataset.team); el("teamPop").classList.remove("on"); };
  });
}

if (el("teamBtn")) {
  if (el("teamDot")) el("teamDot").style.background = teamColorHex();
  el("teamBtn").onclick = e => {
    e.stopPropagation();
    el("teamPop").classList.toggle("on");
  };
  buildTeamPicker(TEAM_COLORS);
  /* 颜色以游戏数据为准（模组可能改过），拿不到就用手里的默认表 */
  window.MDX_API("/api/teamcolors").then(r => r.json()).then(j => {
    if (j && j.ok && Array.isArray(j.colors) && j.colors.length === 24) {
      for (let i = 0; i < 24; i++) TEAM_COLORS[i] = j.colors[i];
      buildTeamPicker(TEAM_COLORS);
      if (el("teamDot")) el("teamDot").style.background = teamColorHex();
    }
  }).catch(() => {});
  document.addEventListener("click", e => {
    const pop = el("teamPop");
    if (pop && pop.classList.contains("on") && !pop.contains(e.target)
        && !el("teamBtn").contains(e.target)) {
      pop.classList.remove("on");
    }
  });
}

if (el("curFile") && D.model) {
  el("curFile").textContent = D.model.file;
  el("curFile").title = D.model.path || "";
}
el("q").oninput = e => { S.query = e.target.value; renderTable(); };
el("overlay").onclick = e => { if (e.target.id === "overlay") closeDialog(); };
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeDialog(); return; }
  if (e.code === "Space") {
    const tag = (e.target && e.target.tagName) || "";
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag)) return;
    e.preventDefault();
    togglePlay();
  }
});

/* 初始化 */
if (!HAS_MODEL) {
  /* 没打开模型：界面保持可用（上面的按钮绑定已经生效），视口留空。
     不在这里放引导卡片——空着更干净，入口就在左侧和顶栏。 */
  el("fname").textContent = tr("未打开模型");
  el("fmeta").textContent = "";
  renderStatus();
} else {
  el("fname").textContent = D.model.file;
  el("fmeta").textContent = `${fmtB(D.model.bytes)} · ${D.model.chunks.length} chunks · ver ${D.model.version}`;
  if (D.origin && D.origin.from === "casc") {
    const ver = String(D.origin.source || "").includes("_hd.w3mod") ? "HD" : "SD";
    el("fmeta").textContent += ` · 取自游戏 ${ver} 版`;
  }
  S.sel = (D.textures && D.textures.length)
    ? { kind: "textures", index: Math.min(10, D.textures.length - 1) }
    : (D.attachments && D.attachments.length ? { kind: "attachments", index: 0 } : null);
  renderAll();
  try {
    initViewport();
  } catch (err) {
    window.__bootErr = (err && err.stack) || String(err);
    showFatal(tr("3D 初始化失败"), (err && err.message) || String(err));
    if (window.console) console.error(err);
  }
}

/* 自诊断：控制台执行 MDX_DEBUG 看状态 */
window.MDX_DEBUG = {
  /* 贴图加载的成败计数：ok 出图、fail 请求失败、blocked 浏览器拒收 */
  get texstat() { return { ok: TEXSTAT.ok, fail: TEXSTAT.fail, blocked: TEXSTAT.blocked }; },

  /* 队伍色状态：tinted 是已经接上遮罩着色的材质数（ORM 加载完才 > 0） */
  get team() {
    let tinted = 0;
    for (const m of R.meshes) {
      const ud = m.material && m.material.userData;
      if (ud && ud.shader && ud.shader.uniforms && ud.shader.uniforms.uTeamColor) tinted++;
    }
    return { index: S.team, hex: teamColorHex(), tinted, total: R.meshes.length };
  },

  /* 高清贴图升级进度：pending 归零说明画面已经是最终质量 */  get texupgrade() {
    let done = 0, hi = 0;
    for (const k in TEXCACHE_HI) {
      if (TEXCACHE_HI[k]) { done++; if (TEXCACHE_HI[k] !== false) hi++; }
    }
    return { pending: TEX_UP_QUEUE.length + TEX_UP_BUSY, done, upgraded: hi };
  },
  get status() {
    const cv = el("vp");
    const r = cv ? cv.getBoundingClientRect() : { width: 0, height: 0 };
    return {
      three: T3 ? T3.REVISION : null,
      preview: S.preview,
      wireframe: R.meshes.length ? !!R.meshes[0].material.wireframe : null,
      model: !!MD,
      data: !!D,
      meshes: R.meshes.length,
      normalMaps: R.meshes.filter(m => m.material && m.material.normalMap).length,
      maps: R.meshes.filter(m => m.material && m.material.map).length,
      matType: R.meshes.length ? R.meshes[0].material.type : null,
      geometry: R.meshes.length ? R.meshes.reduce((s, m) => s + m.geometry.attributes.position.count, 0) : 0,
      cssSize: [Math.round(r.width), Math.round(r.height)],
      buffer: R.renderer ? [R.renderer.domElement.width, R.renderer.domElement.height] : null,
      camera: R.camera ? R.camera.position.toArray().map(v => +v.toFixed(2)) : null,
      target: R.target ? R.target.toArray() : null,
      dist: +R.dist.toFixed(2),
      time: Math.round(R.time),
      error: window.__bootErr,
    };
  },

  /* 离屏渲染一张 PNG（dataURL）。自动化验证画面用——WebGL 的默认画布
     在浏览器合成后就读不到像素了，必须渲染到 render target 再回读。 */
  snapshot(width) {
    if (!R.renderer || !R.scene || !R.camera) return null;
    const cv = R.renderer.domElement;
    const W = Math.min(width || 720, cv.width);
    const H = Math.max(2, Math.round(W * cv.height / cv.width));
    const rt = new T3.WebGLRenderTarget(W, H);
    R.renderer.setRenderTarget(rt);
    R.renderer.render(R.scene, R.camera);
    const buf = new Uint8Array(W * H * 4);
    R.renderer.readRenderTargetPixels(rt, 0, 0, W, H, buf);
    R.renderer.setRenderTarget(null);
    rt.dispose();
    const c2 = document.createElement("canvas");
    c2.width = W; c2.height = H;
    const ctx = c2.getContext("2d");
    const img = ctx.createImageData(W, H);
    for (let y = 0; y < H; y++) {                 // GL 原点在左下，翻过来
      const src = (H - 1 - y) * W * 4;
      img.data.set(buf.subarray(src, src + W * 4), y * W * 4);
    }
    ctx.putImageData(img, 0, 0);
    return c2.toDataURL("image/png");
  },

  /* 每个网格实际用的贴图：索引 + 上传时的像素尺寸 */
  get meshes() {
    return R.meshes.map(m => {
      const map = m.material && m.material.map;
      const nm = m.material && m.material.normalMap;
      return {
        name: m.name,
        tex: m.userData ? m.userData.texIndex : null,
        map: map && map.image ? [map.image.width, map.image.height] : null,
        normal: nm && nm.image ? [nm.image.width, nm.image.height] : null,
      };
    });
  },

  /* 相机拉近/拉远，倍数 < 1 是拉近。自动化看细节用 */
  zoom(f) {
    R.dist = Math.max(R.distMin, Math.min(R.distMax, R.dist * f));
    R.dirty = true;
    return R.dist;
  },
};
