# Retera's Model Studio 现代化 — 技术栈选型与最终实现模式

> 参考文档 ｜ 2026-10-17
> 前置结论：不重写编辑语义，用 Blender 承接几何/动画编辑；自建 MDX 格式内核；原始二进制为唯一真相源

---

## 0. 一页结论

| 层 | 选型 | 一句话理由 | 备选 |
|---|---|---|---|
| **格式内核** | **Python 3.11+**（`struct` + dataclass + 注册表） | Blender 就是 Python，重型编辑能力寄生在它上面 | Rust + PyO3（长期）、Java 保留（只求快） |
| **Blender 前端** | **Blender 4.x Python 插件**（进程内直连内核） | 零 IPC、零序列化、零版本不匹配 | 进程外 CLI 驱动（想闭源时） |
| **轻量前端** | **Tauri 2 + React + Tailwind** | 体积 ~10MB，UI 最现代，跨平台三件套 | PySide6（与内核同语言） |
| **CLI** | **Typer** | 批量管线、CI、给第三方脚本调 | argparse（零依赖） |
| **测试** | **pytest + golden 差分台** | 没有它，任何移植都是赌博 | — |
| **分发** | pip wheel / Blender addon zip / Tauri bundle | 三条渠道各自独立 | — |
| **许可** | 内核 **MIT**；Blender 插件分发须 **GPL-2.0+** | Blender API 传染性，见 §5 | 进程外驱动可规避 |

---

## 1. 为什么内核选 Python

### 三个硬理由

1. **宿主就是 Python。** 我们 70% 的编辑能力（几何、UV、骨骼、动画曲线）来自 Blender 插件，而 Blender 插件只能写 Python。内核选 Python = 进程内直连，省掉 IPC、序列化、版本对齐三层摩擦。这是压倒性理由。
2. **复杂度不在语言，在结构体表。** MDX 是 `[tag][size][data]` 的 chunk 流，本质是"83 张结构定义表"（Retera 里就是 83 个 chunk 类）。`struct` / `construct` 表达这类东西完全够用，用 Rust 写不会有任何表达力优势，只会慢 3 倍。
3. **参照代码就在手边。** Java 源码可逐类对照移植，而且有双端差分台做验收——移植变成了可验证的机械劳动，而不是靠猜。

### 三个已知弱点与对策

| 弱点 | 真实影响 | 对策 |
|---|---|---|
| 解析性能 | 单个模型 100KB–5MB，纯解析远不到瓶颈；**BLP/DDS 图像解码** 和 **整包 CASC 扫描** 会疼 | 图像用 `numpy` 向量化；CASC 遍历用多进程；仍有热点再抽 Rust 扩展（PyO3） |
| 类型安全 | 二进制偏移容易写错 | `mypy --strict` + dataclass + 每个 chunk 强制 `size` 断言 |
| 环境依赖 | 用户机器要有 Python | 用 `pyinstaller` 打单文件；Blender 插件自带解释器，无此问题 |

> **不预优化**：先写对，实测出瓶颈再动手。整包资产处理慢是未来的问题，不是第一版的问题。

---

## 2. 被否的方案（以及什么时候该改选）

| 候选 | 优势 | 否决原因 / 复活条件 |
|---|---|---|
| **保留 Java** | 零移植，Retera 的 83 个解析器直接用 | Blender 侧必须跨进程（Swing 嵌不进 Blender），长期维护两套语言。**复活条件**：要求在 1 个月内出可用原型，可以先用它做 P0/P1 验证，再机械移植 |
| **Rust 内核** | 性能天花板、一份内核服务所有前端（PyO3/FFI/WASM） | 开发速度约为 Python 的 1/3，验证期不该赌。**复活条件**：需要处理整包 CASC（数万模型）或做成服务端批处理 |
| **TypeScript / WASM 内核** | 前端 B 可共享、浏览器直跑 | Blender 侧调用极别扭。**复活条件**：最终产品定位是"浏览器里看模型/在线分享"，Blender 变成次要 |
| **C# / .NET** | Avalonia UI 不错，War3Net 有 MPQ 基础 | 与 Blender 完全无集成，等于自建两条技术线 |
| **Electron** | 生态最全 | 体积/内存代价大，Tauri 已能覆盖需求 |

---

## 3. 最终实现模式（核心）

### 3.1 四个产物，一个内核

| 产物 | 面向 | 入口 | 与内核的关系 |
|---|---|---|---|
| **Blender 插件** `mdx_io` | 会 Blender 的建模者 | Blender 的 Import / Export 菜单 | 同进程 `import mdxcore` |
| **轻量桌面工具** | 只想改贴图/材质/速度的人 | 双击打开 | 通过 sidecar 调 Python 内核 |
| **CLI** `mdx` | 批量管线、CI、第三方 | 命令行 | 直接 import |
| **mdx-core**（pip 包） | 其它开发者 | `pip install mdx-core` | 本体 |

### 3.2 数据流：二进制是真相，场景只是视图

```
open(path)
  ├─ 读入原始字节，切成 [header][chunk…]，记录每段偏移与长度
  ├─ 只解析用得到的 chunk（lazy），其余保持原始字节
  └─ 建视图（Blender 场景 / GUI 表单 / dict）

save()
  ├─ 计算改动集合：哪些 chunk 被修改
  ├─ 只重新序列化这些 chunk
  ├─ 其余 chunk 原样贴回（包括不认识的）
  └─ 拓扑变更 → 粒度退到 geoset 级整体重写
```

三条不可违背的规则：

1. **不认识的 chunk 必须原样透传**，否则 Reforged 新格式一来你就废。
2. **不做全量重新序列化**，除非用户真的改了整体结构。
3. **浮点数原样保留**：只有被改动的数值才重算，未改动的保留原始字节（MDL 文本层同理，保留原始字符串）。

### 3.3 模块结构

```
mdx-core/
  mdxcore/
    container.py      # 容器：chunk 边界、透传、组装
    registry.py       # tag → 解析器注册表
    chunks/           # 按 Retera 的 83 个类逐个移植
      geos.py  mtls.py  seqs.py  bone.py  help.py  pre2.py  ribn.py …
    semantics.py      # WC3 概念（FilterMode / ReplaceableId / 节点类型）
    patch.py          # delta 计算与应用
    mdl/              # MDL 文本读写（保留原始 token）
  tests/
    golden/           # 33 个内置模型 + 社区样本基线
    test_roundtrip.py # 字节级比对
blender-addon/
  mdx_io/
    __init__.py  importer.py  exporter.py  props.py
apps/
  desktop/            # Tauri + React
  cli/                # Typer
```

### 3.4 差分台（不可省略的基础设施）

| 用例 | 断言 |
|---|---|
| open → save | **字节完全相同**（33/33） |
| open → 改 1 个顶点 → save | 只有 GEOS 相关字节变化；再 open 后坐标正确、其它字段逐一相等 |
| open → 改贴图路径 → save | 只有 MTLS/TEXS 变化 |
| MDL → MDX → MDL | 语义等价（浮点误差 < 1e-6，字段顺序与注释保留） |
| 全部用例在 CI 上跑 | GitHub Actions，PR 合并门禁 |

---

## 4. 分阶段落地（含 kill criteria）

| 阶段 | 周期 | 交付 | 通过标准 | 不通过怎么办 |
|---|---|---|---|---|
| **P0 基线** | 3–5 天 | 用现有 Retera 跑 open→save，产出容差数据 + 差分脚本 | 知道"现引擎自身往返的误差有多大" | — |
| **P1 容器层** | 2 周 | `container.py` + `registry.py` + 透传往返 | **33/33 字节相同** | 说明没真正理解格式，回去重读，不要往下走 |
| **P2 语义层** | 3–4 周 | 高频 chunk：GEOS/MTLS/TEXS/SEQS/BONE/HELP | 改顶点、改材质不污染其它 chunk | 砍范围，只做真用到的 |
| **P3 Blender 链路** | 4–6 周 | import + export operator | 往返后 Blender 能重开、模型能进游戏 | 降级为"只导入，导出走 CLI" |
| **P4 轻量前端** | 3–4 周 | 改贴图/材质/动画速度 + 批量转换 | 非技术用户能独立完成 | — |

**总工期：约 3–4 个月到"可用"，其中 P1 是最硬的生死线。**

---

## 5. 交付与许可

| 项 | 结论 |
|---|---|
| 内核 | MIT（与原项目一致），保留 Retera 的版权声明与 MIT 原文 |
| Blender 插件 | 公开分发时须 **GPL-2.0+ 兼容**（`bpy` API 传染）。想闭源 → 改成"进程外调 CLI 驱动 Blender" |
| 移植自 Retera 的代码 | 属于衍生作品，**必须保留 `Copyright (c) Retera` 与 MIT 许可文本** |
| Blender 本体 | GPL，单独分发，不作为我方代码的一部分 |
| 署名 | 在插件"关于"页与 README 标注"基于 Retera's Model Studio（MIT）"，并链接 Hive 原帖 |

> 一句话：**内核 MIT 干净；插件想公开就得 GPL。这个选择必须在写第一行代码前定。**

---

## 6. 需要你确认的三个开关

| # | 开关 | 选项 | 影响 |
|---|---|---|---|
| 1 | **Blender 插件是否开源** | 开源（进程内直连，开发快）｜闭源（进程外驱动，多一层 IPC） | 决定 §3.3 的模块边界，改起来很贵 |
| 2 | **轻量前端技术路线** | Tauri + React（现代、跨平台、体积小）｜PySide6（与内核同语言，少一层） | 决定前端开发者的技能要求 |
| 3 | **是否先保留 Java 做过渡** | 是（1 个月出原型，再机械移植）｜否（直接 Python，多 1.5–2 月） | 决定第一次能看到东西的时间 |

**我的建议：1 选开源、2 选 Tauri、3 选保留 Java 做 P0/P1 验证。**

理由：先在最便宜的路径上拿到"33/33 能不能还原"这个数字，再决定要不要花两个月移植。顺序反了，就是在没有证据的情况下先付最大的一笔成本。
