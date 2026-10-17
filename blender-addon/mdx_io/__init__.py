"""mdx_io — Retera Model Studio 现代化项目的 Blender 前端（原型）。

定位：Blender 负责几何 / UV / 动画编辑，本项目自研的 mdxcore 负责格式读写。
铁律：**二进制是真相，Blender 场景只是视图。**

当前能力（v1）
- 导入：GEOS → 网格（顶点/三角面/第一套 UV）、PIVT → 空物体（带节点名）、
        每个 geoset 一个材质槽（贴图路径写入自定义属性）
- 导出：读回原始文件，逐项比对场景与文件，确认未被 Blender 改坏，然后写出原始字节
- 报告：比对结果打印到控制台并写入场景自定义属性 `mdx_last_report`

尚未支持（刻意）
- 法线导入、蒙皮绑定、动画导入
- GEOS 写回（结构变更，v1 不做）

许可：MIT（与上游 Retera's Model Studio 一致）。分发时请保留上游署名。
"""

bl_info = {
    "name": "MDX (Warcraft III)",
    "author": "based on Retera's Model Studio (MIT)",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "File > Import / Export",
    "description": "导入导出 Warcraft III MDX 模型（Reforged），格式内核复用 mdxcore",
    "category": "Import-Export",
}

import json
from pathlib import Path

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.props import StringProperty

from .importer import import_mdx
from .exporter import export_mdx
from . import ui_edit
from . import ui_binding
from . import ui_batch


class MDX_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "wm.mdx_import"
    bl_label = "导入 MDX"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".mdx"
    filter_glob: StringProperty(default="*.mdx", options={"HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH")
    texture_root: StringProperty(
        name="贴图目录",
        description="导出的贴图放在这里（按 TEXS 里的相对路径存放，或只放文件名）",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        try:
            meta = import_mdx(self.filepath, texture_root=self.texture_root)
        except Exception as exc:                       # noqa: BLE001
            self.report({"ERROR"}, f"导入失败：{exc}")
            return {"CANCELLED"}
        extra = ""
        if meta.get("textures_loaded"):
            extra = f"，加载贴图 {meta['textures_loaded']} 张"
        elif meta.get("textures_missing"):
            extra = f"，贴图缺失 {meta['textures_missing']} 张（可设置贴图目录）"
        if self.texture_root:
            settings.remember_textures(self.texture_root)
        context.scene["mdx_dirty"] = False
        self.report(
            {"INFO"},
            f"已导入 {meta['model_name'] or meta['source_path']}："
            f"{meta['geoset_count']} 个 geoset，{meta['node_count']} 个节点，"
            f"{len(meta.get('sequences') or [])} 段动画{extra}",
        )
        return {"FINISHED"}


class MDX_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "wm.mdx_export"
    bl_label = "导出 MDX"
    bl_options = {"PRESET"}

    filename_ext = ".mdx"
    filter_glob: StringProperty(default="*.mdx", options={"HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        try:
            report, written = export_mdx(self.filepath)
        except Exception as exc:                       # noqa: BLE001
            self.report({"ERROR"}, f"导出失败：{exc}")
            return {"CANCELLED"}

        context.scene["mdx_last_report"] = json.dumps(report, ensure_ascii=False, indent=2)
        if report.get("verified"):
            context.scene["mdx_dirty"] = False

        if report["byte_identical"] and report["geometry_unchanged"]:
            self.report({"INFO"}, f"已写出，与原始文件字节完全相同：{written}")
        elif report["geometry_unchanged"]:
            self.report({"WARNING"}, "已写出，但字节与原始文件不同，请查看报告")
        else:
            self.report(
                {"WARNING"},
                f"检测到几何改动（{len(report['problems'])} 项），v1 不支持 GEOS 写回，已写出原始字节",
            )
        return {"FINISHED"}


class MDX_OT_import_casc(bpy.types.Operator):
    bl_idname = "wm.mdx_import_casc"
    bl_label = "从游戏数据导入"
    bl_description = "直接从 WC3 Reforged 的 CASC 读模型（需要游戏安装目录）"
    bl_options = {"REGISTER"}

    install: StringProperty(name="安装目录", subtype="DIR_PATH")
    casc_path: StringProperty(name="模型路径", description="如 units\\humanfootman\\footman.mdx")

    def invoke(self, context, _event):
        scene = context.scene
        if not self.install:
            self.install = scene.get("mdx_casc_install", "") or settings.get("casc_install", "")
        if not self.install:
            from mdxcore.casc import find_install

            found = find_install()
            if found:
                self.install = str(found)
        if not self.casc_path:
            self.casc_path = scene.get("mdx_casc_path", "") or settings.get("casc_path", "")
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "install", text="安装目录")
        col.prop(self, "casc_path", text="模型路径")

    def execute(self, context):
        try:
            meta = import_mdx(self.casc_path, casc_install=self.install)
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        scene = context.scene
        scene["mdx_casc_install"] = self.install
        scene["mdx_casc_path"] = self.casc_path
        settings.remember_casc(self.install, self.casc_path)
        info = meta.get("casc") or {}
        extra = ""
        if info.get("textures_saved"):
            extra = f"，贴图 {info['textures_saved']} 张"
        if info.get("textures_missing"):
            extra += f"（缺 {info['textures_missing']}）"
        self.report(
            {"INFO"},
            f"从游戏导入 {self.casc_path}：{meta['geoset_count']} 个 geoset"
            f"，{meta['bone_count']} 根骨骼{extra}",
        )
        return {"FINISHED"}


class MDX_OT_search_casc(bpy.types.Operator):
    bl_idname = "wm.mdx_search_casc"
    bl_label = "搜索游戏模型"
    bl_description = "在游戏数据里按名字找模型"

    install: StringProperty(name="安装目录", subtype="DIR_PATH")
    pattern: StringProperty(name="关键字", default="footman")

    def invoke(self, context, _event):
        if not self.install:
            self.install = (context.scene.get("mdx_casc_install", "")
                            or settings.get("casc_install", ""))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "install", text="安装目录")
        col.prop(self, "pattern", text="关键字")

    def execute(self, context):
        import re

        from .casc_source import CascSourceError, list_models

        try:
            hits = list_models(Path(self.install), rf"{re.escape(self.pattern)}.*\.mdx$", limit=40)
        except CascSourceError as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        if not hits:
            self.report({"WARNING"}, f"没有匹配 {self.pattern!r} 的模型")
            return {"CANCELLED"}

        context.scene["mdx_casc_install"] = self.install
        context.scene["mdx_casc_path"] = hits[0]
        settings.remember_casc(self.install, hits[0])
        print("[MDX] 匹配到的模型：")
        for h in hits:
            print(f"    {h}")
        self.report({"INFO"}, f"找到 {len(hits)} 个，已填入第一个：{hits[0]}（完整列表见控制台）")
        return {"FINISHED"}


class MDX_OT_reload_textures(bpy.types.Operator):
    bl_idname = "wm.mdx_reload_textures"
    bl_label = "重新加载贴图"
    bl_description = "按当前的贴图目录，重新解析并挂上材质贴图"

    def execute(self, context):
        from . import textures

        root = context.scene.get("mdx_texture_root", "")
        if not root:
            self.report({"ERROR"}, "请先设置贴图目录")
            return {"CANCELLED"}

        index = textures.build_index(Path(root))
        coll = bpy.data.collections.get("MDX")
        if not coll:
            self.report({"ERROR"}, "当前场景没有 MDX 模型")
            return {"CANCELLED"}

        loaded = missing = 0
        for obj in coll.objects:
            if obj.type != "MESH":
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or not mat.get("mdx_texture_path"):
                    continue
                file = textures.resolve(mat["mdx_texture_path"], Path(root), index)
                if file is None:
                    missing += 1
                    continue
                image = textures.load_image(file)
                if image and textures.attach_to_material(mat, image, "UVMap"):
                    mat["mdx_texture_file"] = str(file)
                    loaded += 1

        self.report({"INFO"}, f"加载 {loaded} 张贴图，缺失 {missing} 张")
        return {"FINISHED"}


class MDX_PT_panel(bpy.types.Panel):
    bl_label = "MDX"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("wm.mdx_import", text="导入 MDX")
        col.operator("wm.mdx_export", text="导出 MDX（校验）")

        casc_box = layout.box()
        casc_box.label(text="游戏数据（CASC）")
        install = context.scene.get("mdx_casc_install", "") or settings.get("casc_install", "")
        if install:
            casc_box.label(text=install[-46:], icon="DISK_DRIVE")
        else:
            casc_box.label(text="尚未设置安装目录", icon="INFO")
        row = casc_box.row(align=True)
        row.operator("wm.mdx_search_casc", text="搜索")
        row.operator("wm.mdx_import_casc", text="导入")
        casc_path = context.scene.get("mdx_casc_path", "") or settings.get("casc_path", "")
        if casc_path:
            casc_box.label(text=casc_path[-46:], icon="FILE")

        tex_box = layout.box()
        tex_box.label(text="贴图")
        tex_box.prop(context.scene, "mdx_texture_root", text="")
        tex_box.operator("wm.mdx_reload_textures", text="重新加载贴图")

        meta_raw = context.scene.get("mdx_meta")
        if meta_raw:
            meta = json.loads(meta_raw)
            box = layout.box()
            box.label(text=f"模型：{meta.get('model_name') or '(未命名)'}")
            box.label(text=f"版本：{meta.get('format_version')}  "
                           f"geoset：{meta.get('geoset_count')}  "
                           f"节点：{meta.get('node_count')}")
            if meta.get("bone_count"):
                box.label(text=f"骨骼：{meta['bone_count']}  "
                               f"关键帧：{meta.get('animation_keys', 0):,}")

            from . import state as statemod

            counts = statemod.counts(statemod.load())
            if counts["textures"] or counts["layers"]:
                box.label(text=f"贴图 {counts['textures']}（外部 {counts['external']}）  "
                               f"材质 {counts['materials']}／层 {counts['layers']}  "
                               f"序列 {counts['sequences']}")
            if context.scene.get("mdx_dirty"):
                box.label(text="有改动尚未导出", icon="ERROR")

            seqs = meta.get("sequences") or []
            if seqs:
                frame = context.scene.frame_current
                current = next((s for s in seqs
                                if s["start_frame"] <= frame <= s["end_frame"]), None)
                if current:
                    loop = "循环" if current["looping"] else "单次"
                    box.label(text=f"当前序列：{current['name']}（{loop}，"
                                   f"{current['start']}–{current['end']} ms）", icon="PLAY")
                box.label(text=f"共 {len(seqs)} 段，明细见「动画序列」面板")

        report_raw = context.scene.get("mdx_last_report")
        if report_raw:
            report = json.loads(report_raw)
            box = layout.box()
            ok = report.get("verified") and not report.get("problems")
            box.label(text="上次导出：" + ("通过" if ok else "有问题"),
                      icon="CHECKMARK" if ok else "ERROR")
            box.label(text=f"  {report.get('mode', '')}")
            if report.get("byte_identical"):
                box.label(text="  与原始文件逐字节相同")
            else:
                box.label(text=f"  变化 {report.get('changed_bytes', 0)} 字节")
            if report.get("problems"):
                for prob in report["problems"][:3]:
                    box.label(text=f"  ! {prob}", icon="ERROR")
            box.operator("wm.mdx_show_report", text="打印完整报告")


class MDX_OT_show_report(bpy.types.Operator):
    bl_idname = "wm.mdx_show_report"
    bl_label = "导出报告"

    def execute(self, context):
        text = context.scene.get("mdx_last_report", "{}")
        print("=== MDX 导出报告 ===")
        print(text)
        self.report({"INFO"}, "报告已打印到系统控制台")
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(MDX_OT_import.bl_idname, text="Warcraft III MDX (.mdx)")


def menu_export(self, context):
    self.layout.operator(MDX_OT_export.bl_idname, text="Warcraft III MDX (.mdx)")


CLASSES = (MDX_OT_import, MDX_OT_export, MDX_OT_show_report,
           MDX_OT_import_casc, MDX_OT_search_casc,
           MDX_OT_reload_textures, MDX_PT_panel) \
    + ui_edit.CLASSES + ui_binding.CLASSES + ui_batch.CLASSES


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    ui_edit.register_props()
    bpy.types.Scene.mdx_texture_root = StringProperty(
        name="贴图目录",
        description="导出的贴图所在目录（Reforged 一般是 dds/tif）",
        subtype="DIR_PATH",
    )
    bpy.types.Scene.mdx_casc_install = StringProperty(
        name="游戏安装目录",
        description="WC3 Reforged 安装目录（含 .build.info 的那一层）",
        subtype="DIR_PATH",
    )
    bpy.types.Scene.mdx_casc_path = StringProperty(
        name="游戏内模型路径",
        description="如 units\\humanfootman\\footman.mdx",
    )

    # 把上次用过的目录灌进默认值：scene 属性是空的就从配置文件取
    try:
        if not bpy.context.scene.mdx_casc_install:
            bpy.context.scene.mdx_casc_install = settings.get("casc_install", "")
        if not bpy.context.scene.mdx_casc_path:
            bpy.context.scene.mdx_casc_path = settings.get("casc_path", "")
        if not bpy.context.scene.mdx_texture_root:
            bpy.context.scene.mdx_texture_root = settings.get("texture_root", "")
    except Exception:                              # noqa: BLE001
        pass
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    ui_edit.unregister_props()
    for attr in ("mdx_texture_root", "mdx_casc_install", "mdx_casc_path"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)


if __name__ == "__main__":
    register()
