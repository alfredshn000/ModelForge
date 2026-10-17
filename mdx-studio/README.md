# ModelForge（方案1：不依赖 Blender）

打开 mdx → 改配置 → 保存。**不需要装 Blender**，双击就能用（打包后）。

作者：Alfred ｜ 协议：MIT（见仓库根目录 `LICENSE`）

```
python run.py                       # 自动挑端口并开窗口
python run.py E:/models/hero.mdx    # 直接打开某个模型
python run.py --port 8765           # 固定端口
python run.py --probe               # 打不开时先跑这个
```

---

## 它是什么

Retera's Model Studio 的界面又旧又缺功能，但真正卡人的不是界面，是**做配置类改动必须开 Blender**。
这个程序解决那一半：贴图路径、材质参数、序列区间、附着点挂载，全部在本地改完写回。

改几何 / 建模型 / 重绑蒙皮仍然是 Blender（`blender-addon/`，方案2）。两者共用同一个内核 `mdx-toolkit/mdxcore`。

## 结构

```
mdx-studio/
  brand.py         产品名 / 版本 / 作者 / 日志路径（改名只改这里）
  run.py           入口（打包后就是 exe 的入口）
  build.py         打包脚本（内核内嵌 + 前端外置）
  make_icons.py    从原始 logo 生成 exe 图标与标签页图标
  session.py       打开 → 改 → 存（纯逻辑，不依赖界面）
  server.py        本地 HTTP，5 类接口
  textures.py      贴图查找与解码（本地 / CASC / 四级缓存）
  extract.py       mdx → 界面数据 / 渲染数据
  assets/          model-forge.ico（exe 图标，7 个尺寸）
  web/
    index.html     外壳 + 启动引导
    app.js         界面逻辑（从原型演进而来）
    logo.png       界面里的品牌图标
    vendor/        three.js r147
  test_server.py   后端自测 46 项
  test_webdir.py   前端目录查找 14 项（含真实 HTTP 请求）
  test_ui.py       界面自动化 47 项（真窗口点真按钮，含真写盘）
  test_e2e.js      端到端（真实页面点击 → 检查磁盘字节）
```

## 游戏内置模型

侧栏「游戏内置」两个入口，直接列游戏 CASC 里的模型：

| 入口 | 容器 | 数量 |
|---|---|---|
| 经典版模型 | `war3.w3mod` | 3,458 个 mdx |
| 重制版模型 | `war3.w3mod\_hd.w3mod` | 5,034 个 mdx |

按路径片段搜索（`footman` / `heropaladin` / `townhall`），点一条即打开。
**打开时把模型复制到 `%LOCALAPPDATA%\ModelForge\models\`，游戏目录始终只读**——
你改的、保存的都是副本，不会写回游戏。

顶栏会标明「取自游戏 HD 版」，文件名带 `hd_` 前缀，一眼能认出这是从游戏里取出来的。

支持的贴图编码：DDS 的 DXT1 / DXT3 / DXT5 / ATI2(BC5) / 未压缩 RGBA，外加 BLP1。
游戏里 96 张实测样本：DXT5 54、ATI2 26、DXT1 16，全部能解。

## 测试

```bash
python test_server.py     # 后端接口与写盘（46 项）
python test_webdir.py     # 前端目录优先级（14 项）
python test_ui.py         # 真窗口里点真按钮（47 项）
```

`test_ui.py` 用 pywebview 自己的 `evaluate_js` 驱动，不需要装浏览器自动化工具：
启动真窗口 → 逐个点击 → 读真 DOM 和 `MDX_DEBUG.status`。原生文件对话框会被临时
替成固定返回值，否则测试会卡在弹窗上等人点。它覆盖了空状态、打开模型、四个视图、
搜索过滤、改贴图、改动计数、3D 网格数、时间轴走没走、三个弹窗，最后**真的写一次盘**
并校验长度不变、内容已变、备份存在。

**打开模型用系统自带的文件对话框**：`run.py` 通过 `js_api` 暴露 `pick_model`，
前端检测到 `window.pywebview.api` 就调它，不再用自制的盘符列表。纯浏览器模式下
拿不到本地完整路径，才退回服务端列目录。

## 打包：内核内嵌，前端外置

pywebview 窗口和系统浏览器加载的是**同一份页面**（`http://127.0.0.1:端口/`），
区别只是一个有地址栏、一个没有。所以「用浏览器」和「打包成 exe」不是二选一，
前端代码跟打不打包无关——只跟 `web/` 目录放在哪有关。

```
dist/ModelForge/
    ModelForge.exe     Python 内核 + mdxcore + 启动逻辑全在里面
    web/               前端，改完重启即生效，不用重新打包
    _internal/         Python 运行时，别动
```

| 改动 | 要重打包吗 |
|---|---|
| 界面布局、配色、按钮、three.js 材质 | **不用**，换 `web/` 里的文件，重启程序 |
| 新增一个 `/api/xxx` 接口、改 `mdxcore` 二进制逻辑 | 要 |

`web/` 的查找顺序（`server.resolve_web_dir`）：
`--web-dir` → `MDX_WEB_DIR` → exe 同目录 `web/` → exe 的 `_internal/web/` → 源码 `web/` → 内嵌兜底。
判定标准是目录里真有 `index.html`，所以外置目录被删了一半时不会挑到空壳白屏。

```bash
python build.py              # 开发版 dist/ModelForge/
python build.py --zip        # 上面那份 + ModelForge-win64.zip（整个文件夹发出去）
python build.py --single     # 单文件 exe（dist/single/ModelForge.exe，拷一个文件就能用）
python build.py --console    # 保留控制台窗口看日志
python build.py --check      # 只检查环境
```

打包前需要 `pip install pyinstaller pywebview`。不装 pywebview 也能跑，只是会退回系统浏览器。

### 单文件版是怎么做的（`--single`）

直接把主程序打成 `--onefile` **不行**：进程能起、服务也正常，但 pywebview 的窗口永远不出现——
卡在 `webview.start()` 里，无异常无日志。同样代码打成 onedir 5 秒内正常出窗口。
原因是 onefile 每次运行都解压到新的临时目录，WebView2 / .NET 桥接按路径加载托管程序集时对不上。

所以 `--single` 走的是**自解压启动器**：

```
ModelForge.exe（外层，19.7 MB，不含 pywebview）
   └── runtime.zip（内嵌，12.5 MB）
         └── 首次运行解压到 %LOCALAPPDATA%\ModelForge\runtime\<版本>\
               然后启动里面的 ModelForge.exe
```

外层只负责解压和转发参数，实际跑的仍是 onedir 结构，所以窗口、CASC、贴图全部正常。
版本号变了会解压到新目录，不影响正在用的旧版本。实测首次启动约 2 秒解压完成、随后出窗口；
之后直接命中缓存。

### 其它交付细节

**两种交付形态，按场景选：**

- 发给别人：`python build.py --single` → `dist/single/ModelForge.exe`，拷这一个文件即可。
- 自己迭代：`dist/ModelForge/` 整个文件夹（前端外置，改完刷新即生效）。

几个实测结论：

- **onedir 产物只拷 exe 出去一定闪退**。`_internal/` 里放着 Python 运行时，缺了它引导器会报
  `Failed to load Python DLL` 然后立刻退出。要单文件就用 `--single`，不要手动挑 exe。
- **PyInstaller 原生 `--onefile` 打主程序不可用**（窗口起不来，见上一节），单文件必须走自解压启动器。
- 重打包时上一次的产物会被挪到 `build-out/_old/`（不直接删）。PyInstaller 自己删旧目录时是
  一次性删一百多个文件，某些带安全策略的机器会把它拦下来，导致打包中断。

## 打不开的时候

```bash
ModelForge.exe --probe
```

打印是否 frozen、找到的前端目录、pywebview / clr / .NET 桥接是否齐、系统 WebView2 版本。
这个命令在窗口版里也会把结果写进日志。

日志位置：`%LOCALAPPDATA%\ModelForge\logs\app.log`（启动、崩溃、窗口各阶段都会记一行）。
启动失败会额外弹一个提示框指向这个文件——窗口版没有控制台，不这样处理就是无声崩溃。

加 `MDX_VERBOSE=1` 还会把三方库的日志写进同目录的 `debug.log`。

## 图标与品牌

```bash
python make_icons.py     # 从 ../ModelForge_logo_no_background.png 生成
```

原始 logo 是「图形 + ModelForge 字样」的竖排构图，而 16×16 的图标只有十几个像素，
带文字必然糊。脚本用 alpha 扫描找出图形与文字之间的空白带，只取图形部分再居中裁正方形，
产出 `assets/model-forge.ico`（16/24/32/48/64/128/256）和 `web/logo.ico`、`web/logo.png`。

名字、版本、作者都在 `brand.py` 里，打包时会写进 exe 的「属性 → 详细信息」。

## 为什么是「本地 HTTP」而不是远程服务

前端跑在 WebView 里，没有文件系统权限，只能通过 HTTP 让 Python 读写磁盘——而读写磁盘正是这个工具的全部价值。

| 方案 | 为什么不用 |
|---|---|
| Electron + Python sidecar | 两个进程、两套打包，体积 150 MB+ |
| Pyodide（Python 编译成 WASM） | **不能访问本地文件**，打不开 mdx、写不了盘、读不了 CASC |

安全边界：

- 只绑 `127.0.0.1`，局域网/外网都连不上
- 启动生成一次性 token，页面地址里带，**所有接口与静态资源都校验**（漏了会 403）
- 不发任何外部请求；关窗口即退出，不装服务、不自启

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面（`?token=`） |
| GET | `/static/*` | 静态资源 |
| GET | `/api/health` | 探活 + 游戏目录是否可用 |
| POST | `/api/open` | `{path}` → 打开模型，返回 meta |
| GET | `/api/meta` | 界面数据（含未保存改动） |
| GET | `/api/render` | 渲染数据（顶点/骨骼/动画，gzip） |
| GET | `/api/tex?i=&size=` | 贴图 PNG |
| POST | `/api/edit` | 单条改动（只记内存） |
| POST | `/api/apply` | 批量改动，**整体校验通过才生效** |
| POST | `/api/revert` | 放弃全部改动 |
| POST | `/api/save` | 校验 → 备份 → 写盘 |
| GET | `/api/browse?dir=` | 目录列表（打开文件的简易浏览器） |

## 保存是怎么保证安全的

改动先攒在内存（`session.pending`），`save()` 时才落盘：

1. `ChunkEditor` 定长覆写 → `check_delta` 断言「只有目标字段的字节变了」
2. 备份成 `<文件名>.bak`（已存在则加时间戳）
3. 写临时文件再 `move`，避免写一半断电留下残文件
4. 保存后**重新解析**，用磁盘上的真实字节替换内存状态

这四类字段全是定长（贴图路径 256B、材质 alpha 4B、序列区间 8B、附着点路径 256B），
所以文件长度永远不变——这比结构变更的校验严格得多，改错位置会被立刻拦下。

## 验证

**后端自测**（`python test_server.py`，39 项全过）：

```
打开 / 元数据 / 渲染 / 贴图 / 越界拒绝
改 4 类字段 → 保存 → 文件长度不变
所有差异都落在已改字段内（逐段核对 diff 范围）
备份存在且与改前字节一致
保存后重新解析、回读值与写入值一致
非法输入 7 类全部被拒（空路径 / 非法字符 / 超长 / 可替换贴图 / filter 越界 / alpha 越界 / 区间倒置）
```

**端到端**（`test_e2e.js`，真实页面点击）：

在页面上选中贴图 #10 → 输入新路径 → 点「导出」→ 点「写入文件」，然后由 Node 检查磁盘：

```
磁盘文件：1,855,389 → 1,855,389 B（长度不变）
字节差异 41 处，范围 8876–8918（就是那条路径字段）
备份存在，且与改前字节完全一致
副本回读 #10 = war3mapImported\David_Helm_Diffuse.dds
原素材 sha1 未变（改的是副本）
```

## 性能：贴图为什么能秒开

DDS 解码是纯 Python，1024² 全解要 **487 ms**，88 张贴图等于 40 秒——这是最初的实测。

解法是**跳块解码**：DXT 本来就是 4×4 块压缩，请求 256px 时每 4 个块只解 1 个，
直接得到小图（不是"先全解再缩"）：

| 请求尺寸 | 耗时 |
|---|---|
| 全尺寸 1024² | 493 ms |
| 512 | 123 ms |
| 256 | 31 ms |
| 128 | 8 ms |

再叠加三层缓存（原始字节 / 解码结果 / PNG），首屏从 40 秒降到 3 秒以内。

## 还没接的

| 项 | 现状 |
|---|---|
| pywebview 窗口 | 现在开系统浏览器；`run.py` 里已经写好 `_try_pywebview`，装了就直接用原生窗口 |
| 打包 exe | `pyinstaller --onefile --noconsole run.py`，还需要处理 three.js / app.js 的打包路径 |
| 游戏 CASC 浏览 | 后端 `/api/browse` 只列磁盘目录；从游戏数据里挑模型还没做界面入口 |
| 批量操作 | Blender 插件里有（批量改路径/解绑），网页版没搬 |
| 撤销/重做 | 只有「放弃全部改动」（`/api/revert`），没有逐步撤销 |

## 和方案2 的分工

| | 方案1（本目录） | 方案2（`blender-addon/`） |
|---|---|---|
| 启动 | 独立进程 | 寄生在 Blender 里 |
| 用途 | 贴图 / 材质 / 序列 / 附着点 | 几何 / 蒙皮 / 建模 |
| 共用 | `mdx-toolkit/mdxcore`（解析、写入、校验、CASC、贴图解码） | 同 |
