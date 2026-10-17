# 直读 Reforged 游戏数据（CASC）

> 目标：像 Retera 一样，不手动导出模型，直接打开游戏本体里的文件。

## 结论

**能做，而且已经做完了。** 纯 Python、零第三方依赖、约 1,300 行。

实测（你的安装：`3.0.0.24268`，`kr` 分支）：

| 指标 | 结果 |
|---|---|
| `war3.w3mod` 内文件 | **22,200** 个 |
| 其中 `.mdx` | **3,458** 个 |
| 其中 `.dds` | **7,015** 个 |
| 打开 + 列全量目录 | **0.14 秒** |
| 单个模型读取 | **平均 1.1 ms** |
| 30 个随机模型读取 + 解析 | **30/30 成功** |
| 模型引用的贴图命中率 | **97.6%**（40 命中 / 1 缺失） |
| Blender 导入 → 原样导出 | **10/10 字节完全相同**（含 23 MB 过场模型） |
| **全量体检**（解析→重建→逐字节比对） | **3,458 / 3,458 = 100%**，16 秒 |

---

## 一、Retera 是怎么做的

它把整套 CASC 读取器**内建在自己的代码里**（`com.hiveworkshop.blizzard.casc`，约 3,700 行），
不依赖任何外部库、不需要 StormLib：

```
.build.info              → 找到 Active 那行，取出 Build Key
Data/config/xx/yy/<key>  → 拿到 root / encoding / vfs-root / vfs-N 的 key
Data/data/*.idx          → EKey → (第几个 data 文件, 偏移, 长度)
Data/data/data.NNN       → BLTE 解码 → 原始字节
TVFS                     → 路径 → 文件内容的映射
```

关键的两处设计：

**1. 虚拟目录前缀。** 模型里写的 `Textures\Footman.blp` 不是完整路径，
真实路径要先套 6 个前缀逐个试：

```
war3.mpq  deprecated.mpq  <locale>-war3local.mpq
war3.w3mod  deprecated.w3mod  war3.w3mod\_locales\<locale>.w3mod
```

**2. `filealiases.json`。** 一张 `src → dest` 的别名表，放在 CASC 里，
用来把虚拟路径映射到实际文件——这也解释了之前那个 `.tif` 后缀之谜。

另外它的 CASC 实现里有两处"能用就行"的妥协：
BLTE 的哈希校验根本没做（`// TODO generate actual hash`，直接当通过），
压缩只支持 `'N'`（原样）和 `'Z'`（zlib），遇到 `'F'`（LZ4）或 `'E'`（加密）会直接抛异常。

---

## 二、安装与路径：必须要装游戏吗

### 不是必须的

CASC 只是**一个可选的来源**。插件有两条完全独立的入口：

| 入口 | 需要装游戏吗 | 说明 |
|---|---|---|
| **文件 > 导入 > Warcraft III MDX** | ❌ 不需要 | 打开任何 `.mdx` / `.mdl` 文件，和普通导入一样 |
| N 面板「导入 MDX」 | ❌ 不需要 | 同上，还能顺便指定贴图目录 |
| N 面板「游戏数据（CASC）」 | ✅ 需要 | 直接从游戏安装目录读，不用先手动导出 |

所以即使换台没装游戏的机器，外部模型照样能编辑——只是用不了"直接打开游戏本体"这一条。

### 为什么不能全自动

Windows 注册表里**没有** WC3 Reforged 的安装路径：

```
HKLM\SOFTWARE\WOW6432Node\Blizzard Entertainment\Warcraft III
    → 只有文件关联（Capabilities），没有 InstallLocation
HKCU\Software\Blizzard Entertainment\Warcraft III
    → 只有 Quenching / Allow Local Files / Preferred Game Version
HKLM\...\Uninstall
    → 没有任何 Warcraft 条目
```

（`HKCU\...\Launcher` 里倒是有 `Locale = zhCN`，可以拿来定语言子目录。）

所以路径只能靠三条路子，代码里按顺序试：

1. **上次用过的**（`mdx-toolkit/work/settings.json`，第一次选完就记住）
2. **常见位置**（`C://Program Files\Warcraft III`、`D://Games//War3 2.0` 等）
3. **扫一遍**（`<盘>:\Games\*` 和 `<盘>:\Program Files\*`，往下钻 3 层找 `.build.info`，约 7 秒）

实测你这台机器第 2 条就命中了，第一次不用手动指定。

### 目录可以填得比真正的安装位置浅

版本号常套好几层：

```
D://Games//War3 2.0\2.0.2.22796\2.0.2.22796\Warcraft III\   ← 真正的安装目录
```

填 `D://Games//War3 2.0` 也能用，代码会自动往下钻到含 `.build.info` 的那层。

### 手动指定的三种方式

| 方式 | 命令 |
|---|---|
| 面板 | N 面板 →「游戏数据（CASC）」→ 点「导入」→ 填安装目录 |
| 环境变量 | `set WC3_INSTALL=D://Games//War3 2.0` |
| 命令行 | `python tools/mdx.py casc --install "D://Games//War3 2.0"` |

选过一次之后会写进 `mdx-toolkit/work/settings.json`，下次自动带上。

### 顺带说明

- **离线可用**：读的是本机文件，不联网、不校验战网账号。
- **只读**：绝不修改游戏目录里的任何东西。导出的模型落在 `mdx-toolkit/work/casc/`。
- **卸载游戏后**：之前导入过的模型已经落盘，照样能打开；只是不能再从 CASC 取新的。
- **需要完整安装**：只要 `Data/data` 那 34 GB 在就行（不用整个 40 GB）。
  像 `D://Games//Re-reforged//_retail_` 这种「已解包散的资产」也能用——直接当普通文件夹导入即可。

---

## 三、我们的实现

```
mdx-toolkit/mdxcore/casc/
    blte.py        BLTE 容器解码（N / Z 两种模式）
    indexfile.py   .idx 本地索引
    tvfs.py        TVFS 目录清单（路径前缀树）
    storage.py     统一入口：路径 → 字节
```

对外就三个方法：

```python
from mdxcore.casc import open_casc, find_install

install = find_install()                    # 自动探测安装目录
with open_casc(install) as casc:
    casc.list_all()                         # 22,200 条路径
    casc.find(r"footman.*\.mdx$")           # 按正则找
    data = casc.read("units\\humanfootman\\footman.mdx")
```

命令行：

```bash
python tools/mdx.py casc                              # 版本、文件数
python tools/mdx.py casc --find "footman.*\.mdx$"     # 搜索
python tools/mdx.py casc --get "units\humanfootman\footman.mdx" --out ft.mdx
```

Blender 里：右侧 N 面板 →「游戏数据（CASC）」→ 选安装目录 → 搜索 / 导入。
导入会把模型和它引用的贴图一起取到 `mdx-toolkit/work/casc/<模型名>/`，
之后就跟打开本地文件完全一样。

---

## 四、六个必须踩过才知道的坑

### 1. 索引里两个字段字节序不同

`.idx` 的条目是 `key + dataOffset + fileSize`，但：

- `dataOffset` 是**大端**
- `fileSize` 是**小端**

统一按一种字节序读，偏移会解成 795 GB 这种不可能的数。

### 2. 每个数据块前有 30 字节块头

`data.NNN` 里，索引给的偏移指向的是**块头开头**，BLTE 从它后面 30 字节才开始：

```
[7 字节][9 字节 key 逆序][4 字节 size][10 字节]  BLTE...
```

实测 40 个随机样本，**40/40 都是 offset+30**。代码里仍做一次探测（64 字节窗口内找 `BLTE`），
不写死，防止别的 build 有差异。

### 3. TVFS 全部字段是大端

和索引相反。判据很好用：`pathOffset + pathSize`、
`fileRefOffset + fileRefSize`、`cascRefOffset + cascRefSize` 三组必须自洽——大端时
`15621 + 3201 = 18822` 正好等于文件长度，小端则是一堆天文数字。

### 4. 路径片段的拼接规则

TVFS 的路径前缀树里，一个节点可能带多个片段，**空片段代表一个反斜杠**：

| 节点片段 | 拼出的文本 |
|---|---|
| `['x86_64', '']` | `x86_64\` |
| `['blizzardbrowser.']` | `blizzardbrowser.` |
| `['app']` | `app`（直接接上，`blizzardbrowser.app`） |
| `['', 'contents', '']` | `\contents\` |

规则是：**同一节点内的片段用 `\` 连接，节点之间直接接续**。
节点之间再加分隔符，`blizzardbrowser.app` 就会被劈成 `blizzardbrowser.\app`。

### 5. ver 1800 的 GEOS：LOD 必选，TANG/SKIN 可选，且 SKIN 换成了宽格式

Retera 的 `ModelUtils` 硬编码了 `900/1000/1100/1200`，遇到 1800 会走错分支。
实测你的安装 3,456 / 3,458 都是 1800，所以必须改。

**LOD（i32 + name[80]）在 1800 里是必选的**，数字一分不差：

```
MATS 之后：
  12 字节   materialId + selectionGroup + selectionType
  84 字节   LOD（i32 + name[80]）      ← Retera 认为 1800 没有
  28 字节   boundsRadius + min + max
   4 字节   extent 数量 = 13
 364 字节   13 × 28
→ 13459 + 4 + 364 = 13827 = UVAS 的位置 ✓
```

**但 TANG / SKIN 是可选的，不能按版本判断。** 这里栽过一次：
footman 没有 TANG，于是我以为「1800 都没有 TANG」，结果全量体检时
113 个模型报 `期望 UVAS，实际 TANG`（cinematics 下的过场模型居多）。

正确做法是**探测标签**：读到就解析，读不到就跳过。实测全量里带 TANG 的有 383 个 geoset。

**SKIN 还换了格式**，这是最隐蔽的一处：

| | 旧（1000–1200） | 新（1800） |
|---|---|---|
| 每顶点 | 8 字节 | **16 字节** |
| 索引 | 4 × u8 | 4 × u16 |
| 权重 | 4 × u8（/255） | 4 × u16（刻度仍是 255） |
| `count` 字段 | 总字节数 | **一半**（索引区字节数 = 顶点数 × 8） |
| 数据长度 | `count` | `count × 2` |

判据不看版本，看「`count` 之后是不是紧跟 UVAS」：

```python
count = c.u32()
tail = data[c.p + count : c.p + count + 4]
skin = c.raw(count * 2 if tail != b"UVAS" else count)
```

新建模里实测：宽格式 933 个 geoset，窄格式 0 个（老格式只存在于 1000–1200 的老模型）。
权重是 u16 但刻度还是 255 —— 例如某顶点四个权重是 `190 + 61 + 4 = 255`。

---

### 6. 索引里的 file_size **包含**那 30 字节块头

这个坑极其隐蔽：中间块多读 30 字节时，zlib 会忽略多余字节，**看起来完全正常**；
只有文件末尾的最后一块会失败（读不满就返回空）。全量体检时正好抓到 2 例：

```
flamethrowermissile.mdx   声明 192 字节，实际读不到（文件末尾只剩 162）
a1m6c010_uthersbook.mdx   声明 2,580,994，实际 2,580,964
```

差 30 字节 = 块头长度。正确读法是 `file_size - 30`。

---

## 五、还顺手解决的两件事

### 贴图扩展名回退

模型里写着 `Textures\Footman.blp`，Reforged 里实际文件是 `textures\footman.dds`。
现在会按 `.blp → .dds → .tif → .png` 的顺序回退，命中率因此从 0% 提到了 97.6%。

### 多级嵌套容器

`war3.w3mod` 本身是一个 1.4 MB 的 TVFS，里面还套着 `_hd.w3mod`。
解析器改成逐级往下钻，任意层数都支持。

---

## 六、一个真实的数据损失陷阱

`units\humanpeasant\peasant.mdx` 第一次导入→导出，文件从 141,031 变成 140,199 字节。

原因：**WC3 允许同一毫秒出现两个关键帧**（做瞬时跳变用），而 Blender 的 F-Curve
会把同帧的 key 合并。15 根骨骼各少 1 个 key，正好 832 字节。

现在这类轨道（文件里有重复时间戳、且合并后数量正好等于唯一时间数）**整条原样透传**，
不参与比对也不重建。报告里会写明"n 条轨道含重复时间戳，原样保留"。

改完之后 peasant 也能字节完全相同了。

---

## 七、限制

| 项 | 说明 |
|---|---|
| 只支持 Reforged | 经典版的 MPQ 没做；你的安装只有 CASC，够用 |
| 只读 | 没有写回游戏数据的实现，也不建议做（会破坏游戏完整性） |
| `'F'` LZ4 / `'E'` 加密块 | 未实现；目前实测的块都是 `'N'` / `'Z'` |
| 索引哈希校验 | 跳过了（和 Retera 一样）。文件损坏时不会报错，会读到垃圾数据 |
| 语言 | locale 默认 `enUS`，只影响 `war3.w3mod\_locales\` 这一层前缀 |

---

## 八、验证怎么跑

```bash
# 全量体检：3,458 个模型逐个「解析 → 重建 → 逐字节比对」
python tools/audit_casc.py --report docs/CASC-全量体检.md

# 抽样统计：文件数、读取耗时、版本分布、贴图命中率
python tools/casc_stats.py --sample 30

# Blender 层：导入 → 原样导出 → 字节比对
blender --background --python "blender-addon/check_casc.py" -- \
    "units\humanfootman\footman.mdx" "units\humanpeasant\peasant.mdx"
```

`check_casc.py` 会逐个报告 geoset 数、骨骼数、贴图（存/载/图三层计数）、
以及导出字节是否与游戏里完全一致。

### 实测结果

| 项 | 结果 |
|---|---|
| 全量体检（3,458 个模型） | **100% 通过**，16 秒 |
| Blender 端到端 | **10/10 字节完全相同** |

覆盖的模型类型：过场动画（23 MB / 146 geoset / 2,696 骨骼）、
单位（footman / peasant / grunt / chaosgrunt / myrmidon / abomination）、
建筑（altarofkings）、装饰物（firepillar / smokesmudge）。

> 「100% 通过」的含义要说清：它证明的是**解析器没有漏字段**
> ——只要能重建出与原始完全相同的字节，就不可能有没读到的数据。
> 它不证明「游戏里显示正确」，后者仍然需要你把模型放进游戏看。
