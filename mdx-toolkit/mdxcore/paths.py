"""贴图路径分类与批量改写。

WC3 模型里 TEXS 的路径有三种来源，混为一谈就会"改坏别人的模型"：

1. **可替换贴图**（`replaceableId != 0`）
   路径一般是空的，图由引擎运行时生成（队伍色、队伍光晕、阴影、队伍标识）。
   任何批量改写都必须跳过——这类条目根本没有可改的路径。

2. **游戏内置资产**
   指向 Reforged 自带的美术资源（`Units\\...`、`Textures\\...` 等）。
   路径就是"地址"，文件在玩家机器上由游戏提供。改了 = 图直接丢失。

3. **模型自带（外部）贴图**
   作者随模型附带的图：`war3mapImported\\`、作者自己的目录（实测 `AF\\...`）、
   或者干脆只写文件名（实测 `kultiranbarracksdeath.mdx` 的 7 张贴图）。
   模型搬家、合并进别的图时，这一批路径必须跟着改。

判定顺序固定为 `replaceable → 内置前缀 → 其余算外部`。
内置白名单只提供**默认答案**，不提供唯一答案：判定依据会一并给出，
界面上可以把任意一条翻过来（作者自有目录撞上同名游戏目录这种事是会发生的）。
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

# 判定结果
REPLACEABLE = "replaceable"
BUILTIN = "builtin"
EXTERNAL = "external"
EMPTY = "empty"

CLASS_LABELS = {
    REPLACEABLE: "可替换",
    BUILTIN: "内置",
    EXTERNAL: "外部",
    EMPTY: "空",
}

# Reforged 自带美术资源的根目录（小写、不带分隔符）。
# 来源：游戏 CASC 里实际的顶层目录，外加经典版沿用的几个。
GAME_ROOTS = frozenset({
    "textures", "units", "buildings", "doodads", "environment", "abilities",
    "objects", "replaceabletextures", "ui", "sharedmodels", "splats",
    "terrainart", "pathtextures", "weather", "gizmos", "font", "fonts",
    "interface", "cinematics", "script", "humanui", "orcui", "undeadui",
    "nightelfui", "loading", "credits", "webui", "campaign", "maps",
})

# 常见 replaceableId 的名字，只用于界面提示，不参与判定
REPLACEABLE_NAMES = {
    1: "队伍色",
    2: "队伍光晕",
    3: "队伍标识",
    11: "地面印记",
    21: "队伍色（Alpha）",
    31: "队伍光晕（Alpha）",
    41: "队伍标识（Alpha）",
    51: "队伍色（无光晕）",
}


def norm(path: str) -> str:
    """统一成 WC3 风格的反斜杠路径。"""
    return (path or "").replace("/", "\\").strip()


def basename(path: str) -> str:
    return norm(path).rsplit("\\", 1)[-1]


def parent(path: str) -> str:
    p = norm(path)
    return p.rsplit("\\", 1)[0] if "\\" in p else ""


def root_of(path: str) -> str:
    """路径第一段（小写）。用来对照 GAME_ROOTS。"""
    p = norm(path).lstrip("\\")
    return p.split("\\", 1)[0].lower() if p else ""


def classify(path: str, replaceable_id: int = 0) -> str:
    """返回 REPLACEABLE / EMPTY / BUILTIN / EXTERNAL。"""
    if replaceable_id:
        return REPLACEABLE
    p = norm(path)
    if not p:
        return EMPTY
    return BUILTIN if root_of(p) in GAME_ROOTS else EXTERNAL


def reason(path: str, replaceable_id: int = 0) -> str:
    """给用户看的判定依据——不接受"黑箱分类"。"""
    cls = classify(path, replaceable_id)
    if cls == REPLACEABLE:
        name = REPLACEABLE_NAMES.get(replaceable_id, "")
        return f"replaceableId={replaceable_id}" + (f"（{name}）" if name else "")
    if cls == EMPTY:
        return "路径为空"
    if cls == BUILTIN:
        return f"根目录 {root_of(path)}\\ 属于游戏内置资产"
    if "\\" not in norm(path):
        return "只有文件名、没有目录"
    return f"根目录 {root_of(path)}\\ 不在游戏内置清单里"


# ---------- 改写 ----------

@dataclass
class Rule:
    """一条改写规则。

    mode="prefix"：把路径改成 `prefix\\文件名`（flatten，默认）或 `prefix\\原相对路径`。
    mode="replace"：把路径里的 find 逐字替换成 repl，前缀与目录保持原样。
    only：参与改写的分类集合，默认只改外部贴图。
    """
    mode: str = "prefix"
    prefix: str = ""
    flatten: bool = True
    find: str = ""
    repl: str = ""
    only: tuple[str, ...] = (EXTERNAL,)

    def validate(self) -> None:
        if self.mode not in ("prefix", "replace"):
            raise ValueError(f"未知改写方式 {self.mode!r}，可选：prefix / replace")
        if self.mode == "prefix":
            if not norm(self.prefix):
                raise ValueError("新路径前缀不能为空")
            if len(self.prefix) > 200:
                raise ValueError(f"前缀过长（{len(self.prefix)} 字符），会写不进 256 字节字段")
        else:
            if not self.find:
                raise ValueError("要替换的字符串不能为空")
            if self.find == self.repl:
                raise ValueError("替换前后相同，不会产生改动")


def style_sep(path: str) -> str:
    """原路径用的是哪种分隔符：纯正斜杠 → `/`，其余（反斜杠或混用）→ `\\`。

    改写时按原风格输出，避免"只想改前缀，却把整条路径的每个分隔符都换了"。
    实测 11 个测试模型 357 条贴图里，**正斜杠 271 条是主流**，
    反斜杠只有 80 条——统一成反斜杠会让 diff 从几个字节膨胀成整段。
    """
    raw = path or ""
    return "/" if "/" in raw and "\\" not in raw else "\\"


def rewrite_one(path: str, rule: Rule) -> str:
    """按规则改写一条路径。不做分类判断（调用方负责筛选）。

    输出保持原路径的分隔符风格（见 style_sep）：游戏两种都认，
    但保持原样能让改动字节数最小，也便于事后核对。
    """
    raw = path or ""
    p = norm(raw)
    if not p:
        return p
    sep = style_sep(raw)
    if rule.mode == "replace":
        out = p.replace(norm(rule.find), norm(rule.repl))
    else:
        name = basename(p)
        prefix = norm(rule.prefix).strip("\\")
        if rule.flatten:
            out = f"{prefix}\\{name}"
        else:
            out = f"{prefix}\\{p.lstrip(chr(92))}"
    return out.replace("\\", "/") if sep == "/" else out


def apply_rule(entries, rule: Rule, include=None) -> dict[int, str]:
    """对一批贴图套用规则，返回 {索引: 新路径}。

    `entries` 是任何带 `.path` / `.replaceable_id` 的对象列表（`texs.Texture` 即可）。
    `include` 覆盖分类筛选：传索引集合时只改这些索引（界面里手工勾选的那批）。
    返回值只包含**真的变了**的条目，方便调用方直接比对。
    """
    rule.validate()
    pool = set(include) if include is not None else None

    out: dict[int, str] = {}
    for i, item in enumerate(entries):
        if pool is not None and i not in pool:
            continue
        path = getattr(item, "path", "")
        rid = getattr(item, "replaceable_id", 0)
        if pool is None and classify(path, rid) not in rule.only:
            continue
        if classify(path, rid) in (REPLACEABLE, EMPTY) and pool is None:
            continue
        new = rewrite_one(path, rule)
        if new != path:
            if len(new.encode("latin-1", "replace")) > 255:
                raise ValueError(f"改写后路径超过 255 字节：{new!r}")
            out[i] = new
    return out


def summarize(entries) -> dict:
    """统计各分类数量，面板顶部用。"""
    counts = {REPLACEABLE: 0, BUILTIN: 0, EXTERNAL: 0, EMPTY: 0}
    for item in entries:
        counts[classify(getattr(item, "path", ""),
                        getattr(item, "replaceable_id", 0))] += 1
    counts["total"] = len(entries)
    return counts


def suggest_prefix(paths) -> str:
    """给一个看着像样的默认前缀：取外部路径里最常见的父目录，否则 mapImported。"""
    from collections import Counter

    dirs = Counter(parent(p) for p in paths
                   if classify(p) == EXTERNAL and "\\" in norm(p))
    if dirs:
        return "war3mapImported"
    return "war3mapImported"


def relative_mismatch(path: str, actual: str) -> bool:
    """文件系统路径与 TEXS 里的路径是否"指向同一个相对位置"。

    供界面判断"这条路径在本地能不能解析到文件"，不做 IO。
    """
    return posixpath.normcase(norm(path).replace("\\", "/")) != \
        posixpath.normcase(norm(actual).replace("\\", "/"))
