# ModelForge

**简体中文** ｜ [English](README.en.md)

面向《魔兽争霸 III》的模型工具链。内核 `mdxcore` 用纯 Python 实现 MDX 解析与**定点写入**：
不认识的块原样透传，只把改动过的字段落盘，写盘前校验长度与块完整性。

上层两个入口共用这套内核 —— `ModelForge`（本地服务 + 浏览器界面，可打包成双击即用的独立程序）
负责配置类改动，Blender 插件负责几何与蒙皮编辑。

支持经典版与重制版（HD）资源、CASC 直读、贴图分级加载与队伍色实时切换。

## 能做什么

| 场景 | 说明 |
|---|---|
| 贴图路径 | 单条修改 / 批量替换外部路径，自动识别 `<root>\` 归属、反斜杠与正斜杠混用 |
| 材质 | 图层过滤模式、透明度、法线 / ORM / 自发光槽位 |
| 序列区间 | 起止毫秒与循环标志 |
| 附着点 | 任意位置插入/删除，全表重编号并同步 PIVT |
| 挂载武器 | 改 ATCH 的挂载路径，进游戏确认跟手走 |
| 动画移植 | 骨骼配对（L1–L4 兜底）、整段动画从 A 模型搬到 B 模型、解绑 |
| 3D 预览 | three.js 真几何 + 蒙皮 + 关键帧 + 粒子，贴图 512→1024 渐进加载 |
| 游戏内置模型 | 直读 CASC 的 `war3.w3mod`（经典 3458 个）与 `_hd.w3mod`（重制版 5034 个） |

## 三条铁律

1. **不认识的块原样透传** —— 新版本 MDX 的新块不会因为解析不了而被丢掉
2. **只改改动过的部分** —— 未触及的浮点保留原字节，不重算
3. **写盘前校验** —— `check_delta`（长度不变）与 `check_chunk_rewrite`（只允许目标块变）

## 快速开始

```bash
# 从源码运行（需要 Python 3.10+，无第三方依赖）
cd mdx-studio
python run.py                          # 自动挑端口并开窗口
python run.py "D:/models/hero.mdx"     # 直接打开某个模型

# 打包成独立程序
python -m pip install pyinstaller pywebview
python build.py --zip                  # 输出 ModelForge-win64.zip
python build.py --single               # 单文件 exe（自解压启动器）
```

窗口后端是系统自带的 WebView2。没装也能跑，会退回系统浏览器，功能一致。

## 结构

```
mdx-toolkit/mdxcore/     纯 Python 内核，零依赖
  container.py           容器与块表
  chunks/                MDX 各块的解析与定点写入
  casc/                  CASC 存储读取（TVFS + 本地索引）
  dds.py blp.py          DXT1/3/5、ATI2、BLP1 解码，跳块解码提速
  paths.py               贴图路径分类与改写
  transfer.py            动画移植
  nodesedit.py           节点表插删与重编号
  verify.py              写前校验

mdx-studio/              独立编辑器（本地 HTTP 服务 + 浏览器前端）
  server.py session.py   接口与会话
  extract.py             mdx → 界面数据 / 渲染数据
  textures.py            贴图三级查找与磁盘缓存
  web/                   前端（原生 JS + three.js r147）
  build.py run.py        启动与打包

blender-addon/           Blender 插件（几何、蒙皮编辑）
docs/                    设计与实测文档
```

## 测试

```bash
cd mdx-studio
python test_server.py                  # 后端 46 项
python test_webdir.py                  # 前端目录解析 14 项
python test_ui.py                      # 真窗口点真按钮 82 项
```

`test_ui.py` 需要一个真实模型当素材。仓库里**不包含任何游戏素材**（版权原因），
请自备一个 `.mdx`，用环境变量指过去：

```bash
MDX_TEST_MODEL="D:/models/hero.mdx" python test_ui.py
```

## 已知限制

- 单文件模式用的是**自解压启动器**（外层 exe 释放运行时后启动内层）。直接
  PyInstaller `--onefile` 打包 pywebview 会卡死在 `webview.start()`，实测如此。
- 只处理**单个** MDX 文件，不支持 mdx + mdx2 双模型。
- BLP2 未实现解码（经典版贴图是 BLP1，重制版是 DDS）。
- 队伍色通过 ORM 贴图的 alpha 通道做遮罩染色（Reforged 的做法）；经典版模型走替换贴图那条路。

## 参考与致谢

本项目的**格式研究、界面取舍与若干实现思路**参考了
[Retera's Model Studio](https://github.com/Retera/ReterasModelStudio)（MIT）的成果：

- MDX 各块（尤其 MTLS 图层与 HD 贴图槽）的字段布局与版本差异
- 图层 FilterMode 到 OpenGL 混合状态的映射
- 可替换贴图（队伍色 / 队伍光晕）的换装路径 `ReplaceableTextures\TeamColor\TeamColorNN.blp`
- 视口操作手感与面板组织方式

Retera 的源码不包含在本仓库中，如需对照请访问其原始仓库。

## 许可

MIT，见 [LICENSE](LICENSE)。

本仓库不包含任何《魔兽争霸 III》的游戏素材。使用本项目需要你自行拥有该游戏。
