# mdx_io — Blender 前端（v1 原型）

把 Blender 当作几何/动画编辑器，格式读写交给工作区里的 `mdx-toolkit/mdxcore`。

> **铁律：二进制是真相，Blender 场景只是视图。**
> 导出时不重新序列化整个模型，而是读回原文件、逐项比对、再写出原始字节。
> 只要比对通过，输出就是字节完全相同的文件。

---

## 当前能力

| 能力 | 状态 |
|---|---|
| GEOS → 网格（顶点 / 三角面 / 第一套 UV） | ✅ |
| PIVT → 空物体（带真实节点名） | ✅ |
| 每个 geoset 一个材质槽，贴图路径写入自定义属性 | ✅ |
| 导出 + 保真校验（顶点/面/UV 逐项比对） | ✅ **11/11 模型实测通过，偏差 0** |
| 几何写回（移动顶点，定点覆写 12 字节） | ✅ **实测只变 1 字节** |
| BONE → Armature（层级 / 轴心点） | ✅ **10/10 模型逐顶点核对** |
| 骨骼动画 → Action 关键帧（含真实切线） | ✅ **86 万关键帧逐点核对** |
| 动画写回（时间 / 数值 / 切线） | ✅ **只写改动的字段，切线可保原值** |
| 关键帧增删 | ✅ **只重建目标骨骼的记录，其它骨骼逐字节不变；游戏内已验证** |
| 法线导入（自定义分裂法线） | ✅ 改一个法线只动 12 字节 |
| 动画序列 → 时间轴标记 | ✅ 11/11 模型，标记数 == 序列数 |
| 贴图加载（dds / tif / png …） | ✅ 真实 DDS 实测有像素；缺失/读不了会报数 |
| 导出报告面板 | ✅ 场景属性 + 面板显示改动摘要 |
| **直读 Reforged 游戏数据** | ✅ 面板内选安装目录、搜索、导入；贴图一并取出 |
| 动画序列 → 时间轴标记 | ✅ 11/11 模型，标记数 == 序列数 |
| 贴图加载（dds / tif / png …） | ✅ 真实 DDS 实测有像素；缺失/读不了会报数 |
| 导出报告面板 | ✅ 场景属性 + 面板显示改动摘要 |
| **直读 Reforged 游戏数据** | ✅ 面板内选安装目录、搜索、导入；贴图一并取出 |
| 顶点增删 / 三角增删 | ✅ **重建 GEOS，其余 chunk 逐字节不变；游戏内已验证** |


---

## 安装（Blender 4.x）

1. 把 `blender-addon/mdx_io` 整个文件夹复制到 Blender 插件目录：
   ```
   Windows: %APPDATA%\Blender Foundation\Blender\4.x\scripts\addons\mdx_io
   macOS:   ~/Library/Application Support/Blender/4.x/scripts/addons/mdx_io
   Linux:   ~/.config/blender/4.x/scripts/addons/mdx_io
   ```
2. Blender → Edit → Preferences → Add-ons → 搜索 "MDX" → 勾选启用
3. 如果提示找不到 mdxcore，设置环境变量：
   ```
   MDX_TOOLKIT=C:\Users\alfre\Desktop\TEST APP\mdx-toolkit
   ```
   （正常情况下插件会自动往上两级找到它）

---

## 使用

- **导入**：File → Import → Warcraft III MDX (.mdx)
- **导出**：File → Export → Warcraft III MDX (.mdx)
- 右侧 3D 视图按 `N` → MDX 面板，可看模型信息与导出报告

---

## 无界面校验（推荐先跑这个）

```bash
blender --background --python "C:/Users/alfre/Desktop/TEST APP/blender-addon/check_roundtrip.py" -- "C:/Users/alfre/Desktop/TEST APP/test-models/01-simple/BigBookshelfClean.mdx"
```

输出会打印：

```json
{
  "byte_identical": true,
  "geometry_unchanged": true,
  "checks": [ { "vertex_mismatch": 0, "uv_ok": true, ... } ]
}
```

**判定标准：`byte_identical` 与 `geometry_unchanged` 必须同时为 true。**

### 编辑写回校验

```bash
blender --background --python "check_edit.py" -- "test-models/01-simple/BigBookshelfClean.mdx" 0 0 0 0 10
```

参数：`模型 geoset序号 顶点序号 dx dy dz`。

会检查：文件长度不变、差异只在目标顶点的 12 字节内、读回坐标等于期望值、其它 geoset 未变。

### 骨骼与蒙皮校验

```bash
blender --background --python "check_skin.py" -- "test-models"
```

核对骨骼数量/名字/父子关系，以及**每个顶点**的顶点组权重是否与文件的蒙皮数据一致。

### 动画校验

```bash
blender --background --python "check_anim.py" -- "test-models"
```

逐条曲线核对关键帧的时间与数值（旋转按四元数顺序转换）。实测 **8/8 通过，866,372 个关键帧全部一致**。

切线现在是**真的写进手柄**了：按 1/3 间距摆放，三次贝塞尔与 WC3 的三次 Hermite 逐点等价。

### 动画写回校验

```bash
blender --background --python "check_anim_edit.py" -- "test-models"
```

三种编辑各跑一遍，逐项断言差异范围：

| 编辑 | 期望 | 实测 |
|---|---|---|
| 关键帧整体平移（连手柄一起） | 只有 4 字节的 time 变，切线不变 | 1–2 字节，切线逐字节相同 |
| 只改关键帧数值 | 只有 value 段变 | 1–4 字节 |
| 只拖右手柄 | 只有 outTan 段变 | 4–6 字节 |

### 两种打开方式

**外部模型（不需要装游戏）**

- `文件 > 导入 > Warcraft III MDX (.mdx)`
- 或 N 面板「导入 MDX」（可以顺手指定贴图目录）

导出同理：`文件 > 导出 > Warcraft III MDX (.mdx)`。

**游戏本体（需要装 Reforged，且只支持 Reforged）**

不用先手动导出模型——N 面板的「游戏数据（CASC）」一栏填游戏安装目录，
点「搜索」按名字找（如 `footman`），找到后点「导入」。

导入时会把模型和它引用的贴图一起取到 `mdx-toolkit/work/casc/<模型名>/`，
之后跟打开本地文件完全一样，导出、改贴图、改动画全都照常。

> 安装目录不用精确填到最里层：填 `D://Games//War3 2.0` 也行，会自动往下钻到含
> `.build.info` 的那一层。选过一次就记在 `mdx-toolkit/work/settings.json`，
> 下次自动带上；也可以用环境变量 `WC3_INSTALL` 指定。

```bash
blender --background --python "check_casc.py" -- "units\humanfootman\footman.mdx"
```

校验内容：能否导入、贴图是否挂上、**不改动时导出是否与游戏里的字节完全相同**。

实测 5/5 通过（altarofkings / grunt / peasant / chaosgrunt / footman），
另有 1 个纯特效模型正确跳过。

### 体验层

```bash
blender --background --python "check_scene.py" -- "test-models"
```

核对三件事：每段动画都有 marker、材质都挂上了贴图、导出报告落到了场景里。

**贴图怎么用**：在右侧 N 面板的「贴图」里选一个目录（模型旁边或你导出的贴图文件夹），
导入时会自动解析。也可以先导入再点「重新加载贴图」。

### 关键帧增删

```bash
blender --background --python "check_keyframes.py" -- "test-models"
```

在 Blender 里真的插一个键、真的删一个键，然后核对：只有 BONE 块变化、
目标骨骼的关键帧数 ±1、**其它骨骼的记录逐字节不变**、导出文件能重新导入。

### 法线

```bash
blender --background --python "check_normals.py" -- "test-models"
```

核对三件事：导入后法线与文件一致（受 2×int16 编码限制，误差 ≤2e-3）、
不改动时导出字节完全相同、转一个法线后只有那 12 字节变。

### 几何 + 动画同时写

```bash
blender --background --python "check_mixed.py" -- <model.mdx>
```

同一次导出里既移动顶点又平移关键帧，实测文件长度不变、**只变 3 字节**。

### 拓扑变更校验

```bash
blender --background --python "check_topology.py" -- "test-models/01-simple/BigBookshelfClean.mdx" 0
```

在 Blender 里真的挤出三角形、真的删顶点，然后验证：

- 只有 GEOS 块变化，其它 chunk 逐字节不变
- 顶点/三角数量符合预期
- 法线 / GNDX / UV / 切线 / 蒙皮数组随顶点数同步
- 其它 geoset 逐字节不变
- 导出的文件能重新导入

### 实测结果（Blender 5.2.2）

| 项目 | 结果 |
|---|---|
| 11 个模型导入→导出 | **11/11 通过，顶点偏差 0.0，UV 0 异常** |
| 移动 BigBookshelf 顶点 0 的 Z | 文件只变 **1 字节**，读回偏差 0 |
| 移动 David geoset5 顶点 100 的 Z | 文件只变 **1 字节**，读回偏差 0 |
| 骨骼 + 蒙皮校验 | **10/10 通过**（无骨骼的模型正确跳过），权重逐顶点核对 0 不符 |
| 动画校验 | **11/11 通过**（含无动画模型），866,372 个关键帧逐一核对 |
| 动画写回 | **6/6 通过**，三种编辑的差异都只落在目标字段 |
| 拓扑变更 | 挤出三角形 +3 顶点/+1 面（+177 B）、删 1 顶点/−1 面（−63 B），均通过 |
| 关键帧增删 | 插入 +52 B、删除 −52 B，其它骨骼逐字节不变 |
| 法线 | 未改动 → 字节相同；改一个 → 只变 12 字节 |

---

## 许可

MIT。基于 Retera's Model Studio（MIT）的格式知识实现，分发时请保留上游署名。
