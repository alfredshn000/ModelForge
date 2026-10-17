"""可编辑字段面板：贴图 / 材质 / 动画序列 / 几何动画 / 轴心点。

这五类字段都是**定长**的，所以改动既不改变文件长度、也不破坏结构。
统一走 `state.py`（场景里的可编辑值）→ `state_export.py`（比对 + 定点写入）
这条链，面板本身不碰字节。

三条设计原则，与前几轮保持一致：

1. **改动先落在场景里，导出时才算数。** 面板每改一处只写 `mdx_state`，
   导出时逐字段与文件比对，只写真正变了的字节。随时可以放弃改动（不上磁盘）。
2. **分类不能是黑箱。** 贴图是"游戏自带"还是"模型自带"，判定依据逐条可见，
   而且可以人工翻转——作者自有目录撞上同名游戏目录这种事真的会发生。
3. **选贴图一律搜索。** 模型动辄上百张贴图，长下拉列表不可用。
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, StringProperty)

from . import state as statemod
from .bridge import get_core

CLASS_ICONS = {
    "builtin": "LOCKED",
    "external": "UNLOCKED",
    "replaceable": "COLOR",
    "empty": "BLANK1",
}
CLASS_TEXT = {"builtin": "内置", "external": "外部", "replaceable": "可替换", "empty": "空"}

FILTER_ITEMS = [
    ("0", "None（不混合）", "直接覆盖，用于不透明物体"),
    ("1", "Transparent（透明键色）", "按 alpha 值做二值裁剪"),
    ("2", "Blend（半透明）", "按 alpha 值混合"),
    ("3", "Additive（加色）", "发光、法术效果"),
    ("4", "AddAlpha（加色 alpha）", ""),
    ("5", "Modulate（正片叠底）", ""),
    ("6", "Modulate2x（正片叠底×2）", ""),
]


# ---------- 小工具 ----------

def _mark_dirty():
    bpy.context.scene["mdx_dirty"] = True


def _state_or_warn(operator) -> dict:
    st = statemod.load()
    if not st:
        operator.report({"ERROR"}, "当前场景没有 MDX 状态，请先导入模型")
    return st


class _Tex:
    """给 mdxcore.paths 用的轻量适配器（它只读 .path / .replaceable_id）。"""
    __slots__ = ("path", "replaceable_id")

    def __init__(self, path, rid):
        self.path = path
        self.replaceable_id = rid


def _texture_enum(self, context):
    st = statemod.load()
    items = []
    for t in st.get("textures") or []:
        p = t.get("path") or "（空）"
        label = f"{t['index']:>3}  {p}"
        items.append((str(t["index"]), label[:96], p))
    return items or [("-1", "（模型没有贴图表）", "")]


def _image_icon(image) -> int:
    """取图像的预览缩略图 ID；拿不到就返回 0（调用方退回内置图标）。"""
    if image is None:
        return 0
    try:
        image.preview_ensure()
        return image.preview.icon_id or 0
    except Exception:                              # noqa: BLE001
        return 0


def _material_by_id(mat_id: int):
    """按 MTLS 索引找到场景里的材质（一个材质可能被多个 geoset 引用）。"""
    for obj in bpy.data.objects:
        if obj.type != "MESH" or "mdx_material_id" not in obj:
            continue
        if int(obj["mdx_material_id"]) != mat_id:
            continue
        for slot in obj.material_slots:
            if slot.material:
                return slot.material
    return None


def _material_image(mat):
    """材质里接到 Base Color 的那张图（面板缩略图用）。"""
    if mat is None or not mat.use_nodes:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
    return None


# ---------- 一键改贴图路径 ----------

class MDX_OT_rewrite_textures(bpy.types.Operator):
    bl_idname = "mdx.rewrite_textures"
    bl_label = "一键改外部贴图路径"
    bl_description = "把模型自带（外部）的贴图路径批量改到新目录；游戏内置贴图一律不动"
    bl_options = {"REGISTER"}

    mode: EnumProperty(name="方式", items=[
        ("PREFIX", "统一前缀", "改成 前缀\\文件名（或 前缀\\原相对路径）"),
        ("REPLACE", "查找替换", "只把路径里的某段字面换掉，其余保持原样"),
    ])
    prefix: StringProperty(name="新前缀", default="war3mapImported\\")
    flatten: BoolProperty(name="压平目录", default=True,
                          description="只保留文件名，丢掉原来的子目录")
    find: StringProperty(name="查找")
    repl: StringProperty(name="替换为")
    include_locked: BoolProperty(
        name="含我标记为内置的条目", default=False,
        description="默认只改自动判定为外部的条目；勾上后连人工标记的一起改")

    def invoke(self, context, _event):
        st = statemod.load()
        if not st:
            self.report({"ERROR"}, "请先导入模型")
            return {"CANCELLED"}
        if not self.prefix:
            from mdxcore import paths as pathmod
            self.prefix = pathmod.suggest_prefix(
                [t.get("path", "") for t in st.get("textures") or []]) + "\\"
        return context.window_manager.invoke_props_dialog(self, width=520)

    # ---- 规则 ----

    def _rule(self):
        from mdxcore import paths as pathmod
        if self.mode == "PREFIX":
            return pathmod.Rule(mode="prefix", prefix=self.prefix, flatten=self.flatten)
        return pathmod.Rule(mode="replace", find=self.find, repl=self.repl)

    def _plan(self):
        """返回 (新路径映射, 说明)。只算，不写。"""
        from mdxcore import paths as pathmod

        st = statemod.load()
        texs = st.get("textures") or []
        if self.mode == "PREFIX" and not self.prefix.strip():
            return {}, "前缀为空"
        if self.mode == "REPLACE" and (not self.find or self.find == self.repl):
            return {}, "替换前后相同"

        allow = set()
        for t in texs:
            cls = t.get("class", "external")
            if cls != "external":
                continue
            if t.get("locked") and not self.include_locked:
                continue
            allow.add(t["index"])

        entries = [_Tex(t.get("path", ""), t.get("replaceable_id", 0)) for t in texs]
        try:
            mapping = pathmod.apply_rule(entries, self._rule(), include=allow)
        except Exception as exc:                    # noqa: BLE001
            return {}, str(exc)
        return mapping, ""

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "mode", expand=True)
        box = col.box()
        if self.mode == "PREFIX":
            box.prop(self, "prefix", text="前缀")
            box.prop(self, "flatten")
        else:
            box.prop(self, "find", text="查找")
            box.prop(self, "repl", text="替换为")
        col.prop(self, "include_locked")

        mapping, err = self._plan()
        info = col.box()
        if err:
            info.label(text=err, icon="ERROR")
        elif not mapping:
            info.label(text="没有条目会被改动", icon="INFO")
        else:
            info.label(text=f"将改动 {len(mapping)} 条贴图路径", icon="CHECKMARK")
            for i in list(mapping)[:6]:
                info.label(text=f"  {i}: {mapping[i][-52:]}")
            if len(mapping) > 6:
                info.label(text=f"  …另有 {len(mapping) - 6} 条")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        mapping, err = self._plan()
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        if not mapping:
            self.report({"WARNING"}, "没有需要改动的贴图")
            return {"CANCELLED"}

        for i, new in mapping.items():
            st["textures"][i]["path"] = new
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"已改 {len(mapping)} 条贴图路径（导出时写入文件）")
        return {"FINISHED"}


class MDX_OT_toggle_texture_lock(bpy.types.Operator):
    bl_idname = "mdx.toggle_texture_lock"
    bl_label = "切换改/不改"
    bl_description = "人工翻转某条贴图的判定：标为内置则批量改路径时跳过"

    index: IntProperty()

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        texs = st.get("textures") or []
        if not 0 <= self.index < len(texs):
            return {"CANCELLED"}
        t = texs[self.index]
        t["locked"] = not t.get("locked", False)
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"贴图 {self.index}：" +
                    ("不再参与批量改写" if t["locked"] else "重新纳入批量改写"))
        return {"FINISHED"}


class MDX_OT_set_texture_path(bpy.types.Operator):
    bl_idname = "mdx.set_texture_path"
    bl_label = "改贴图路径"
    bl_description = "直接编辑一条贴图路径"

    index: IntProperty()
    path: StringProperty(name="路径")

    def invoke(self, context, _event):
        st = statemod.load()
        texs = st.get("textures") or []
        if 0 <= self.index < len(texs):
            self.path = texs[self.index].get("path", "")
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"贴图 {self.index}（路径最长 255 字节）")
        col.prop(self, "path", text="")
        col.operator("mdx.pick_texture_path", text="从模型已有贴图里选…").index = self.index

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        texs = st.get("textures") or []
        if not 0 <= self.index < len(texs):
            return {"CANCELLED"}
        new = self.path.replace("/", "\\").strip()
        if len(new.encode("latin-1", "replace")) > 255:
            self.report({"ERROR"}, "路径超过 255 字节")
            return {"CANCELLED"}
        texs[self.index]["path"] = new
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"贴图 {self.index} 路径已改")
        return {"FINISHED"}


class MDX_OT_pick_texture_path(bpy.types.Operator):
    bl_idname = "mdx.pick_texture_path"
    bl_label = "选贴图"
    bl_property = "value"

    index: IntProperty()
    value: EnumProperty(name="贴图", items=_texture_enum)

    def invoke(self, context, _event):
        return context.window_manager.invoke_search_popup(self)

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        src = int(self.value)
        texs = st.get("textures") or []
        if not (0 <= src < len(texs) and 0 <= self.index < len(texs)):
            return {"CANCELLED"}
        texs[self.index]["path"] = texs[src].get("path", "")
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"贴图 {self.index} 已指向 {texs[src].get('path', '')[-40:]}")
        return {"FINISHED"}


# ---------- 材质图层 ----------

class MDX_OT_assign_texture(bpy.types.Operator):
    bl_idname = "mdx.assign_texture"
    bl_label = "选贴图"
    bl_description = "给这个槽位指定一张贴图（搜索模型里的贴图表）"
    bl_property = "value"

    target: StringProperty()          # "layer:<材质>:<层>:<槽名>"
    value: EnumProperty(name="贴图", items=_texture_enum)

    def invoke(self, context, _event):
        return context.window_manager.invoke_search_popup(self)

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        parts = self.target.split(":")
        if len(parts) != 4:
            return {"CANCELLED"}
        mi, li, slot = int(parts[1]), int(parts[2]), parts[3]
        lay = statemod.layer_of(st, mi, li)
        if lay is None:
            self.report({"ERROR"}, "找不到对应的材质图层")
            return {"CANCELLED"}
        idx = int(self.value)
        if idx < 0:
            return {"CANCELLED"}

        if slot == "Diffuse" and not (lay.get("hd") or {}):
            lay["texture_id"] = idx
        else:
            hd = lay.get("hd")
            if not hd:
                # 低版本模型没有 HD 槽：直接改历史字段 textureId
                lay["texture_id"] = idx
            else:
                if slot not in hd:
                    self.report({"ERROR"}, f"该图层没有 {slot} 槽")
                    return {"CANCELLED"}
                hd[slot] = idx
        statemod.save(st)
        _mark_dirty()

        if slot == "Diffuse":
            mat = _material_by_id(mi)
            if mat is not None:
                _restore_image(mat, st, mi, li)
        self.report({"INFO"}, f"材质 {mi} 层 {li} 的 {slot} → 贴图 {idx}（导出时写入）")
        return {"FINISHED"}


def _restore_image(mat, st, mi: int, li: int) -> None:
    """把状态里的贴图重新挂到材质上，让预览跟着变。"""
    from . import textures as texmod

    idx = statemod.diffuse_index(st, mi, li)
    if idx < 0:
        return
    root = bpy.context.scene.get("mdx_texture_root", "")
    root = Path(root) if root else None
    index = texmod.build_index(root) if root else {}
    path = statemod.texture_path(st, idx)
    file = texmod.resolve(path, root, index)
    if file is None:
        return
    image = texmod.load_image(file)
    if image is not None:
        texmod.set_image_on_material(mat, image, "UVMap")


class MDX_OT_layer_filter(bpy.types.Operator):
    bl_idname = "mdx.layer_filter"
    bl_label = "改 FilterMode"

    mat: IntProperty()
    layer: IntProperty()
    value: EnumProperty(name="FilterMode", items=FILTER_ITEMS)

    def invoke(self, context, _event):
        st = statemod.load()
        lay = statemod.layer_of(st, self.mat, self.layer)
        if lay is not None:
            self.value = str(int(lay.get("filter", 0)))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"材质 {self.mat} 层 {self.layer}")
        col.prop(self, "value", text="")

    def execute(self, context):
        st = _state_or_warn(self)
        lay = statemod.layer_of(st, self.mat, self.layer) if st else None
        if lay is None:
            return {"CANCELLED"}
        lay["filter"] = int(self.value)
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"材质 {self.mat} 层 {self.layer} → {FILTER_ITEMS[int(self.value)][1]}")
        return {"FINISHED"}


class MDX_OT_layer_alpha(bpy.types.Operator):
    bl_idname = "mdx.layer_alpha"
    bl_label = "改图层 Alpha"

    mat: IntProperty()
    layer: IntProperty()
    alpha: FloatProperty(name="Alpha", min=0.0, max=1.0, default=1.0, subtype="FACTOR")
    sync_geoa: BoolProperty(
        name="同时应用到 geoset alpha", default=False,
        description="图层 alpha 与 geoset alpha 是两处独立字段，多数情况下要一起调才是想要的效果")

    def invoke(self, context, _event):
        st = statemod.load()
        lay = statemod.layer_of(st, self.mat, self.layer)
        if lay is not None:
            self.alpha = float(lay.get("alpha", 1.0))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"材质 {self.mat} 层 {self.layer}")
        col.prop(self, "alpha", slider=True)
        col.prop(self, "sync_geoa")

    def execute(self, context):
        st = _state_or_warn(self)
        lay = statemod.layer_of(st, self.mat, self.layer) if st else None
        if lay is None:
            return {"CANCELLED"}
        lay["alpha"] = float(self.alpha)
        if self.sync_geoa:
            targets = _material_geosets(st, self.mat)
            for item in st.get("geoa") or []:
                if item.get("geoset") in targets:
                    item["alpha"] = float(self.alpha)
        statemod.save(st)
        _mark_dirty()
        _apply_viewport_alpha(self.mat, float(self.alpha))
        self.report({"INFO"}, f"材质 {self.mat} 层 {self.layer} alpha = {self.alpha:.2f}")
        return {"FINISHED"}


def _material_geosets(st, mat_id: int) -> set:
    """哪些 geoset 用了这个材质（从场景对象上读，导入时记的）。"""
    out = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or "mdx_material_id" not in obj:
            continue
        if int(obj["mdx_material_id"]) == mat_id and "mdx_geoset_index" in obj:
            out.add(int(obj["mdx_geoset_index"]))
    return out


def _apply_viewport_alpha(mat_id: int, alpha: float) -> None:
    mat = _material_by_id(mat_id)
    if mat is None:
        return
    try:
        mat.diffuse_color = (1.0, 1.0, 1.0, alpha)
    except Exception:                              # noqa: BLE001
        pass


# ---------- 动画序列 ----------

class MDX_OT_edit_sequence(bpy.types.Operator):
    bl_idname = "mdx.edit_sequence"
    bl_label = "改动画区间"

    index: IntProperty()
    start: IntProperty(name="起始（毫秒）", min=0)
    end: IntProperty(name="结束（毫秒）", min=0)
    non_looping: BoolProperty(name="不循环（只播一次）")

    def invoke(self, context, _event):
        st = statemod.load()
        seqs = st.get("sequences") or []
        if 0 <= self.index < len(seqs):
            self.start = int(seqs[self.index].get("start", 0))
            self.end = int(seqs[self.index].get("end", 0))
            self.non_looping = bool(seqs[self.index].get("non_looping", False))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        st = statemod.load()
        seqs = st.get("sequences") or []
        name = seqs[self.index].get("name", "") if 0 <= self.index < len(seqs) else ""
        fps = float(context.scene.render.fps or 24)
        col = self.layout.column()
        col.label(text=f"序列 {self.index}：{name}")
        col.prop(self, "start", text="起始 (ms)")
        col.prop(self, "end", text="结束 (ms)")
        box = col.box()
        box.label(text=f"= 第 {self.start / 1000 * fps:.2f} – {self.end / 1000 * fps:.2f} 帧"
                       f"（{fps:g} fps）")
        valid = self.end > self.start
        if not valid:
            box.label(text="结束必须大于起始", icon="ERROR")
        col.prop(self, "non_looping")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        seqs = st.get("sequences") or []
        if not 0 <= self.index < len(seqs):
            return {"CANCELLED"}
        if self.end <= self.start:
            self.report({"ERROR"}, "结束必须大于起始")
            return {"CANCELLED"}
        seqs[self.index]["start"] = int(self.start)
        seqs[self.index]["end"] = int(self.end)
        seqs[self.index]["non_looping"] = bool(self.non_looping)
        statemod.save(st)
        _mark_dirty()
        _refresh_markers(context, st)
        self.report({"INFO"}, f"序列 {self.index} 已改（导出时写入）")
        return {"FINISHED"}


def _refresh_markers(context, st) -> None:
    """把时间轴标记与状态对齐——改了区间之后不刷新，标记就骗人了。"""
    fps = float(context.scene.render.fps or 24)
    seqs = st.get("sequences") or []
    try:
        for m in list(context.scene.timeline_markers):
            context.scene.timeline_markers.remove(m)
        end = 0
        for s in seqs:
            sf = s["start"] / 1000.0 * fps
            ef = s["end"] / 1000.0 * fps
            end = max(end, int(round(ef)))
            context.scene.timeline_markers.new(s.get("name") or f"seq{s['index']}",
                                               frame=int(round(sf)))
        if seqs:
            context.scene.frame_end = end
    except Exception:                              # noqa: BLE001
        pass


# ---------- 几何动画（GEOA） ----------

class MDX_OT_edit_geoa(bpy.types.Operator):
    bl_idname = "mdx.edit_geoa"
    bl_label = "改几何动画"

    index: IntProperty()
    alpha: FloatProperty(name="Alpha", min=0.0, max=1.0, default=1.0, subtype="FACTOR")
    color: FloatVectorProperty(name="颜色", subtype="COLOR", size=3, min=0.0, max=1.0,
                               default=(1.0, 1.0, 1.0))

    def invoke(self, context, _event):
        st = statemod.load()
        items = st.get("geoa") or []
        if 0 <= self.index < len(items):
            self.alpha = float(items[self.index].get("alpha", 1.0))
            c = items[self.index].get("color") or [1, 1, 1]
            self.color = (float(c[0]), float(c[1]), float(c[2]))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        st = statemod.load()
        items = st.get("geoa") or []
        gid = items[self.index].get("geoset", -1) if 0 <= self.index < len(items) else -1
        col.label(text=f"几何动画 {self.index} → geoset {gid}")
        col.prop(self, "alpha", slider=True)
        col.prop(self, "color")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("geoa") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}
        items[self.index]["alpha"] = float(self.alpha)
        items[self.index]["color"] = [float(c) for c in self.color]
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"几何动画 {self.index} 已改（导出时写入）")
        return {"FINISHED"}


# ---------- 轴心点 ----------

class MDX_OT_edit_pivot(bpy.types.Operator):
    bl_idname = "mdx.edit_pivot"
    bl_label = "改轴心点"
    bl_description = "PIVT 里的轴心点坐标；附着点、武器挂点都靠它定位"

    index: IntProperty()
    xyz: FloatVectorProperty(name="坐标", subtype="TRANSLATION", size=3, default=(0, 0, 0))
    use_cursor: BoolProperty(name="取 3D 游标位置", default=False)
    use_bone: BoolProperty(name="取选中骨骼的头部", default=False)
    move_bone: BoolProperty(name="Blender 里的骨骼头跟着移动", default=True)

    def invoke(self, context, _event):
        st = statemod.load()
        items = st.get("pivots") or []
        if 0 <= self.index < len(items):
            x = items[self.index].get("xyz") or [0, 0, 0]
            self.xyz = (float(x[0]), float(x[1]), float(x[2]))
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        st = statemod.load()
        items = st.get("pivots") or []
        item = items[self.index] if 0 <= self.index < len(items) else {}
        col = self.layout.column()
        label = item.get("node") or "（未能确认对应的节点）"
        col.label(text=f"轴心点 {self.index} → {label}")
        col.prop(self, "xyz")
        box = col.box()
        box.label(text="也可以从场景里取位置：")
        box.prop(self, "use_cursor")
        box.prop(self, "use_bone")
        col.prop(self, "move_bone", text="同时移动 Blender 骨骼头")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("pivots") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}

        xyz = [float(c) for c in self.xyz]
        if self.use_cursor:
            xyz = list(context.scene.cursor.location)
        elif self.use_bone:
            bone = _active_bone(context)
            if bone is None:
                self.report({"ERROR"}, "没有选中的骨骼（请先在骨架里点一根骨头）")
                return {"CANCELLED"}
            xyz = list(bone.head_local)

        old = items[self.index].get("xyz") or [0, 0, 0]
        items[self.index]["xyz"] = xyz
        statemod.save(st)
        _mark_dirty()

        if self.move_bone:
            _move_bone_to(context, self.index, xyz,
                          [xyz[k] - float(old[k]) for k in range(3)])
        self.report({"INFO"}, f"轴心点 {self.index} = ({xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f})")
        return {"FINISHED"}


def _active_bone(context):
    """当前选中的骨骼：编辑/姿态模式下取活动骨，物体模式下取骨架的活动骨。"""
    if context.active_bone is not None:
        return context.active_bone
    pose = getattr(context, "active_pose_bone", None)
    if pose is not None:
        return pose.bone
    obj = context.object
    if obj is not None and obj.type == "ARMATURE":
        return obj.data.bones.active
    return None


def _bone_name_map(arm) -> dict:
    raw = arm.get("mdx_bone_names") if arm else None
    if not raw:
        return {}
    try:
        return {int(k): v for k, v in json.loads(raw).items()}
    except Exception:                              # noqa: BLE001
        return {}


def _move_bone_to(context, pivot_index: int, xyz, delta) -> None:
    """轴心点与骨骼头是同一个概念的两处投影，改了 PIVT 就把骨骼头挪过去。"""
    arm = bpy.data.objects.get("MDX_Armature")
    if arm is None:
        return
    name = _bone_name_map(arm).get(pivot_index)
    if not name:
        return
    try:
        if context.object is not arm:
            for o in context.selected_objects:
                o.select_set(False)
            arm.select_set(True)
            context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        eb = arm.data.edit_bones.get(name)
        if eb is not None:
            eb.head = xyz
            eb.tail = (xyz[0], xyz[1] + 5.0, xyz[2])
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:                              # noqa: BLE001
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:                          # noqa: BLE001
            pass


# ---------- 预览 ----------

class MDX_OT_preview_texture(bpy.types.Operator):
    bl_idname = "mdx.preview_texture"
    bl_label = "看大图"
    bl_description = "在图像编辑器里打开这张贴图"

    index: IntProperty()

    def execute(self, context):
        from . import textures as texmod

        st = statemod.load()
        path = statemod.texture_path(st, self.index)
        if not path:
            self.report({"ERROR"}, "这条贴图路径为空")
            return {"CANCELLED"}
        root = context.scene.get("mdx_texture_root", "")
        root = Path(root) if root else None
        file = texmod.resolve(path, root, texmod.build_index(root) if root else {})
        if file is None:
            self.report({"ERROR"}, f"本地找不到文件：{path}")
            return {"CANCELLED"}
        image = texmod.load_image(file)
        if image is None:
            self.report({"ERROR"}, f"Blender 读不了这个格式：{file.suffix}")
            return {"CANCELLED"}
        for area in context.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
                self.report({"INFO"}, f"已在图像编辑器打开 {file.name}")
                return {"FINISHED"}
        self.report({"WARNING"}, f"没有打开的图像编辑器窗口；贴图是 {file}")
        return {"CANCELLED"}


class MDX_OT_reload_from_state(bpy.types.Operator):
    bl_idname = "mdx.reload_from_state"
    bl_label = "按状态刷新预览"
    bl_description = "按当前状态里的贴图路径，重新把贴图挂到各材质上"

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        done = 0
        for obj in bpy.data.objects:
            if obj.type != "MESH" or "mdx_material_id" not in obj:
                continue
            mi = int(obj["mdx_material_id"])
            for slot in obj.material_slots:
                if slot.material:
                    _restore_image(slot.material, st, mi, 0)
                    done += 1
                    break
        self.report({"INFO"}, f"刷新了 {done} 个材质")
        return {"FINISHED"}


# ---------- 面板 ----------

def _panel_header(layout, text: str, icon: str = "NONE"):
    row = layout.row()
    row.label(text=text, icon=icon)


class MDX_PT_textures(bpy.types.Panel):
    bl_label = "贴图（含一键改路径）"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return

        c = statemod.counts(st)
        box = layout.box()
        box.label(text=f"{c['textures']} 条：内置 {c['builtin']}  "
                       f"外部 {c['external']}  可替换 {c['replaceable']}")
        row = box.row(align=True)
        row.operator("mdx.rewrite_textures", text="一键改外部路径…", icon="FILE_REFRESH")
        row.operator("mdx.reload_from_state", text="", icon="FILE_REFRESH")

        texs = st.get("textures") or []
        ext = [t for t in texs if t.get("class") == "external"]
        if ext:
            box = layout.box()
            box.label(text=f"外部（会跟着模型走）{len(ext)} 条", icon="UNLOCKED")
            for t in ext:
                r = box.row(align=True)
                r.label(text=f"{t['index']:>3} {Path(t['path'] or '（空）').name}"[:44])
                op = r.operator("mdx.preview_texture", text="", icon="IMAGE_DATA")
                op.index = t["index"]
                op = r.operator("mdx.toggle_texture_lock", text="",
                                icon="LOCKED" if t.get("locked") else "UNLOCKED")
                op.index = t["index"]
                op = r.operator("mdx.set_texture_path", text="", icon="GREASEPENCIL")
                op.index = t["index"]

        others = [t for t in texs if t.get("class") != "external"]
        if others:
            box = layout.box()
            box.label(text=f"内置 / 可替换 {len(others)} 条（默认不改）", icon="LOCKED")
            show = [t for t in others if t.get("locked") is False][:12]
            for t in show:
                r = box.row(align=True)
                r.label(text=f"{t['index']:>3} {Path(t['path'] or '（空）').name}"[:44])
                r.label(text=CLASS_TEXT.get(t.get("class", ""), ""))
                op = r.operator("mdx.toggle_texture_lock", text="", icon="UNLOCKED")
                op.index = t["index"]
            if len(others) > len(show):
                box.label(text=f"…共 {len(others)} 条，完整清单见导出报告")

        layout.label(text=f"贴图目录：{context.scene.get('mdx_texture_root', '') or '未设置'}",
                     icon="FILE_FOLDER")
        if context.scene.get("mdx_dirty"):
            layout.label(text="有改动尚未导出", icon="ERROR")


class MDX_PT_materials(bpy.types.Panel):
    bl_label = "材质与图层"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return

        mats = st.get("materials") or []
        if not mats:
            layout.label(text="模型没有材质块", icon="INFO")
            return

        layout.label(text="改完导出才写入文件；α 是静态值，动画轨道另算", icon="INFO")

        for obj in bpy.data.objects:
            if obj.type != "MESH" or "mdx_material_id" not in obj:
                continue
            mi = int(obj["mdx_material_id"])
            if not 0 <= mi < len(mats):
                continue
            mat = obj.material_slots[0].material if obj.material_slots else None

            box = layout.box()
            head = box.row(align=True)
            icon_id = _image_icon(_material_image(mat))
            if icon_id:
                head.label(text="", icon_value=icon_id)
            else:
                head.label(text="", icon="IMAGE_DATA")
            head.label(text=f"{obj.name} → 材质 {mi}")

            for lay in mats[mi].get("layers") or []:
                li = lay["index"]
                self._layer_row(box, st, mi, li, lay)

            if any(not l.get("hd_trusted", True) for l in mats[mi].get("layers") or []):
                box.label(text="HD 槽后还有子块，贴图改动会被跳过", icon="ERROR")

    def _layer_row(self, box, st, mi, li, lay):
        filter_idx = int(lay.get("filter", 0)) % len(FILTER_ITEMS)
        row = box.row(align=True)
        row.label(text=f"层{li}")
        op = row.operator("mdx.layer_filter", text=FILTER_ITEMS[filter_idx][1].split("（")[0])
        op.mat, op.layer = mi, li
        op = row.operator("mdx.layer_alpha", text=f"α {lay.get('alpha', 1.0):.2f}",
                          icon="IMAGE_ALPHA")
        op.mat, op.layer = mi, li

        hd = lay.get("hd") or {}
        self._slot_row(box, st, mi, li, "Diffuse", "漫反射", hd)
        for name in ("Normal", "ORM", "Emissive", "TeamColor", "Reflections"):
            if name in hd:
                self._slot_row(box, st, mi, li, name, name, hd)

    def _slot_row(self, box, st, mi, li, slot, label, hd):
        if slot == "Diffuse":
            idx = hd["Diffuse"] if "Diffuse" in hd else int(
                statemod.layer_of(st, mi, li).get("texture_id", -1))
        else:
            idx = int(hd.get(slot, -1))
        path = statemod.texture_path(st, idx) if idx >= 0 else ""

        row = box.row(align=True)
        row.label(text=f"    {label}")
        row.label(text=Path(path).name if path else "（无）",
                  icon="FILE_IMAGE" if path else "ERROR")
        op = row.operator("mdx.assign_texture", text="", icon="VIEWZOOM")
        op.target = f"layer:{mi}:{li}:{slot}"
        if idx >= 0:
            op = row.operator("mdx.preview_texture", text="", icon="IMAGE_DATA")
            op.index = idx


class MDX_PT_sequences(bpy.types.Panel):
    bl_label = "动画序列"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return
        seqs = st.get("sequences") or []
        if not seqs:
            layout.label(text="模型没有动画序列块", icon="INFO")
            return

        fps = float(context.scene.render.fps or 24)
        frame = context.scene.frame_current
        box = layout.box()
        box.label(text=f"{len(seqs)} 段（共享一条时间轴，单位毫秒）")
        for s in seqs:
            sf = s["start"] / 1000.0 * fps
            ef = s["end"] / 1000.0 * fps
            active = sf <= frame <= ef
            row = box.row(align=True)
            row.label(text=("▶ " if active else "  ") + (s.get("name") or f"seq{s['index']}")[:22],
                      icon="PLAY" if active else "NONE")
            row.label(text=f"{s['start']}–{s['end']}")
            row.label(text="单次" if s.get("non_looping") else "循环")
            op = row.operator("mdx.jump_sequence", text="", icon="FRAME_NEXT")
            op.index = s["index"]
            op = row.operator("mdx.edit_sequence", text="", icon="GREASEPENCIL")
            op.index = s["index"]


class MDX_OT_jump_sequence(bpy.types.Operator):
    bl_idname = "mdx.jump_sequence"
    bl_label = "跳到序列"
    index: IntProperty()

    def execute(self, context):
        st = statemod.load()
        seqs = st.get("sequences") or []
        if not 0 <= self.index < len(seqs):
            return {"CANCELLED"}
        fps = float(context.scene.render.fps or 24)
        context.scene.frame_current = int(round(seqs[self.index]["start"] / 1000.0 * fps))
        return {"FINISHED"}


class MDX_PT_geoa(bpy.types.Panel):
    bl_label = "几何动画（显隐 / 颜色）"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return
        items = st.get("geoa") or []
        if not items:
            layout.label(text="模型没有几何动画块", icon="INFO")
            return
        box = layout.box()
        box.label(text=f"{len(items)} 条（每条绑定一个 geoset）")
        for item in items:
            row = box.row(align=True)
            row.label(text=f"geoset {item.get('geoset', -1)}")
            row.label(text=f"α {item.get('alpha', 1.0):.2f}")
            c = item.get("color") or [1, 1, 1]
            row.label(text=f"色 ({c[0]:.2f}, {c[1]:.2f}, {c[2]:.2f})")
            op = row.operator("mdx.edit_geoa", text="", icon="GREASEPENCIL")
            op.index = item["index"]


class MDX_PT_pivots(bpy.types.Panel):
    bl_label = "轴心点 / 附着点"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return
        items = st.get("pivots") or []
        if not items:
            layout.label(text="模型没有轴心点块", icon="INFO")
            return

        note = st.get("pivot_note") or ""
        layout.label(text=f"{len(items)} 个" + (f"｜{note}" if note else ""), icon="INFO")

        box = layout.box()
        shown = 0
        for item in items:
            tag = item.get("tag") or ""
            name = item.get("node") or ""
            # 附着点 / 辅助点优先显示：这类才是"挂武器"真正要动的
            if not (tag in ("ATCH", "HELP") or context.scene.get("mdx_pivot_all")):
                continue
            shown += 1
            row = box.row(align=True)
            row.label(text=f"{'附着' if tag == 'ATCH' else '辅助' if tag == 'HELP' else '节点'}"
                            f"#{item['index']} {name}"[:34])
            x = item.get("xyz") or [0, 0, 0]
            row.label(text=f"({x[0]:.2f}, {x[1]:.2f}, {x[2]:.2f})")
            op = row.operator("mdx.edit_pivot", text="", icon="GREASEPENCIL")
            op.index = item["index"]
        if shown == 0:
            box.label(text="这个模型没有附着点/辅助点节点", icon="INFO")

        row = layout.row()
        row.prop(context.scene, "mdx_pivot_all", text="显示全部节点轴心点")


class MDX_PT_attachments(bpy.types.Panel):
    bl_label = "附着点（挂武器用）"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        if not st:
            layout.label(text="请先导入模型", icon="INFO")
            return
        items = st.get("attachments") or []
        if not items:
            layout.label(text="模型没有附着点（ATCH）", icon="INFO")
            return

        pivots = st.get("pivots") or []

        def node_name(oid):
            if 0 <= oid < len(pivots):
                p = pivots[oid]
                return p.get("node") or f"节点 {oid}"
            return f"节点 {oid}"

        layout.label(text=f"{len(items)} 个附着点（物体名以 ATCH_ 开头）", icon="EMPTY_AXIS")

        box = layout.box()
        for a in items:
            row = box.row(align=True)
            row.label(text=(a.get("name") or f"#{a['index']}")[:20])
            row.label(text=node_name(a.get("parent_id", -1))[:18])
            row.label(text=f"id {a.get('attachment_id', 0)}")

            r2 = box.row(align=True)
            path = a.get("path") or ""
            r2.label(text=Path(path).name if path else "（只作定位点，不挂模型）",
                     icon="LINKED" if path else "UNLINKED")
            op = r2.operator("mdx.attach_path", text="", icon="FILEBROWSER")
            op.index = a["index"]
            op = r2.operator("mdx.focus_attach", text="", icon="CURSOR")
            op.index = a["index"]
            op = r2.operator("mdx.attach_id", text="", icon="SORTALPHA")
            op.index = a["index"]
            op = r2.operator("mdx.attach_name", text="", icon="GREASEPENCIL")
            op.index = a["index"]
            op = r2.operator("mdx.remove_attachment", text="", icon="TRASH")
            op.index = a["index"]

        col = layout.column(align=True)
        col.operator("mdx.mount_weapon", text="挂载武器…", icon="LINKED")
        col.operator("mdx.add_attachment", text="新增附着点…", icon="ADD")
        layout.label(text="改完直接写盘并重新导入；导出后进编辑器查看", icon="INFO")


class MDX_OT_attach_path(bpy.types.Operator):
    bl_idname = "mdx.attach_path"
    bl_label = "设置挂载模型"
    bl_description = "这个附着点要挂哪个模型（游戏内路径，留空表示只作为定位点）"

    index: IntProperty()
    path: StringProperty(name="游戏内路径",
                         description=r"如 war3mapImported\Sword.mdx")
    from_local: BoolProperty(
        name="从本地文件推导", default=False,
        description="选一个本地 .mdx，自动写成 war3mapImported\\文件名")
    local_file: StringProperty(name="本地模型", subtype="FILE_PATH")

    def invoke(self, context, _event):
        st = statemod.load()
        items = st.get("attachments") or []
        if 0 <= self.index < len(items):
            self.path = items[self.index].get("path", "")
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        col = self.layout.column()
        st = statemod.load()
        items = st.get("attachments") or []
        name = items[self.index].get("name", "") if 0 <= self.index < len(items) else ""
        col.label(text=f"附着点「{name}」")
        col.prop(self, "path", text="")
        col.prop(self, "from_local")
        if self.from_local:
            col.prop(self, "local_file", text="")
            if self.local_file:
                col.label(text="将写成 war3mapImported\\" + Path(self.local_file).name,
                          icon="INFO")
        else:
            col.label(text=r"游戏内路径要带上目录，例如 war3mapImported\Sword.mdx",
                      icon="INFO")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("attachments") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}

        path = self.path
        if self.from_local:
            if not self.local_file:
                self.report({"ERROR"}, "勾了「从本地文件推导」但没选文件")
                return {"CANCELLED"}
            path = f"war3mapImported\\{Path(self.local_file).name}"
        path = (path or "").replace("/", "\\").strip()
        if len(path.encode("latin-1", "replace")) > 255:
            self.report({"ERROR"}, "路径超过 255 字节")
            return {"CANCELLED"}

        items[self.index]["path"] = path
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"附着点 {self.index} 挂载 → {path or '（空）'}")
        return {"FINISHED"}


class MDX_OT_attach_id(bpy.types.Operator):
    bl_idname = "mdx.attach_id"
    bl_label = "附着点编号"
    bl_description = "引擎按这个编号找挂点；不确定就沿用原值"

    index: IntProperty()
    value: IntProperty(name="编号", min=0, max=65535)

    def invoke(self, context, _event):
        st = statemod.load()
        items = st.get("attachments") or []
        if 0 <= self.index < len(items):
            self.value = int(items[self.index].get("attachment_id", 0))
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "value")
        col.label(text="0–65535；同一个模型里编号通常不重复", icon="INFO")

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("attachments") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}
        items[self.index]["attachment_id"] = int(self.value)
        statemod.save(st)
        _mark_dirty()
        self.report({"INFO"}, f"附着点 {self.index} 编号 → {self.value}")
        return {"FINISHED"}


class MDX_OT_attach_name(bpy.types.Operator):
    bl_idname = "mdx.attach_name"
    bl_label = "附着点名字"
    bl_description = "改名字只影响识别，不影响挂载；物体名会一起更新"

    index: IntProperty()
    name: StringProperty(name="名字")

    def invoke(self, context, _event):
        st = statemod.load()
        items = st.get("attachments") or []
        if 0 <= self.index < len(items):
            self.name = items[self.index].get("name", "")
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("attachments") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}
        new = self.name.strip()
        if not new:
            self.report({"ERROR"}, "名字不能为空")
            return {"CANCELLED"}
        items[self.index]["name"] = new
        statemod.save(st)
        _mark_dirty()

        # 视口里的空物体跟着改名，否则列表和场景对不上
        oid = items[self.index].get("object_id", -1)
        for obj in bpy.data.objects:
            if obj.get("mdx_node_index") == oid:
                obj.name = f"ATCH_{new}"
                break
        self.report({"INFO"}, f"附着点 {self.index} → {new}")
        return {"FINISHED"}


class MDX_OT_focus_attach(bpy.types.Operator):
    bl_idname = "mdx.focus_attach"
    bl_label = "定位到附着点"
    bl_description = "把 3D 游标移到这个附着点上，并选中它"

    index: IntProperty()

    def execute(self, context):
        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        items = st.get("attachments") or []
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}
        oid = items[self.index].get("object_id", -1)
        pivots = st.get("pivots") or []
        if not 0 <= oid < len(pivots):
            return {"CANCELLED"}
        xyz = pivots[oid].get("xyz") or [0, 0, 0]
        context.scene.cursor.location = xyz

        for obj in bpy.data.objects:
            obj.select_set(False)
        target = next((o for o in bpy.data.objects
                       if o.get("mdx_node_index") == oid), None)
        if target is not None:
            target.select_set(True)
            context.view_layer.objects.active = target
        self.report({"INFO"}, f"游标已移到「{items[self.index].get('name', '')}」"
                              f"({xyz[0]:.2f}, {xyz[1]:.2f}, {xyz[2]:.2f})")
        return {"FINISHED"}


def _bone_enum(self, context):
    """父节点候选：按 objectId 编号（那是轴心点下标，也是 parentId 该填的值）。"""
    from mdxcore.chunks import bone as bonemod

    try:
        from .disk import target_path
        mdx = get_core().load(target_path())
        bones = bonemod.parse(mdx.data(mdx.find("BONE")))
    except Exception:                              # noqa: BLE001
        return [("0", "（读不到骨骼）", "")]
    items = [(str(b.object_id), f"{b.object_id:>3}  {b.name}", b.name) for b in bones]
    return items or [("0", "（模型没有骨骼）", "")]


def _insert_at_enum(self, context):
    """插入位置：末尾，或某个现有附着点之前。"""
    st = statemod.load()
    items = [("-1", "末尾（编号最大）", "追加到节点表最后")]
    for a in st.get("attachments") or []:
        items.append((str(a["object_id"]), f"「{a.get('name', '')}」之前",
                      f"插到编号 {a['object_id']} 之前"))
    return items


class MDX_OT_add_attachment(bpy.types.Operator):
    bl_idname = "mdx.add_attachment"
    bl_label = "新增附着点"
    bl_description = "加一个附着点并挂上模型（会改写文件并重新导入）"

    name: StringProperty(name="名字", default="Weapon Ref")
    parent: EnumProperty(name="父节点", items=_bone_enum)
    at: EnumProperty(name="插入位置", items=_insert_at_enum)
    path: StringProperty(name="挂载模型", description=r"如 war3mapImported\Sword.mdx")
    from_local: BoolProperty(name="从本地文件推导", default=False)
    local_file: StringProperty(name="本地模型", subtype="FILE_PATH")
    use_cursor: BoolProperty(name="坐标取 3D 游标", default=False,
                             description="不勾则用父节点的轴心点")

    def invoke(self, context, _event):
        st = statemod.load()
        if not st:
            self.report({"ERROR"}, "请先导入模型")
            return {"CANCELLED"}
        # 默认父节点取名字里带 hand right 的骨骼，没有就用第一根
        for b in _bone_enum(None, context):
            if "hand" in b[2].lower() and "r" in b[2].lower():
                self.parent = b[0]
                break
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        layout = self.layout
        from .disk import target_path

        try:
            name = target_path().name
        except Exception:                          # noqa: BLE001
            name = "模型"
        box = layout.box()
        box.label(text=f"会改写 {name}（备份为 {name}.bak）并重新导入", icon="ERROR")
        box.label(text="会重编号它后面所有节点，父子关系同步调整", icon="INFO")

        col = layout.column()
        col.prop(self, "name")
        col.prop(self, "parent")
        col.prop(self, "at")
        col.prop(self, "path", text="")
        col.prop(self, "from_local")
        if self.from_local:
            col.prop(self, "local_file", text="")
        col.prop(self, "use_cursor")

        # 可加性检查：不满足就别让用户按下去
        try:
            from mdxcore import nodesedit
            info = nodesedit.inspect(get_core().load(target_path()))
            box = layout.box()
            if info["ok"]:
                box.label(text=f"可以新增：下一个编号 {info['next_id']}"
                               f"（现有节点最大 {info['max_node_id']}）",
                          icon="CHECKMARK")
            else:
                box.label(text=info["reason"], icon="ERROR")
        except Exception as exc:                   # noqa: BLE001
            layout.label(text=str(exc)[:80], icon="ERROR")

    def execute(self, context):
        from mdxcore import nodesedit
        from .disk import CommitError, commit, target_path

        try:
            mdx = get_core().load(target_path())
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        path = self.path
        if self.from_local:
            if not self.local_file:
                self.report({"ERROR"}, "勾了「从本地文件推导」但没选文件")
                return {"CANCELLED"}
            path = f"war3mapImported\\{Path(self.local_file).name}"

        xyz = list(context.scene.cursor.location) if self.use_cursor else None
        at = int(self.at)
        try:
            replacements, info = nodesedit.insert_attachment(
                mdx, name=self.name, parent_id=int(self.parent),
                path=path, xyz=xyz, at=None if at < 0 else at)
            result = commit(mdx, replacements)
        except nodesedit.NodeEditError as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}
        except CommitError as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"已新增附着点「{self.name}」编号 {info['object_id']}"
                    f"（{info['position']}，重编号 {info['shifted']} 处），"
                    f"{result['bytes_before']} → {result['bytes_after']} B")
        return {"FINISHED"}


class MDX_OT_remove_attachment(bpy.types.Operator):
    bl_idname = "mdx.remove_attachment"
    bl_label = "删除附着点"
    bl_description = "删除编号最大的那个附着点（会改写文件并重新导入）"

    index: IntProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        items = st.get("attachments") or []
        a = items[self.index] if 0 <= self.index < len(items) else {}
        from .disk import target_path

        try:
            name = target_path().name
        except Exception:                          # noqa: BLE001
            name = "模型"
        box = layout.box()
        box.label(text=f"会删掉「{a.get('name', '')}」（编号 {a.get('object_id', -1)}）",
                  icon="ERROR")
        box.label(text=f"并改写 {name}（备份为 {name}.bak）", icon="INFO")
        box.label(text="只支持删编号最大的那一条；删中间的会牵动全局索引", icon="INFO")

    def execute(self, context):
        from mdxcore import nodesedit
        from .disk import CommitError, commit, target_path

        try:
            mdx = get_core().load(target_path())
            replacements, info = nodesedit.remove_attachment(mdx, self.index)
            result = commit(mdx, replacements)
        except (nodesedit.NodeEditError, CommitError) as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"已删除「{info['name']}」，"
                    f"{result['bytes_before']} → {result['bytes_after']} B")
        return {"FINISHED"}


def _attach_enum(self, context):
    """挂在哪个附着点上。"""
    st = statemod.load()
    items = []
    for a in st.get("attachments") or []:
        path = a.get("path") or ""
        items.append((str(a["index"]),
                      f"{a.get('name', '')}" + (f"（已挂 {Path(path).name}）" if path else ""),
                      f"编号 {a.get('object_id', -1)}"))
    return items or [("-1", "（模型没有附着点）", "")]


class MDX_OT_mount_weapon(bpy.types.Operator):
    bl_idname = "mdx.mount_weapon"
    bl_label = "挂载武器"
    bl_description = "选一个武器模型 + 一个附着点，自动写路径并把文件放到模型旁边"

    attach: EnumProperty(name="挂在", items=_attach_enum)
    mode: EnumProperty(name="武器来源", items=[
        ("FILE", "本地文件", "选一个 .mdx，自动复制到模型目录并写成 war3mapImported\\文件名"),
        ("GAME", "游戏自带", "直接引用游戏内路径，不需要 import"),
    ])
    local_file: StringProperty(name="武器模型", subtype="FILE_PATH")
    game_path: StringProperty(name="游戏内路径",
                             description=r"如 abilities\weapons\axe\axemissile.mdx")

    def invoke(self, context, _event):
        st = statemod.load()
        if not st or not st.get("attachments"):
            self.report({"ERROR"}, "模型没有附着点，请先用「新增附着点」")
            return {"CANCELLED"}
        # 默认挑一个名字里带 weapon 或 hand 的
        for a in st["attachments"]:
            n = (a.get("name") or "").lower()
            if "weapon" in n or "hand" in n:
                self.attach = str(a["index"])
                break
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        st = statemod.load()
        idx = int(self.attach)
        items = st.get("attachments") or []
        a = items[idx] if 0 <= idx < len(items) else {}

        col = layout.column()
        col.prop(self, "attach")
        col.prop(self, "mode", expand=True)
        if self.mode == "FILE":
            col.prop(self, "local_file", text="")
            if self.local_file:
                col.label(text="将写成 war3mapImported\\" + Path(self.local_file).name
                               + "，并复制到模型同级目录", icon="INFO")
                col.label(text="进游戏前记得在编辑器/地图里 import 这个文件", icon="ERROR")
        else:
            col.prop(self, "game_path", text="")
            col.label(text=r"例：abilities\weapons\axe\axemissile.mdx", icon="INFO")

        box = layout.box()
        box.label(text=f"目标附着点：{a.get('name', '')}（编号 {a.get('object_id', -1)}）",
                  icon="EMPTY_AXIS")
        box.label(text="改完直接写盘并重新导入；导出时正式生效")

    def execute(self, context):
        from . import textures as texmod
        from .disk import CommitError, commit, target_path

        st = _state_or_warn(self)
        if not st:
            return {"CANCELLED"}
        try:
            idx = int(self.attach)
        except ValueError:
            self.report({"ERROR"}, "请选择附着点")
            return {"CANCELLED"}
        items = st.get("attachments") or []
        if not 0 <= idx < len(items):
            self.report({"ERROR"}, "附着点不存在")
            return {"CANCELLED"}

        if self.mode == "FILE":
            if not self.local_file:
                self.report({"ERROR"}, "请选择武器模型文件")
                return {"CANCELLED"}
            src = Path(self.local_file)
            if not src.is_file():
                self.report({"ERROR"}, f"文件不存在：{src}")
                return {"CANCELLED"}
            try:
                mdx = get_core().load(src)
                if not any(c.tag == "MODL" for c in mdx.chunks):
                    raise ValueError("不是 MDX 文件")
            except Exception as exc:               # noqa: BLE001
                self.report({"ERROR"}, f"武器模型读不了：{exc}"[:200])
                return {"CANCELLED"}

            # 放到模型同级目录，路径写 war3mapImported\<文件名>
            try:
                dst_dir = target_path().parent
            except CommitError as exc:
                self.report({"ERROR"}, str(exc)[:200])
                return {"CANCELLED"}
            target = dst_dir / src.name
            if not (target.exists() and target.samefile(src)):
                import shutil
                try:
                    shutil.copyfile(src, target)
                except OSError as exc:
                    self.report({"ERROR"}, f"复制失败：{exc}")
                    return {"CANCELLED"}
            path = f"war3mapImported\\{src.name}"
        else:
            path = (self.game_path or "").replace("/", "\\").strip()
            if not path:
                self.report({"ERROR"}, "请填写游戏内路径")
                return {"CANCELLED"}
            if len(path.encode("latin-1", "replace")) > 255:
                self.report({"ERROR"}, "路径超过 255 字节")
                return {"CANCELLED"}

        before = items[idx].get("path", "")
        items[idx]["path"] = path
        statemod.save(st)
        _mark_dirty()

        # 顺手把预览也接上（能找到文件就加载）
        try:
            from mdxcore.chunks import atch as atchmod
            mdx0 = get_core().load(target_path())
            file_atc = atchmod.parse(mdx0.data(mdx0.find("ATCH")))
            k = file_atc[idx].object_id
            parent = st["pivots"][k].get("node") if 0 <= k < len(st.get("pivots") or []) else ""
        except Exception:                          # noqa: BLE001
            parent = ""

        self.report({"INFO"},
                    f"「{items[idx].get('name', '')}」→ {Path(path).name}"
                    f"（父节点 {parent or '?'}）；导出后进编辑器查看")
        return {"FINISHED"}


CLASSES = (
    MDX_OT_rewrite_textures,
    MDX_OT_toggle_texture_lock,
    MDX_OT_set_texture_path,
    MDX_OT_pick_texture_path,
    MDX_OT_assign_texture,
    MDX_OT_layer_filter,
    MDX_OT_layer_alpha,
    MDX_OT_edit_sequence,
    MDX_OT_jump_sequence,
    MDX_OT_edit_geoa,
    MDX_OT_edit_pivot,
    MDX_OT_preview_texture,
    MDX_OT_reload_from_state,
    MDX_OT_attach_path,
    MDX_OT_attach_id,
    MDX_OT_attach_name,
    MDX_OT_focus_attach,
    MDX_OT_add_attachment,
    MDX_OT_remove_attachment,
    MDX_OT_mount_weapon,
    MDX_PT_textures,
    MDX_PT_materials,
    MDX_PT_sequences,
    MDX_PT_geoa,
    MDX_PT_pivots,
    MDX_PT_attachments,
)


def register_props():
    bpy.types.Scene.mdx_pivot_all = BoolProperty(
        name="显示全部轴心点", default=False,
        description="默认只列附着点/辅助点；勾上后列出所有节点的轴心点")


def unregister_props():
    for attr in ("mdx_pivot_all",):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
