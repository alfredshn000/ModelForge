# Retera's Model Studio — 界面现代化重构可行性研究

> 研究对象：本地 `ReteraModelStudio_0.04.7a9`（release 包）+ 上游源码仓库 `Retera/ReterasModelStudio`（MIT，2026-09-23 仍有提交）
> 结论日期：2026-10-17 ｜ 目的：判断"用更新的技术重构 UI"是否可行、代价与路径

---

## 0. 结论先行

| 结论 | 内容 |
|---|---|
| **不建议** | 用 Web 技术（React/Electron/Three.js）全量重写。这是 15 万行、2284 个编译类、含自研 OpenGL 渲染管线的桌面工程，重写 ≈ 另起一个产品，单人 12–24 个月，且会丢掉 20 年的边界 case 修复 |
| **建议** | 走 **三层递进式改造**：L0 换外观（2–3 周）→ L1 换交互骨架（2–4 个月）→ L2 拆架构（按需）。每一步可独立交付、可截图验收、可回退 |
| **顺带解决** | 项目自述 MIT，但实际捆绑了 **InfoNode Docking Windows（GPL，2009 年停更）** 与 **JTattoo（GPL/商业双授权）**。UI 重构换掉这两个库，等于同时把授权问题洗干净——两件事是同一件事 |
| **真正的"旧"** | 不是配色。是 **205 处菜单项 / 670 处 JOptionPane 模态弹窗 / 76 处 JFileChooser 文件流程**，以及模型层（`Bone`、`Bitmap`、`Camera`、`AnimFlag`…32 个文件）直接弹窗的强耦合。只换皮肤能解决约 30% 的观感问题 |

---

## 1. 现状体检

### 1.1 技术栈与构建

| 项 | 实测 |
|---|---|
| 语言 / 字节码 | Java，**class major 61 = Java 17**（本地 jar 用 JDK17 编译） |
| 构建 | Gradle 7.3 wrapper，3 个子项目 `craft3data` / `craft3editor` / `matrixeater` |
| 打包 | jpackage 应用镜像，自带 JRE 17.0.2（`bin/` 里 62 个 DLL） |
| GUI 框架 | **Swing / AWT**，外观管理器 = **JTattoo 1.6.12**（Aluminium / Noire / HiFi / Acryl 等 13 套主题） |
| 停靠窗口 | **InfoNode Docking Windows 1.6.1（GPL，2009-02 构建）** |
| 布局 | MiG Layout 4.2（2005 年版本） |
| 3D 渲染 | **LWJGL 2.9.3**（2015 年 EOL）+ 固定管线 OpenGL 2.x，`BetterAWTGLCanvas` 嵌进 Swing |
| 其它 | RSyntaxTextArea 3.0.2（MDL 脚本编辑）、Nashorn 15.3（JS 脚本）、GlazedLists 1.11、image4j、DDS/BLP 插件 |
| 本机环境 | ❌ 未安装任何 JDK（`java` 不在 PATH），需先装 Temurin 17 才能构建 |

### 1.2 代码规模

| 模块 | 文件 | 行数 |
|---|---:|---:|
| `craft3data`（数据 + 大部分 GUI） | 739 | 120,000 |
| ├─ `wc3/gui`（**UI 主战场**） | 399 | **52,832** |
| ├─ `wc3/mdl`（MDL 模型 + 编辑） | 96 | 24,041 |
| ├─ `wc3/mdx`（MDX 二进制） | 83 | 10,830 |
| `craft3editor`（物编/触发器） | 156 | 9,888 |
| `matrixeater`（应用壳） | 135 | 20,955 |
| **合计** | **1,030** | **150,843** |

### 1.3 UI 陈旧度指标（grep 实测）

| 指标 | 数量 | 含义 |
|---|---:|---|
| `new JMenu(...)` | 205 | 2005 式菜单树承载主交互 |
| `JOptionPane` | **670** | 模态弹窗当主力反馈通道 |
| `JFileChooser` | 76 | 文件为中心，无"项目/工作区"概念 |
| `new JLabel` / `JButton` / `JPanel` | 188 / 115 / 109 | 手写像素级拼装 |
| `setPreferredSize`/`setMaximumSize`/`setMinimumSize` | 52/62/50 | 固定尺寸 → HiDPI 下必糊 |
| `new Font(...)` | 20 | 字体散落硬编码，无字阶体系 |
| `new Color(...)` | 153 | 无设计 token |
| `JTable` / `JTree` / `JTabbedPane` | 5 / 5 / 6 | 数据密集界面极少用表格化表达 |

### 1.4 结构性债务（决定"能走多远"）

| 问题 | 证据 |
|---|---|
| **上帝类** | `MainPanel.java` 263 KB（约 6–7k 行）、`ImportPanel.java` 139 KB、`EditableModel.java` 123 KB、`AnimFlag.java` 92 KB |
| **模型层污染 UI** | `wc3/mdl` 下 **32 个文件** `import javax.swing.JOptionPane`；`EditableModel` 第 429/472/506/654/996 行直接在模型逻辑里弹警告框 → 任何新 UI 都无法干净替换 |
| **无测试** | 仓库无 `src/test`、无 CI，改 UI 只能靠人眼回归 |
| **上游仍在动** | 2026-09-23 仍有提交（`Revert lighting, if it aint broke dont fix strategy`）→ 硬分叉会持续冲突 |
| **已有的重构缝** | `gui/modeledit/newstuff` 包（62 类）已是作者自己做的编辑模式抽象（`ModelEditor` / `SelectionManager` / 策略模式），可作为新面板的接入点，不必从零 |
| **GPL 污染** | `lib/idw-gpl.jar` manifest 明写 `InfoNode Docking Windows GPL 1.6.1`；JTattoo 为 GPL/商业双授权 |

---

## 2. 为什么"换皮肤"只解决 30%

界面显旧是三层叠加，改造难度逐层陡增：

| 层 | 症状 | 改造成本 | 解决后观感 |
|---|---|---|---|
| **L0 外观层** | JTattoo 拟物质感、12px 工具栏图标、无字阶、150% 缩放下发虚、深浅色不统一 | 2–3 周 | ⭐⭐⭐ 立刻"像 2020 年代的软件" |
| **L1 交互层** | 菜单树找功能、每个操作一个模态框、右侧用树+表单硬塞属性、无命令面板/无内联校验/无快捷键提示 | 2–4 个月 | ⭐⭐⭐⭐ 才叫"好用" |
| **L2 架构层** | 上帝类、模型层弹窗、无测试 → 每加一个现代控件都要绕开历史耦合 | 持续 | 决定后续能否持续演进 |

**只做 L0 的风险**：皮肤换完，用户第一句评价往往是"还是那个老软件，只是换成深色了"——因为 670 个弹窗的交互节奏没变。

---

## 3. 五条路线对比

| # | 路线 | 做法 | 工期（单人） | 风险 | 功能保真 | 评价 |
|---|---|---|---:|---|---|---|
| **A** | **原位美化（L0）** | FlatLaf + SVG 图标集 + 字阶 + HiDPI + 停靠页签重绘 + 弹窗收敛为 Toast | **2–3 周** | 极低 | 100% | ✅ **必做第一步**，投入产出比最高 |
| **B** | **Swing 壳重写（L1）** | 换掉 InfoNode → FlexDock(MIT)/自研；新 MainFrame：左资源树 / 中视口 / 右 Inspector；命令面板 Ctrl+K；模态框改内联校验；最近文件+工作区 | **2–4 个月** | 中 | ~95%（少数工具窗口延后） | ✅ **主推荐**，保留渲染与解析内核 |
| **C** | 混合：Swing 视口 + 内嵌 Web 面板 | JCEF / JavaFX WebView 只承载右侧属性面板与设置页，走薄桥接 | 2–3 个月 | 中高 | 90% | ⚠️ 仅当你要 CSS 级视觉自由度；双技术栈维护成本高 |
| **D** | 全量 Web/Electron 重写 | Java 引擎留后台，前端 Three.js 重画 | **12–24 个月** | 极高 | 50–70%（粒子/飘带/绑定/动画曲线要重做） | ❌ 不建议 |
| **E** | 换语言重写（C#/Avalonia、Rust/wgpu） | 全栈移植 | 24 个月+ | 极高 | 30% | ❌ 不建议 |

> 关键判断依据：3D 视口是通过 `BetterAWTGLCanvas` / `PerspectiveViewport`（52 KB）/ `AnimatedPerspectiveViewport`（63 KB）直连 LWJGL2 固定管线的。D 路线要重写这部分 + 全部 MDX/MDL/CASC 读写的交互语义，收益（"界面新"）远小于代价。

---

## 4. 推荐路线（B：分三段交付）

### L0 — 外观层（2–3 周，先出可看的效果）

| 动作 | 具体做法 |
|---|---|
| 换外观管理器 | JTattoo → **FlatLaf 3.x（Apache-2.0）**；保留"亮/暗/跟随系统"三档，删掉 13 套拟物主题 |
| 图标 | 全量替换工具栏/树/按钮图标为 **24px 扁平 SVG**（`flatlaf-extras` 的 SVG 支持），保留 WC3 语义色（队伍色、骨骼色） |
| 字阶 | 统一 **Inter / Noto Sans SC**，建 12/13/14/16/20 五级字阶 + 行高，干掉 20 处散落 `new Font` |
| HiDPI | 修 164 处 `setPreferredSize/Max/Min` 中的硬编码尺寸；开 `sun.java2d.uiScale` 自适应 |
| 停靠页签 | 重绘 InfoNode 的 tab / 标题栏为现代扁平（先换皮不换库，降低 L0 风险） |
| 弹窗 | 新增 `Notification` 层：校验失败、保存成功、警告改为**非模态底部 Toast + 字段内联红字**；`JOptionPane` 从 670 处收敛到 <80 处"真·需要用户决策"的场景 |
| 验收 | 主窗口 / 模型编辑 / 动画编辑三屏 before-after 截图；150% 缩放不糊；启动时间不劣化 |

### L1 — 交互骨架（2–4 个月）

| 动作 | 具体做法 |
|---|---|
| 停靠库 | InfoNode(GPL) → **FlexDock(MIT)** 或自研轻量分栏（**顺带解决授权**） |
| 主框架 | 三栏范式：左侧"资源/组件树（带模糊搜索）"、中间视口、右侧"**属性 Inspector**（分节折叠、即时生效、无弹窗）" |
| 命令化 | 菜单从 205 项收敛为 6 个一级菜单 + **命令面板 Ctrl+K**（"编辑 → 合并顶点"这类深层功能全部可搜）；所有命令带快捷键提示 |
| 文件流 | 引入"最近文件 / 工作区"，`JFileChooser` 从 76 处降到入口级；支持拖拽打开 |
| 数据选择器 | `gui/datachooser`（41 类）重构为"表格 + 过滤 + 右侧预览"，替代下拉/树硬选 |
| 接入点 | 复用 `gui/modeledit/newstuff` 的 `ModelEditor`/`SelectionManager` 抽象，新面板挂其事件，不碰渲染层 |

### L2 — 架构（按需，与 L1 交织）

| 动作 | 收益 |
|---|---|
| 拆 `MainPanel`（263 KB）为 View/Controller/EventBus | 后续每加一个现代控件不再被历史代码反噬 |
| `wc3/mdl` 去 Swing 依赖（32 文件）：`JOptionPane` → 抛异常/回调，由 UI 层统一呈现 | 模型层可测试，未来若真要走 Web 也不再是死结 |
| 引入 MDL/MDX round-trip 回归测试（JUnit） | UI 改造敢动刀的前提 |
| **不在此阶段动 LWJGL2**（另立专项：LWJGL3 + 现代 GL，与 UI 解耦） | 避免风险叠加 |

---

## 5. 选型替换清单（同时解决"旧"与"GPL"）

| 现用 | 问题 | 建议替换 | 许可 |
|---|---|---|---|
| JTattoo 1.6.12 | 拟物、2015 年后无维护、GPL/商业双授权 | **FlatLaf 3.x** | Apache-2.0 |
| InfoNode Docking 1.6.1 | GPL、2009 年停更、无 HiDPI | **FlexDock**（MIT）/ **DockingFrames**（LGPL-2.1）/ 自研分栏 | MIT / LGPL / — |
| MiG Layout 4.2 | 2005 年版本，与 FlatLaf 无冲突但 API 老 | 升级 MiG 11.x（API 基本兼容）或保持 | BSD 系 |
| RSyntaxTextArea 3.0.2 | 版本旧 | 升级 3.6.x | BSD-3 |
| LWJGL 2.9.3 | 2015 EOL，固定管线 | **独立专项**：LWJGL 3 + 现代 GL | BSD |
| Nashorn 15.3 | JDK17 已无内置，但项目已自带 | 保留 | UPL/GPL+CE |

> 法务建议：公开发布重构版时，**保留原作者署名、MIT 声明原文、Hive 页面链接**，并在 README 标注"UI 分支，非官方"。若不清掉 InfoNode/JTattoo，整个分发物在 GPL 传染下必须整体 GPL——L0/L1 恰好把这个坑填了。

---

## 6. 风险清单

| 风险 | 影响 | 应对 |
|---|---|---|
| 上游持续提交（2026-09 仍活跃） | 硬分叉后合并地狱 | 采用**薄补丁策略**：把改造做成 `ui/` 独立模块 + 少量 hook，尽量不改被上游频繁改动的文件 |
| 无测试、无 CI | 回归全靠人眼 | 先补 smoke test：打开 33 个内置模型 / 一次 MDX→MDL→MDX 往返 |
| 模型层弹窗耦合 | 新 UI 无法干净接管错误呈现 | L2 第一步就做 `JOptionPane` 抽取 |
| 本机无 JDK | 无法构建/调试 | 装 Temurin 17 + 用 `gradlew` 验证构建（Gradle 7.3 支持 JDK17） |
| 单人产能 | L1 易拖期 | 切成"每周一个可见模块"（先做 Inspector，再做命令面板），不追求一次改完 |
| 用户习惯迁移 | 老用户找不到功能 | 保留经典菜单布局作为可选"经典模式"，命令面板并存 3 个版本期 |

---

## 7. 建议的第一周动作

1. 装 **Temurin JDK 17**，`gradlew :matrixeater:run` 跑通本地可调试版本（这是所有后续的前提）。
2. 拉 `ui-modern` 分支，**只做 L0**：FlatLaf 接入 + 主窗口重绘，产出三屏 before/after 截图给你看效果，再决定是否投 L1。
3. 用现有 33 个内置模型做一次"打开/编辑/保存"人工冒烟清单，作为改造基线。
4. 明确 L1 的范围边界（哪些工具窗口本期不动），避免无限扩散。

---

## 8. 需要你拍板的三件事

| # | 决策点 | 选项 |
|---|---|---|
| 1 | **目标层级** | 只要好看（做到 L0）｜ 要好用（L0+L1，推荐）｜ 想做成可分发产品/跨平台（需追加 L2 + 授权清理） |
| 2 | **渲染是否同批动** | 不动 LWJGL2（推荐，UI 与渲染解耦）｜ 一并迁 LWJGL3（风险翻倍） |
| 3 | **分发方式** | 仅自用/内部（授权可从宽）｜ 公开发布（必须先清 GPL 依赖 + 署名合规） |

---

### 附：本次研究取证的源码路径

- 源码仓库：`https://github.com/Retera/ReterasModelStudio`（MIT，浅克隆于 `_research/rms-src`）
- 字节码版本：`craft3data-0.4.5.jar` → `GUITheme.class` major = 61（Java 17）
- 外观设置代码：`craft3data/.../gui/datachooser/DataSourceChooserPanel.java:1018-1077`
- 主题枚举：`craft3data/.../gui/GUITheme.java`
- 渲染接入点：`craft3data/.../gui/lwjgl/BetterAWTGLCanvas.java`、`gui/modeledit/PerspectiveViewport.java`
- 模型层弹窗：`craft3data/.../wc3/mdl/EditableModel.java:429,472,506,654,996`
- 已有重构缝：`craft3data/.../gui/modeledit/newstuff/`
