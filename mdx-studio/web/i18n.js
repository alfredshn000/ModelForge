/* 界面文案的中英对照。
 *
 * key 就是中文原文——不用另起一套 key 名，改造时把 "保存" 换成 t("保存") 即可，
 * 漏掉的条目会原样显示中文，不会出现 "settings.save" 这种东西露给用户。
 *
 * 新增文案：在 zh / en 里各加一行，代码里写 t("中文原文")。
 */

(function () {
"use strict";
const I18N = {
  zh: {},                       // 中文是原文，不需要表

  en: {
    /* ---- 顶栏与状态 ---- */
    "未打开模型": "No model",
    "未改动": "No changes",
    "已改": "Modified",
    "校验通过": "Verified",
    "保存": "Save",
    "保存中…": "Saving…",
    "打开": "Open",
    "打开模型…": "Open model…",
    "导出": "Export",
    "写入": "Write",
    "写入文件": "Write to file",
    "撤销": "Undo",
    "重做": "Redo",

    /* ---- 视图与分类 ---- */
    "贴图": "Textures",
    "材质": "Materials",
    "序列": "Sequences",
    "附着点": "Attachments",
    "全部": "All",
    "内置": "Built-in",
    "外部": "External",
    "可替换": "Replaceable",
    "游戏按玩家颜色即时生成，模型里没有这个文件": "Generated at runtime from the player colour — there is no file in the model",
    "队伍颜色": "Team color",
    "队伍色": "Team color",
    "队伍颜色（运行时替换）": "Team color (replaced at runtime)",
    "队伍光晕（运行时替换）": "Team glow (replaced at runtime)",
    "可替换贴图 {n}": "Replaceable texture {n}",
    "模型": "Model",
    "游戏内置": "In-game",
    "经典版模型": "Classic models",
    "重制版模型": "Reforged models",
    "资源": "Resources",

    "属性": "Properties",
    "漫反射": "Diffuse",
    "前缀": "Prefix",
    "个": "items",
    "处": "refs",
    "定长 256 B": "fixed 256 B",
    "定长 132 B": "fixed 132 B",
    "条（内置 / 可替换 / 已锁定）": "(built-in / replaceable / locked)",

    /* ---- 视口 ---- */
    "实体": "Solid",
    "线框": "Wireframe",
    "播放": "Play",
    "暂停": "Pause",
    "（空格）": " (Space)",
    "跟随文件": "Follow file",
    "循环": "Loop",
    "单次": "Once",
    "往返": "Ping-pong",
    "循环：跟随文件 / 循环 / 单次 / 往返（点一下切换）":
      "Loop: follow file / loop / once / ping-pong (click to cycle)",
    "自动播": "Auto-play",
    "切换动画后会自动播放": "Auto-play after switching animation",
    "切换动画后不自动播放": "Don't auto-play after switching animation",
    "切换动画后自动播放": "Auto-play after switching",
    "动画第一帧": "First frame",
    "重置视角": "Reset view",
    "3D 初始化失败": "3D init failed",
    "渲染出错": "Render error",
    "几何数据未加载": "Geometry not loaded",
    "没有模型": "No model",
    "低模": "Flat color",

    "已改 {n} 处": "Modified {n}",
    "{a} 张在用 · 共 {b} 条": "{a} in use · {b} total",
    "贴图 {a}/{b}": "Textures {a}/{b}",
    "{v} 顶点 · {f} 三角 · {b} 骨骼 · {t}": "{v} verts · {f} tris · {b} bones · {t}",

    "层": "Layer",
    "无贴图": "no texture",
    "跳到该段": "Jump to this sequence",
    "新值": "After",
    "循环：": "Loop: ",

    /* ---- 表格列 ---- */
    "名称": "Name",
    "起 (ms)": "Start (ms)",
    "止 (ms)": "End (ms)",
    "时长": "Duration",
    "编号": "ID",
    "挂载模型": "Attached model",
    "父节点": "Parent",
    "来源": "Source",
    "判定依据": "Reason",
    "被引用": "Referenced by",
    "写入块": "Chunk",
    "文件": "File",
    "大小": "Size",
    "格式版本": "Format",
    "骨骼 / 关键帧": "Bones / Keys",
    "材质 / 图层": "Material / Layer",
    "两面": "Two-sided",
    "是": "Yes",
    "否": "No",
    "无光照": "Unlit",
    "命中": "Matched",
    "跳过": "Skipped",
    "锁定不改": "Lock",
    "取消锁定": "Unlock",
    "图层": "Layer",
    "FilterMode": "FilterMode",
    "Alpha": "Alpha",

    /* ---- 贴图相关 ---- */
    "路径": "Path",
    "类型": "Type",
    "运行时生成（队伍色）": "Generated at runtime (team color)",
    "本地文件或游戏数据": "Local file or game data",
    "无": "None",
    "未选中": "Nothing selected",
    "无法加载": "Failed to load",
    "贴图不可用": "Textures unavailable",
    "贴图加载中": "Loading textures",
    "部分贴图未加载": "Some textures are missing",
    "这些贴图读不出来": "These textures could not be read",
    "该贴图未能找到（本地与游戏数据都没有）":
      "Texture not found (neither local nor in game data)",
    "本地文件模式：贴图不可用": "Local file mode: textures unavailable",
    "浏览器禁止 file:// 页面读取本地图片。模型与动画正常，贴图用平均色代替。\n":
      "Browsers block local images on file:// pages. The model and animation still work; textures are shown as average colors.\n",
    "在 prototype 目录执行 python -m http.server 后访问即可看到完整贴图。":
      "Run python -m http.server in the prototype folder and open it over http to see full textures.",
    "对应部位用该贴图的平均色代替。检查 tex/ 下的文件是否存在，或用 start.bat 以本地服务打开。":
      "Affected parts use the texture's average color. Check that the files exist, or open via a local server.",

    /* ---- 弹窗 ---- */
    "打开模型": "Open model",
    "游戏内置模型": "In-game models",
    "经典版 SD": "Classic SD",
    "重制版 HD": "Reforged HD",
    "搜索": "Search",
    "位置": "Location",
    "输入路径片段，例如 footman / heropaladin / townhall":
      "Part of the path, e.g. footman / heropaladin / townhall",
    "读取中…": "Loading…",
    "读取失败": "Failed to read",
    "读取失败：": "Failed to read: ",
    "正在打开…": "Opening…",
    "读取目录…": "Reading folder…",
    "列目录失败": "Failed to list folder",
    "列目录失败：": "Failed to list folder: ",
    "此电脑": "This PC",
    "目录": "Folder",
    "没有匹配的模型": "No matching models",
    "打开失败": "Failed to open",
    "打开失败：": "Failed to open: ",
    "只列出文件夹与 .mdx 文件": "Folders and .mdx files only",
    "打开后复制到本机工作目录，改动不会写进游戏":
      "Copied to your local work folder; the game files are never modified",
    "取消": "Cancel",
    "挂载武器": "Attach weapon",
    "武器来源": "Weapon source",
    "游戏自带": "From game data",
    "本地文件": "Local file",
    "写入前自动备份": "A backup is made before writing",
    "没有改动，将透传原始字节。": "No changes — the file will be copied as-is.",
    "定长覆写": "Fixed-size rewrite",
    "字段覆写": "Field rewrite",
    "文件长度": "File size",
    "校验": "Validation",
    "备份": "Backup",
    "改动前": "Before",
    "改动后": "After",
    "跳过无法识别的改动": "Skip unrecognised changes",
    "一键改外部路径": "Rewrite external paths",
    "该模型没有动画序列": "This model has no animations",
    "（空）": "(empty)",
    "未实现": "Not implemented",

    /* ---- 保存与错误 ---- */
    "没有需要保存的改动": "Nothing to save",
    "改动被拒绝": "Change rejected",
    "保存失败": "Save failed",
    "写入失败：": "Write failed: ",
    "已写入": "Wrote",
    "处": "edits",
    "已备份": "backed up",
    "长度": "size",
    "已保存": "Saved",
    "无法加载": "Failed to load",
    "服务未响应": "Service not responding",
    "取模型数据失败": "Failed to load model data",
    "取渲染数据失败": "Failed to load render data",
    "加载失败：": "Load failed: ",
    "加载失败": "Load failed",
    "app.js 加载失败": "app.js failed to load",
  },
};

const LANG_KEY = "model-forge.lang";

/** 当前语言：localStorage 优先，其次跟随系统。 */
function currentLang() {
  const saved = localStorage.getItem(LANG_KEY);
  if (saved === "zh" || saved === "en") return saved;
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

/** 翻译。找不到就返回原文，绝不显示 key。 */
function translate(text, params) {
  if (!text) return text;
  const table = I18N[currentLang()];
  let out = (table && table[text] !== undefined) ? table[text] : text;
  if (params) {
    for (const k of Object.keys(params)) {
      out = out.replaceAll("{" + k + "}", params[k]);
    }
  }
  return out;
}


/** 后端给的"判定依据"是中文短语，英文界面下在这里转换。
 *  它是数据而不是报错，但用户看到一列中文会觉得没翻译完。 */
function translateReason(text) {
  if (!text || currentLang() === "zh") return text;
  let m = /^根目录 (.+)\\ 属于游戏内置资产$/.exec(text);
  if (m) return 'Root "' + m[1] + '" is a built-in game asset';
  m = /^根目录 (.+)\\ 不在游戏内置清单里$/.exec(text);
  if (m) return 'Root "' + m[1] + '" is not in the built-in list';
  m = /^replaceableId=(\d+)（(.+)）$/.exec(text);
  if (m) return "replaceableId=" + m[1] + " (" + m[2] + ")";
  if (text === "路径为空") return "Path is empty";
  if (text === "只有文件名、没有目录") return "File name only, no folder";
  return text;
}

/** 把页面上所有 data-i18n 的静态文案替换掉。 */
function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach(node => {
    const key = node.dataset.i18n;
    const val = translate(key);
    if (node.placeholder !== undefined && node.dataset.i18nAttr === "placeholder") {
      node.placeholder = val;
    } else if (node.dataset.i18nAttr === "title") {
      node.title = val;
    } else {
      node.textContent = val;
    }
  });
  document.documentElement.lang = currentLang() === "en" ? "en" : "zh-CN";
}

function setLang(lang) {
  localStorage.setItem(LANG_KEY, lang);
  location.reload();                 // 整页重建最省事，也避免半中半英
}

window.MF_I18N = {
  t: translate,
  currentLang: currentLang,
  setLang: setLang,
  applyStaticI18n: applyStaticI18n,
  reason: translateReason,
};
})();
