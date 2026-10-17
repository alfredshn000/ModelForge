# mdx-toolkit

MDX 格式内核。当前 **P1：参数编辑层**，已在 21 个真实模型上自测通过。

## 设计铁律

1. **未修改的 chunk 原样透传**（复用原始 bytes，不重新编码）
2. **不认识的 tag 不报错**，原样保留
3. **没有「保存整个模型」的入口**——只有「操作 → 替换集合 → rebuild」，从 API 形状上杜绝全量重写
4. **格式布局一律照搬 Retera 源码**，不许靠字节猜（已经吃过两次亏）

## 目录

```
mdxcore/
  container.py     容器：切分 chunk、校验、透传拼接
  diff.py          字节差分 + 偏移→chunk 映射
  verify.py        写入校验：确认只动了该动的字节
  ops.py           语义操作，统一返回 {chunk序号: bytes}
  chunks/
    modl.py        模型头（372B：name[336] + unknownNull + boundsRadius + 两个 extent + blendTime）
    seqs.py        动画序列（132B/条：name[80] + 起止帧 + 速度 + 循环标志 + 稀有度 + 包围盒）
    texs.py        贴图表（268B/条：replaceableId + path[256] + unknownNull + flags，**无计数字段**）
    mtls.py        材质/图层（28B 固定前缀 + inclusiveSize 跳过变长轨道；含 HD 6 槽）
    geoa.py        几何动画（28B 固定前缀：alpha + flags + color + geosetId）
    atch.py        附着点（双层 size：名字在 +8、objectId 在 +88、path[260] + 编号）
    pivt.py        轴心点（12B/点，无计数字段）
    nodes.py       节点名字枚举（按 inclusiveSize 走，走不通就不采信）
    bpos.py        绑定姿势（Reforged，3×4 矩阵，只读）
    geos.py        几何体（标签化子块，parse → build 字节级可逆；含顶点增删）
    bone.py        骨骼（Node 前缀 + 尾部 geosetId/geosetAnimationId + 动画轨道）
    animflag.py    动画轨道（tag/count/interp/globalSeq，切线逐条内联）
    animscan.py    在变长记录里定位轨道（末尾锚定搜索，不写死版本偏移）
  tangents.py      切线换算（WC3 值/毫秒 ↔ Blender 值/帧，纯函数）
  skin.py          蒙皮权重（SKIN 子块 / legacy 矩阵组）
  paths.py         贴图路径分类（内置/外部/可替换）+ 批量改写规则
  writers.py       定点写入器（攒批量 → 一次交付 + 绝对偏移 span）
  transfer.py      动画移植（骨骼配对 L1–L4、轨道搬运、序列替换、解绑）
  nodesedit.py     节点增删（任意位置插删附着点：全表重编号 + PIVT 同步，可逐字节还原）
  dds.py           DDS 解码（DXT1/3/5 + ATI2）+ 手写 PNG 编码 + 盒式降采样
  blp.py           BLP1 解码（经典版调色板贴图）
  casc/            只读 CASC（直读 Reforged 游戏数据）
    blte.py          BLTE 容器解码（N / Z）
    indexfile.py     .idx 本地索引（offset 大端、size 小端）
    tvfs.py          TVFS 目录清单（全部字段大端）
    storage.py       统一入口 + 安装目录自动探测
tools/
  mdx.py           统一 CLI：info / tex / seq / model / mat / geoa / pivt / bpos /
                   geoset / anim / track / batch
  make_geom_test.py 在模型上方造一个金字塔标记（引擎验证用）
  casc_stats.py     CASC 读取统计（文件数 / 耗时 / 贴图命中率）
  audit_casc.py     全量体检（解析 → 重建 → 逐字节比对）
  test_paths.py     贴图路径分类/改写/幂等验证（真实模型）
  test_transfer.py  动画移植验证（自绑逐字节还原、解绑再绑回逐字节还原）
  test_nodesedit.py 附着点增删验证（加一条再删掉必须逐字节还原）
  test_textures.py  贴图链路验证（CASC 容器/路径、613 个 BLP1、通道语义断言）
  test_flow_casc.py 完整流程：从 CASC 取出真实模型 → 修改 → 写盘（游戏目录只读）
  selftest.py      21 个模型的全量自测
  baseline.py      用现成 Retera 做 open→save，产出保真度报告
work/              Retera 转换产物（可删）
out/               本工具的输出
reports/           基线报告
review/            游戏内 A/B 对比包
```

## 命令

```bash
cd mdx-toolkit
PY=python    # 本机用 C:/Users/alfre/.workbuddy/binaries/python/versions/3.13.12/python.exe

# 模型概览
$PY tools/mdx.py info "../test-models/03-animated/David/David.mdx"

# 贴图
$PY tools/mdx.py tex  <file>                            # 列出
$PY tools/mdx.py tex  <file> --index 0 --path a.blp     # 改路径

# 动画序列
$PY tools/mdx.py seq  <file>                            # 列出
$PY tools/mdx.py seq  <file> --index 0 --start 0 --end 1000
$PY tools/mdx.py seq  <file> --index 0 --noloop

# 模型头
$PY tools/mdx.py model <file>                           # 查看
$PY tools/mdx.py model <file> --name NewName            # 改名

# 材质 / 图层
$PY tools/mdx.py mat  <file>                                          # 列出材质与图层（含 HD 槽）
$PY tools/mdx.py mat  <file> --mat 0 --layer 0 --alpha 0.6            # 改透明度
$PY tools/mdx.py mat  <file> --mat 0 --layer 0 --filter blend         # 改 FilterMode
$PY tools/mdx.py mat  <file> --mat 0 --layer 0 --tex 9                # 改贴图（HD 模型自动走 HD 槽）
$PY tools/mdx.py mat  <file> --mat 0 --layer 0 --tex 9 --hd-type Normal

# 几何动画
$PY tools/mdx.py geoa <file>                                  # 列出
$PY tools/mdx.py geoa <file> --index 0 --alpha 0.35           # 改整块透明度
$PY tools/mdx.py geoa <file> --index 0 --color 1,0,0          # 改整块颜色

# 轴心点 / 绑定姿势
$PY tools/mdx.py pivt <file>                          # 列出（含骨骼名）
$PY tools/mdx.py pivt <file> --index 0 --xyz 0,0,0    # 改轴心点
$PY tools/mdx.py bpos <file>                          # 绑定姿势（只读）

# 几何：移动 / 增删顶点、加三角形
$PY tools/mdx.py geoset <file>                                        # 列出 geoset
$PY tools/mdx.py geoset <file> --geoset 0 --index 0 --xyz 1,2,3        # 移动顶点
$PY tools/mdx.py geoset <file> --geoset 0 --add-vertex 1,2,3 --from 0  # 新增（克隆 0 号顶点属性）
$PY tools/mdx.py geoset <file> --geoset 0 --delete-vertex 10           # 删除（连带三角形）
$PY tools/mdx.py geoset <file> --geoset 0 --add-tri 0,1,2              # 新增三角形

# 骨骼动画（字段级写入）
$PY tools/mdx.py anim <file>                                    # 列出有动画的骨骼
$PY tools/mdx.py anim <file> --bone 112                         # 该骨骼有哪些轨道
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR              # 列出关键帧
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --index 3 --time 1200
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --index 3 --value 1,2,3
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --index 3 --outtan 0,0,0
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --shift 400  # 整体平移（可见性测试）
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --add-key --time 5000 --value 0,0,0
$PY tools/mdx.py anim <file> --bone 112 --tag KGTR --delete-key 3

# 材质图层 / 几何动画的轨道（非节点轨道）
$PY tools/mdx.py track <file>                                        # 列出全部
$PY tools/mdx.py track <file> --kind layer --mat 0 --layer 0          # 该图层的轨道
$PY tools/mdx.py track <file> --kind geoa --record 0                  # 该记录的轨道
$PY tools/mdx.py track <file> --kind geoa --record 0 --tag KGAO --index 2 --value 0.25

# 直接读游戏数据（Reforged CASC）
$PY tools/mdx.py casc                                  # 版本 / 文件数
$PY tools/mdx.py casc --find "footman.*\.mdx$"         # 搜索
$PY tools/mdx.py casc --get "units\humanfootman\footman.mdx" --out ft.mdx

# 批量
$PY tools/mdx.py batch <目录> --tex-from "旧路径" --tex-to "新路径"

# 默认输出到 out/<原名>.edited.mdx，可用 --out 指定

# 自测
$PY tools/selftest.py
```

## 写入校验

分两种，按操作是否会改变块长度选择：

**`check_delta`（参数编辑）**

- 文件长度不得变化
- 所有差异区间必须落在该操作的字段范围内（范围先按区间并集合并——
  相邻字段的改动会跨过分界，逐段判断会把合法改动误判成越界）
- 写出后必须能重新解析，chunk 序列不得变化
- 没有任何差异 → 视为失败（说明没写进去）

**`check_chunk_rewrite`（结构变更，如增删顶点）**

- 写出后必须能重新解析
- chunk 数与顺序不得变化
- 除目标 chunk 外，每个 chunk 必须**逐字节相同**
- 目标 chunk 必须真的变了

校验不通过时**不会写出文件**。

## 已验证的事实

| 项 | 结果 |
|---|---|
| 容器透传 | 21/21 模型「解析→重建」逐字节相同 |
| 语义层可逆 | TEXS / SEQS 逐字节可重建；MODL 差异仅限 name 字段（会清尾部残留，属预期） |
| 写入精度 | 3,107,066 B 的模型改一条贴图路径，只变 **59 字节（0.0019%）** |
| 引擎语义 | **六组**样本在编辑器和游戏内全部验证通过：贴图 / HD 槽 / 动画时间 / 新增几何 / 删关键帧 / 隐藏几何 |
| 游戏数据直读 | CASC 内 3,458 个 mdx 可列可读；30/30 随机模型解析成功，贴图命中 97.6% |
| 版本覆盖 | 1000 / 1100 / 1200 之外，补上 **1800**（GEOS 必有 LOD，TANG/SKIN 可选，SKIN 换 16 字节宽格式） |
| 全量体检 | **3,458 / 3,458 个模型 100% 通过**（解析→重建逐字节一致），16 秒 |
| GEOS 几何 | 10/10 模型「解析 → 重建」逐字节相同（说明没有漏字段） |
| 骨骼蒙皮 | 10/10 模型权重逐顶点核对一致（Blender 侧） |
| 骨骼动画 | 235,769 条轨道解析成功（Lvbu 最多 132,555 条）；1,248 个轨道块重建逐字节相同 |
| 切线换算 | 5000 组随机参数往返误差 < 1.6e-10 |
| 动画写回 | 改 1 条关键帧时间只变 1–4 字节；切线可原样保留 |
| 顶点增删 | 11 个模型 × 增/删/加面 = 33 项结构校验全过，只有 GEOS 变化 |
| 关键帧增删 | 11/11 模型「加一个再删掉」逐字节还原原文件 |
| 非节点轨道 | 80 条（图层 32 + 几何动画 48）；改一条只变 1–4 字节 |
| 法线 | 定点写回只动 12 字节 |
| 材质 / 贴图 / 序列 / 几何动画 / 轴心点 / 附着点 | 11/11 模型 × 9 组 = 99 项全过，长度不变、只改目标字段 |
| 动画移植 | 自己绑自己 3/3 逐字节还原；解绑再绑回 31,718 键逐字节还原；同族换绑匹配 99% |
| 贴图路径分类 | 367 条真实贴图：内置 273 / 外部 84 / 可替换 10，判定依据逐条可查 |
| ATCH 布局 | 71 条记录全部自证（外层 size + 内层 nodeSize + path[256] + unknownNull + 编号） |
| 节点表完整性 | 11/11 模型「节点数 == 轴心点数、编号 0..n-1 无缺无重」；半数节点块是双重 size |
| 附着点增删（任意位置） | **52 项全过**；末尾加再删、中间插再删**都逐字节还原**；父子关系名字映射不变 |
| ATCH 挂载（引擎实测） | A/B/C/D 四组对照全部符合预期：**path 生效、跟着父骨骼动、末尾追加的编号被引擎接受** |
| BLP1 解码 | 650 个全扫：613 个调色板全部解出、37 个 JPEG 明确报错、0 未知错误；图标颜色语义正确 |
| CASC 容器 | `_hd.w3mod`（26,788 条）与经典层（22,200 条）都可枚举；标准路径与 `.tif→.dds` 回退全部命中 |
| 分隔符 | `\\` 与 `/` 与混用四种写法解析到同一文件、读出同样字节；改写保留原风格 |
| CASC 取出流程 | 23 项全过：副本与原字节一致、改完只动目标字段、**游戏 Data 目录 309 个文件的大小与 mtime 全未变** |
| MTLS HD 槽（1800） | 修正版本判断后，HeroHalahk 六槽正确指向 2/3/4/5/6/7（Diffuse/Normal/ORM/Emissive/TeamColor/Reflections） |
| 自测规模 | 441 项断言，覆盖 21 个模型 |
