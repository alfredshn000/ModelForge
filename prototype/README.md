# 方案1 界面原型（可点击）

## 怎么打开

**推荐：双击 `start.bat`** —— 它起一个本地服务再开浏览器，贴图能正常加载。

直接双击 `index.html` 也能用，但**贴图不会显示**：浏览器禁止 `file://` 页面把本地图片
上传到 WebGL（`SecurityError: image contains cross-origin data`）。这时模型会退化成
"平均色的低模"，并在视口顶部给出说明。正式客户端走本地服务，不存在这个问题。

## 四个模型（验证管线通用性）

`?m=<slug>` 或左栏下拉切换：

| slug | 模型 | 规模 | 用来验证 |
|---|---|---|---|
| `david` | Alexstrasza 英雄（作者改版） | 17,320 顶点 / 120 骨骼 / 10 序列 | 骨骼动画、贴图、法线 |
| `kultiranbarracks` | Kultiran 兵营 | 50,990 顶点 / 41 骨骼 / 5 序列 | 建筑、尺寸差三倍时的自动取景 |
| `fire` | 火焰发射器 | 0 顶点 / 1 个 PRE2 | 纯粒子模型 |
| **`halahk`** | **Halahk the Lifebringer（游戏自带）** | 26,295 顶点 / 120 骨骼 / 13 序列 | **从 CASC 取出 → 修改 → 预览** 的完整链路，ver 1800 |

每个模型一个目录：

```
models/<slug>/
  data.js         界面用（贴图/材质/序列/附着点）
  model.js        渲染用（顶点/骨骼/轨道/粒子/包围盒）
  textures.js     贴图元数据
  tex/*.png       贴图
  _source-*.mdx   从游戏数据取出的原始副本（只有 halahk 有）
```

`python build_models.py` 一次重建全部，`python build_models.py halahk` 只重建一个。

`halahk` 本地没有，`build_models.py` 会用 `casc.read()` 从游戏里读出来写到
`models/halahk/_source-HeroHalahk.mdx` 再处理。**CASC 全程只读，游戏目录一个字节都不写**
（`tools/test_flow_casc.py` 会在前后拍 Data 目录快照来证明，309 个文件的大小与 mtime 必须完全一致）。

全部从 `test-models/03-animated/David/David.mdx` 实测导出，没有一个数字是编的。
重新生成：

```bash
python export_data.py
python export_geometry.py
python export_textures.py
```

## 3D 视口：真几何 + 真骨骼动画

不是示意几何。顶点、三角、法线、UV、每顶点 4 骨骼权重、骨骼层级、动画关键帧全部来自文件。

| 操作 | 效果 |
|---|---|
| **左键拖** | 旋转（跟手方向：向右拖，模型向右转） |
| **滚轮** | 缩放 0.7–9 倍，右下角实时显示 |
| **右键拖** | 平移 |
| **双击** | 复位视角 |
| **实体 / 线框** | 线框看出拓扑与分块 |

## 贴图

DDS 解码写在内核里（`mdxcore/dds.py`），零依赖：

| 格式 | 说明 | 本地实测 |
|---|---|---|
| DXT1 | 8 字节/4×4 块，c0≤c1 时第三色透明 | 7 张 |
| DXT5 | 16 字节/块，显式 alpha 表 | 42 张 |
| ATI2 / BC5 | 两个 alpha 块存 R、G，B 由单位法线重建 | 8 张（法线贴图） |
| 未压缩 | 按 RGBA mask 解析 | 备用 |

PNG 编码也是手写的（`zlib` + chunk + CRC），不引 Pillow。

匹配规则：按 TEXS 路径的 basename 去 `test-models` 里找同名 dds。
David 的 88 条贴图里 **命中 14 条**（`AF\Units\Human\David\David_*.dds`），
其余 73 条指向 `Units/Human/HeroHalahk/...tif`——那是作者原始工程路径，游戏数据和本地都没有。

**FilterMode 已在用**：David 的 27 个材质里 Transparent 22、None 4、Additive 1（全部单层）。
映射按 Retera 的 `bindLayer()`：Transparent → `alphaTest 0.75`，Additive → `SRC_ALPHA, ONE`，
Blend/Modulate/Modulate2x 用 `CustomBlending` 的对应因子。

## 法线贴图

`MeshLambertMaterial` 在 three 里**不支持 normalMap**，所以材质换成 `MeshPhongMaterial`
（`specular: 0x000000, shininess: 2`，视觉上等价于 Lambert）。

法线贴图来自 HD 合并槽的 `Normal`：ATI2/BC5 解码后 R、G 就是切线空间 xy，
B 由单位法线重建。挂载时必须 `encoding = LinearEncoding`——法线数据不能做 sRGB 转换。

David 只有 `David_Armor_Normal` 一张命中（诊断里 `normalMaps: 1`）。
Kultiran 的 Normal 指向内置 `.tif`，本地没有。

## 粒子（PRE2）

**格式是这一轮新查清的，和别的节点块都不一样**：

```
outerSize(u32) + nodeSize(u32, 含自己 4 字节) + Node + 自己的轨道 + 固定字段 171 字节
```

`outerSize == 4 + nodeSize + 171` 这条等式在 13 个模型上全部成立
（David 687 = 4+512+171、DustEmitter 271 = 4+96+171）。
少数模型（InfernalMagicFire）尾部还有 24 字节额外数据，所以校验放宽成"装得下即可"。

固定字段顺序（无标签，连续）：`speed, variation, latitude, gravity, lifespan,
emissionRate, length, width / filterMode, rows, columns, headOrTail / tailLength, time /
segmentColor[3][3] / segmentAlpha[3] / segmentScaling[3] / 12 个 interval / textureId, squirt,
priorityPlane, replaceableId`。

**注意**：David 的 PRE2 参数全是 0（`rate=0, speed=0`），它没有真实粒子。
所以粒子验证用的是 Retera 自带的 `LargeFireEmitter.mdx`（56/s、1.7s、橙→红渐变）。

渲染：每个发射器一个 `THREE.Points`，CPU 模拟（速度 + 纬度锥 + 重力 + 生命），
颜色按生命在三段色之间插值，alpha 乘到颜色上。贴图缺失时用 canvas 画的柔光圆点兜底。

**没做**：RIBB 飘带、CORN 爆米花、贴图翻页（rows×columns 的 UV 切分）。

## 路径分隔符：`\` 与 `/` 等价

WC3 的贴图路径两种分隔符都认，作者写法并不统一。实测 11 个测试模型的 **357 条贴图**：

| 写法 | 条数 | 例子 |
|---|---|---|
| **正斜杠** | **271** | `Units/Human/HeroHalahk/Human_..._Main_Diffuse.tif` |
| 反斜杠 | 80 | `AF\Units\Human\David\David_Helm_Diffuse.dds` |
| 混用 | 6 | `Units\Human/Jaina/X.tif` |

**正斜杠反而是主流**——尤其是暴雪自己的资产（HeroHalahk 的 18 条全是 `/`）。

代码里要保证三件事，缺一件就会出错：

| 要求 | 为什么 | 位置 |
|---|---|---|
| 查找时归一化 | CASC 内部只用 `\`，不转换就查不到 | `Casc.resolve()` 里 `replace("/", "\\")` |
| 分类与分隔符无关 | 否则"一键改外部路径"会把游戏内置贴图当外部改掉 | `paths.classify()` 先 `norm()` |
| **改写保留原风格** | 只想改前缀，不该把整条路径的每个分隔符都换掉 | `paths.rewrite_one()` 按 `style_sep()` 输出 |

实测四种写法（原样 / 全反斜杠 / 全正斜杠 / 混用）在 CASC 里解析到**同一个文件、读出一模一样的字节**，
且分类结果都是"内置"。所以引擎对分隔符是透明的，我们只需保证自己前后一致。

### 贴图去哪找（三级）与命中率

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | 本地同名文件 | 按 TEXS 路径的 basename 在 `test-models` 里找（整合包自带贴图） |
| 2 | **游戏数据 CASC** | 用 TEXS 原路径查，`.tif` 会自动回退 `.dds` / `.blp` |
| 3 | 无 | 材质退化成该贴图的平均色 |

CASC 路径默认 `D:/Games/War3 2.0`，可用环境变量 `MDX_CASC_PATH` 覆盖；找不到游戏目录时静默只用本地文件。

**两个曾经让命中率只有 20% 的坑**（都已修）：

| 坑 | 后果 |
|---|---|
| `war3.w3mod` 里还套着 `_hd.w3mod` | 人类/兽人/精灵的资产**全在 HD 层**，经典层只有 creeps 等少量。只查第一层会把绝大多数内置贴图判成"缺失" |
| TVFS 前缀树会折叠分隔符 | 树里存的是 `replaceabletextures\commandbuttonsbtnxxx.blp`（少一个 `\`），标准写法 `ReplaceableTextures\CommandButtons\BTNxxx.blp` 永远查不到。用「忽略分隔符」的归一化索引兜底，歧义项丢弃 |

修完后：

| 模型 | 修前 | 修后 | 剩下未命中的原因 |
|---|---|---|---|
| David | 18 / 88 | **87 / 88** | 1 条 `replaceableId=1`（队伍色，运行时生成）。其中 14 条 `AF\Units\Human\David\*.dds` 来自本地文件 |
| Kultiran | 6 / 21 | **19 / 21** | 1 条 `replaceableId`；1 条作者自带的 `kultiran_Barracks_ORM.tif`，本地整合包里没有这个文件 |
| fire | 1 / 1 | 1 / 1 | — |

所以**绝大多数"未找到"是实现问题，不是模型问题**。

### 三种贴图容器

| 格式 | 来源 | 说明 |
|---|---|---|
| DDS | Reforged（`_hd.w3mod` 全是这个） | DXT1/3/5 + ATI2，内核 `mdxcore/dds.py` |
| **BLP1** | 经典层（650 个） | 调色板压缩，内核 `mdxcore/blp.py`。613 个能解，37 个 JPEG 压缩明确报 `BLP1_JPEG` |
| PNG | Retera 自带粒子素材 | 直接用 |

BLP1 有个必须踩过才知道的布局：**调色板固定跟在 156 字节头后面**（`mipOffsets[0] = 1180 = 156 + 1024`），
不在 mip 内部；`mipSizes[0] = 宽×高×2`（索引 + alpha，不含调色板）。
把调色板算进 mip 会让整张图变成彩色噪点——而尺寸、alpha 统计全都正常，光看数字发现不了。
调色板是 **BGRA**，读成 RGBA 会红蓝互换。

验证：`python tools/test_textures.py`（16 项，含 CASC 容器、路径、`.tif→.dds` 回退、
613 个 BLP1 全量解码、四张图标的颜色语义断言——火焰之心必须偏红、霜之镰刀必须偏蓝）。

### 贴图面板显示图像

列表每行带 22×22 缩略图，选中后右栏显示 170px 大图 + 来源（`local` / `casc` / 原始尺寸），
缺失的用红虚线斜纹占位——一眼能看出哪张缺了。

**注意**：`<img>` 在任何打开方式下都能显示，只有"上传到 WebGL"会被 `file://` 拦住。
所以即使 3D 视口没贴图，面板里照样看得到图。

### 贴图加载策略

走普通的 `tex/*.png` 文件，按需加载（同一张只请求一次，多个材质共享同一个 Texture）。

失败处理分三层，保证"看不到贴图也不会像坏了"：

| 情况 | 行为 |
|---|---|
| 正常（http） | 显示贴图 |
| `file://` | 不发请求，直接用贴图的**平均色**，视口顶部提示原因 |
| 图片缺失 / 跨域无 CORS | 同上，保持平均色，不抛错 |

平均色由导出脚本采样写入 `textures.js`，所以即使没有贴图，模型也是有色调的，
不会变成刺眼的白色。

## 时间轴

```
[▶/❚❚] [↻] Stand 1 9200–11867 ms · 2,667 ms · 循环  ▬▬▬●▬▬  10,435 ms  [自动播]
```

| 元素 | 行为 |
|---|---|
| **▶ / ❚❚** | 播放 / 暂停（快捷键 **空格**） |
| **↻ 循环模式** | 点一下循环切换四种：**跟随文件**（按 SEQS 里的 non_looping 标记）/ 强制循环 / 单次 / 往返。图标随之变：`↻` 循环、`→` 单次、`⇄` 往返 |
| **自动播** | 开（默认）时，点序列色带会立刻从该段第一帧开始播；关掉则只定位不播 |
| **拖动条** | 按住左右拖，实时改变姿态；淡色分段是 10 段序列 |
| **下方色带** | 点击某段 = 切到该序列 + 跳到首帧 +（自动播开启时）开始播放 |
| **单次模式播完** | 停在最后一帧，按钮自动变回 ▶ |

拖动条和 3D 是联动的：拖到哪一毫秒，骨骼就是那一毫秒的姿态。

## 能点的其余部分

| 位置 | 操作 | 会看到什么 |
|---|---|---|
| 左栏 | 切贴图 / 材质 / 序列 / 附着点 | 中间表格换内容 |
| 贴图 | 搜索、全部/外部/内置/可替换 | 实时过滤 |
| 贴图 | 一键改外部路径 | 前缀/替换、实时预览命中 14 条、应用后全部标绿 |
| 任一表格 | 点行 | 右栏出表单，改值后行变绿、顶栏计数 +1 |
| 材质 | 改 FilterMode / Alpha | 右栏出现「原值 → 新值」 |
| 序列 | 改起止 ms / 循环 | 底部时间轴跟着变 |
| 附着点 | 选行 → 选武器 → 挂载 | 写入 ATCH 路径 |
| 顶栏 | 导出 | 改动清单 + 文件长度不变 + 备份名 |
| 中间分隔条 | 上下拖动 | 调整视口与表格比例 |

## 假的（有意为之）

| 项 | 现状 | 真实实现 |
|---|---|---|
| 法线贴图 | 导出了但没挂 | 需要 `normalMap` + 切线空间 |
| Emissive / ORM | 导出了但没挂 | 需要三张贴图叠加 |
| 飘带 RIBB | 没有 | 历史位置连成带子 |
| 爆米花 CORN | 没有 | Reforged 新增，Retera 支持也有限 |
| 粒子翻页纹理 | 只取第一帧 | 逐帧动画要改用自定义 shader + UV 偏移 |
| 写入文件 | 点「写入文件」只清空改动 | `ChunkEditor` + `check_delta` |
| 打开 / 保存 / 撤销 | 无动作 | session API |
| 新增附着点 / 选文件 | 只弹提示 | 内核已有，接上即可 |

## 关键实现细节（都是踩过的坑）

| 点 | 结论 |
|---|---|
| 骨骼局部矩阵 | `T(pivot+trans) · R · S · T(−pivot)`，依据 Retera `MathUtils.fromRotationTranslationScaleOrigin` |
| 绑定姿态 | 上述公式在 trans=0/rot=I/scale=1 时恒等于单位阵 → `boneInverses` 全是 identity |
| Skeleton 创建时机 | 必须在 bind pose 下创建，否则会把「动画第一帧」当成绑定姿态，蒙皮整体错位 |
| KGRT 步长 | 旋转是 `(time, x, y, z, w)` 五个值，位置/缩放是四个。按 4 读会让矩阵炸到 1e93 |
| 绑定模式 | `bindMode = "detached"`，避免 group 的缩放污染 `bindMatrixInverse` |
| **父级变换必须 identity** | 骨骼和 mesh 同在一层时，`bone.matrixWorld` 与 `mesh.matrixWorld` 都会带上父级变换，蒙皮会算**两次**。之前在外层挂 `S(0.01)` 把 Z 转 Y，缩放平方成 1e-4，模型只有 1 厘米高——看起来就是"没有模型"。Z-up 交给 `camera.up = (0,0,1)` 处理 |
| 坐标轴 | WC3 是 Z-up、正面朝 +X、右手 −Y；**用 `camera.up=(0,0,1)`，不要给 group 加变换** |
| 贴图 UV | `texture.flipY = false`，WC3 的 UV 原点在左上 |
| 贴图传输 | 必须 base64 内嵌，`file://` 下本地图片无法进 WebGL |

## 校验

```bash
node verify_animation.js   # 24 项：数据 + 骨骼矩阵 + 蒙皮 + 采样 + 取景
node smoke.js              # 21 项：界面渲染 + 视图切换 + 交互流程
```

`verify_animation.js` 不需要 WebGL，直接跑 `index.html` 里的真实现（把 three 的数学类型和 DOM 桩喂进去）：

| 类 | 断言 |
|---|---|
| 数据 | 28 geoset / 17,320 顶点 / 25,023 三角 / 148 轨道；索引不越界；**权重和 = 1**；蒙皮索引全合法 |
| 骨骼 | 120 根父节点全合法无自环；bind pose 下全为单位阵；`boneInverses` 全为单位阵 |
| 动画 | 8 个时刻矩阵无 NaN；位移 < 500 单位；蒙皮后顶点仍在 0–120 高度内 |
| 采样 | 148 条轨道在关键帧时刻必须**精确命中原值** |
| 取景 | 投影到 NDC 后模型必须落在 [-1,1] 内、高度占 25%–100%、中心偏移 < 0.35 |

第三条曾经抓到真 bug：KGRT 按 4 个值读，四元数变成垃圾值，矩阵累积放大到 **1.3e93**，
蒙皮高度跑到 ±10 亿。修完 stride 后回到正常区间。

## 出问题时怎么查

页面自带三层保护：

1. 视口中央会显示 `3D 初始化失败` + 具体错误（`window.onerror` 与 `initViewport` 的 try/catch）
2. 控制台执行 `MDX_DEBUG.status` 得到：three 版本 / 数据是否加载 / 网格数 / canvas 尺寸 / 相机位置 / 当前时间 / 错误
3. `node _diag.js`（需要 Chrome 调试端口，用法见文件头）直接截图并 dump console

`preview.png` 是当前正确渲染的参照。

## 与方案2 的分工

- 方案1（本原型）：换贴图路径、改材质、换动画、挂武器、改序列 —— 不带 Blender
- 方案2（已有）：改顶点、做新模型、重绑蒙皮 —— Blender 插件
- 互通：左栏「用 Blender 打开」检测到安装才启用，未安装置灰

## 第三方

`vendor/three.min.js` —— three.js r147，MIT，本地化以支持 `file://` 直接打开。
