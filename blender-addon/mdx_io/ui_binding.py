"""动画绑定：把别的模型的动画换到当前模型上，或者把动画整个解绑。

用户原话：「某个模型绑定了 footman 的动画，可以把它改成绑定 militia 的动画」。
Retera 完全没有这个能力——它只能整模型打开、整模型保存，动画是模型的一部分，
没有"换一套动画"这个概念。

**这是唯一一个会直接改写磁盘文件的界面操作**，原因不是偷懒：

动画轨道（BONE）和序列表（SEQS）都是结构会变的块。如果只改场景状态，
下一次导出会拿 Blender 里那条旧 Action 去比对，把刚搬进来的动画又覆盖回去。
所以这里走的是「写盘 → 重新导入」：文件立刻变成新的真相，场景跟着重建。

代价是「未导出的其他改动会丢失」，所以对话框里写得很显式，并且自动备份原件。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import state as statemod
from .bridge import get_core

PLAN_KEY = "mdx_binding_plan"
RECORD_KEY = "mdx_binding"

TRANSLATION_ITEMS = [
    ("copy", "照搬（含位移轨道）", "完全照源模型搬，忠实但两模型比例不同时位移会错位"),
    ("skip", "跳过位移轨道", "只搬旋转与缩放；位移是相对父节点的绝对值，跨模型最不可靠"),
    ("scale", "位移按比例缩放", "搬位移但乘以一个比例系数，适合两模型尺寸差固定倍数"),
]


def _plan_key(mode: str, path: str, translation: str, seqs: bool) -> str:
    return f"{mode}|{path}|{int(seqs)}|{translation}"


def _load_source(mode: str, file: str, install: str, casc_path: str):
    """读出源模型的 MdxFile。失败抛异常并带人话。"""
    core = get_core()
    if mode == "CASC":
        from .casc_source import CascSourceError, read_model

        if not install or not casc_path:
            raise ValueError("请填写游戏安装目录与模型路径")
        try:
            return core.parse(read_model(Path(install), casc_path))
        except CascSourceError as exc:
            raise ValueError(str(exc)) from None
    if not file:
        raise ValueError("请选择源模型文件")
    src = Path(file)
    if not src.is_file():
        raise ValueError(f"文件不存在：{src}")
    return core.load(src)


def _target_path() -> Path:
    meta = bpy.context.scene.get("mdx_meta")
    if not meta:
        raise ValueError("当前场景没有 MDX 模型，请先导入")
    path = Path(json.loads(meta)["source_path"])
    if not path.is_file():
        raise ValueError(f"原始文件不存在：{path}")
    return path


# ---------- 检查匹配 ----------

class MDX_OT_check_binding(bpy.types.Operator):
    bl_idname = "mdx.check_binding"
    bl_label = "检查匹配"
    bl_description = "先算一遍能对上多少根骨骼，不写文件"

    mode: EnumProperty(items=[("FILE", "本地文件", ""), ("CASC", "游戏数据", "")])
    file: StringProperty(subtype="FILE_PATH")
    install: StringProperty(subtype="DIR_PATH")
    casc_path: StringProperty()
    translation: EnumProperty(items=TRANSLATION_ITEMS)

    def execute(self, context):
        from mdxcore import transfer

        try:
            target = get_core().load(_target_path())
            source = _load_source(self.mode, self.file, self.install, self.casc_path)
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}
        try:
            plan = transfer.measure(target, source,
                                    translation=self.translation)
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, f"匹配失败：{exc}"[:200])
            return {"CANCELLED"}

        plan["key"] = _plan_key(self.mode, self.file or self.casc_path,
                                self.translation, True)
        plan["source_label"] = (Path(self.file).name if self.mode == "FILE"
                                else self.casc_path)
        context.scene[PLAN_KEY] = json.dumps(plan, ensure_ascii=False)
        self.report({"INFO"}, f"匹配 {plan['matched']} / {plan['target_bones']} 根骨骼"
                              f"（{plan['coverage']:.0%}）")
        return {"FINISHED"}


# ---------- 绑定 ----------

class MDX_OT_bind_animation(bpy.types.Operator):
    bl_idname = "mdx.bind_animation"
    bl_label = "绑定到其他模型的动画"
    bl_description = "用另一个模型的动画替换本模型的动画（会改写文件并重新导入）"

    mode: EnumProperty(name="来源", items=[("FILE", "本地文件", "选一个 .mdx"),
                                          ("CASC", "游戏数据", "从 WC3 Reforged 里读")])
    file: StringProperty(name="模型文件", subtype="FILE_PATH")
    install: StringProperty(name="安装目录", subtype="DIR_PATH",
                            description="WC3 安装目录（含 .build.info 的那一层）")
    casc_path: StringProperty(name="游戏内路径",
                              description=r"如 units\human\militia\militia.mdx")
    translation: EnumProperty(name="位移轨道", items=TRANSLATION_ITEMS, default="copy")
    scale_translation: FloatProperty(name="位移比例", default=1.0, min=0.001, max=100.0)
    replace_sequences: BoolProperty(
        name="同时替换动画序列", default=True,
        description="段名与时间区间也换成源模型的——不换的话动画会与 Stand/Walk 这些段错位")
    keep_target_tracks: BoolProperty(
        name="目标独有骨骼保留原动画", default=True,
        description="关掉的话，源模型没有的骨骼会被清空动画（模型会僵住）")

    def invoke(self, context, _event):
        if not context.scene.get("mdx_meta"):
            self.report({"ERROR"}, "请先导入模型")
            return {"CANCELLED"}
        if not self.install:
            self.install = context.scene.get("mdx_casc_install", "")
        if not self.casc_path:
            self.casc_path = "units\\human\\footman\\footman.mdx"
        if not self.file:
            self.file = context.scene.get(MDX_OT_bind_animation.bl_idname + "_last", "")
        return context.window_manager.invoke_props_dialog(self, width=580)

    def draw(self, context):
        layout = self.layout
        meta = json.loads(context.scene.get("mdx_meta") or "{}")
        target = Path(meta.get("source_path", "")).name

        warn = layout.box()
        warn.label(text=f"会改写 {target}（自动备份为 {target}.bak）并重新导入",
                   icon="ERROR")
        warn.label(text="未导出的其它改动会丢失", icon="INFO")

        layout.prop(self, "mode", expand=True)
        box = layout.box()
        if self.mode == "FILE":
            box.prop(self, "file", text="")
        else:
            box.prop(self, "install", text="安装目录")
            box.prop(self, "casc_path", text="模型路径")
            box.label(text=r"例：units\human\militia\militia.mdx", icon="INFO")

        layout.prop(self, "translation", text="位移")
        if self.translation == "scale":
            layout.prop(self, "scale_translation")
        layout.prop(self, "replace_sequences")
        layout.prop(self, "keep_target_tracks")

        # 缓存的匹配结果：只有参数没变才展示，避免拿旧结论骗人
        raw = context.scene.get(PLAN_KEY)
        key = _plan_key(self.mode, self.file or self.casc_path, self.translation, True)
        plan = json.loads(raw) if raw else None
        box = layout.box()
        if not plan or plan.get("key") != key:
            box.label(text="还没检查过匹配情况", icon="INFO")
            op = box.operator("mdx.check_binding", text="检查匹配（不写文件）",
                              icon="VIEWZOOM")
            op.mode = self.mode
            op.file = self.file
            op.install = self.install
            op.casc_path = self.casc_path
            op.translation = self.translation
        else:
            ratio = plan["coverage"]
            icon = "CHECKMARK" if ratio >= 0.8 else "ERROR"
            box.label(text=f"匹配 {plan['matched']} / {plan['target_bones']} 根骨骼"
                           f"（{ratio:.0%}）", icon=icon)
            how = plan.get("by_how") or {}
            if how:
                box.label(text="  " + "，".join(f"{k} {v}" for k, v in how.items()))
            box.label(text=f"待搬 {plan['incoming_keys']:,} 个关键帧；"
                           f"非零位移 {plan['nonzero_translation_keys']:,} 条")
            box.label(text=f"源 {plan['source_bones']} 骨 / {plan['source_sequences']} 段，"
                           f"本模型 {plan['target_sequences']} 段")
            if plan.get("target_only_count"):
                box.label(text=f"本模型独有骨骼 {plan['target_only_count']} 根")
            if plan.get("note"):
                for line in plan["note"].split("；"):
                    box.label(text=line, icon="ERROR")

    def execute(self, context):
        from mdxcore import rebuild, transfer
        from mdxcore.verify import check_chunk_rewrite

        try:
            dst = _target_path()
            target = get_core().load(dst)
            source = _load_source(self.mode, self.file, self.install, self.casc_path)
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        try:
            replacements, stats = transfer.apply(
                target, source,
                translation=self.translation,
                scale_translation=self.scale_translation,
                replace_sequences=self.replace_sequences,
                keep_target_tracks=self.keep_target_tracks)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        before = dst.read_bytes()
        after = rebuild(target, replacements)
        problems = check_chunk_rewrite(before, after, target,
                                       transfer.allowed_tags(replacements, target))
        if problems:
            for p in problems[:3]:
                self.report({"ERROR"}, p)
            return {"CANCELLED"}

        # 备份：只留一份"绑定前"，避免把工作区塞满
        backup = dst.with_suffix(dst.suffix + ".bak")
        try:
            backup.write_bytes(before)
        except OSError as exc:
            self.report({"ERROR"}, f"备份失败，未改写：{exc}")
            return {"CANCELLED"}

        dst.write_bytes(after)

        # 重新导入：场景必须跟着新字节重建，否则下次导出会用旧 Action 覆盖回去
        from .importer import import_mdx

        try:
            import_mdx(str(dst))
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, f"已写入 {dst.name} 但重新导入失败：{exc}"
                                   f"（备份在 {backup.name}）")
            return {"CANCELLED"}

        label = (Path(self.file).name if self.mode == "FILE" else self.casc_path)
        context.scene[RECORD_KEY] = json.dumps({
            "source": label,
            "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "matched": stats["plan"]["matched"],
            "coverage": stats["plan"]["coverage"],
            "keys": stats["keys"],
            "bones": stats["replaced_bones"],
            "sequences": stats.get("sequences", 0),
        }, ensure_ascii=False)
        context.scene[MDX_OT_bind_animation.bl_idname + "_last"] = self.file
        context.scene["mdx_dirty"] = False

        self.report({"INFO"},
                    f"已绑定 {label}：{stats['replaced_bones']} 根骨骼 / "
                    f"{stats['keys']:,} 个关键帧（备份 {backup.name}）")
        return {"FINISHED"}


# ---------- 解绑 ----------

class MDX_OT_unbind_animation(bpy.types.Operator):
    bl_idname = "mdx.unbind_animation"
    bl_label = "解绑本模型动画"
    bl_description = "清空模型内嵌的全部动画轨道（骨骼保留，蒙皮不受影响）"

    drop_sequences: BoolProperty(
        name="连动画序列一起清掉", default=False,
        description="会留下一个空的 SEQS 块；只在确定要完全重做动画时勾")

    def invoke(self, context, _event):
        if not context.scene.get("mdx_meta"):
            self.report({"ERROR"}, "请先导入模型")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        meta = json.loads(context.scene.get("mdx_meta") or "{}")
        target = Path(meta.get("source_path", "")).name
        box = layout.box()
        box.label(text=f"会改写 {target}（自动备份为 {target}.bak）并重新导入",
                  icon="ERROR")
        box.label(text=f"骨骼 {meta.get('bone_count', 0)} 根会保留，只清关键帧",
                  icon="INFO")
        layout.prop(self, "drop_sequences")

    def execute(self, context):
        from mdxcore import rebuild, transfer
        from mdxcore.verify import check_chunk_rewrite

        try:
            dst = _target_path()
            target = get_core().load(dst)
            replacements, stats = transfer.clear_animation(
                target, drop_sequences=self.drop_sequences)
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc)[:200])
            return {"CANCELLED"}

        before = dst.read_bytes()
        after = rebuild(target, replacements)
        problems = check_chunk_rewrite(before, after, target,
                                       transfer.allowed_tags(replacements, target))
        if problems:
            for p in problems[:3]:
                self.report({"ERROR"}, p)
            return {"CANCELLED"}

        backup = dst.with_suffix(dst.suffix + ".bak")
        try:
            backup.write_bytes(before)
        except OSError as exc:
            self.report({"ERROR"}, f"备份失败，未改写：{exc}")
            return {"CANCELLED"}
        dst.write_bytes(after)

        from .importer import import_mdx

        try:
            import_mdx(str(dst))
        except Exception as exc:                   # noqa: BLE001
            self.report({"ERROR"}, f"已写入但重新导入失败：{exc}")
            return {"CANCELLED"}

        context.scene[RECORD_KEY] = json.dumps({
            "source": "（已解绑）",
            "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "keys": 0, "bones": 0, "matched": 0, "coverage": 0.0,
        }, ensure_ascii=False)
        context.scene["mdx_dirty"] = False
        self.report({"INFO"}, f"已清空 {stats['cleared_bones']} 根骨骼的 "
                              f"{stats['cleared_keys']:,} 个关键帧")
        return {"FINISHED"}


# ---------- 面板 ----------

class MDX_PT_binding(bpy.types.Panel):
    bl_label = "动画绑定"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        meta_raw = context.scene.get("mdx_meta")
        if not meta_raw:
            layout.label(text="请先导入模型", icon="INFO")
            return
        meta = json.loads(meta_raw)
        st = statemod.load()

        box = layout.box()
        box.label(text=f"内嵌动画：{meta.get('bone_count', 0)} 骨 / "
                       f"{meta.get('animation_keys', 0):,} 键 / "
                       f"{len(st.get('sequences') or [])} 段")

        rec_raw = context.scene.get(RECORD_KEY)
        if rec_raw:
            rec = json.loads(rec_raw)
            box = layout.box()
            box.label(text=f"上次操作：{rec.get('source', '')}", icon="LINKED")
            box.label(text=f"  {rec.get('when', '')}　"
                           f"{rec.get('bones', 0)} 骨 / {rec.get('keys', 0):,} 键")
            if rec.get("coverage"):
                box.label(text=f"  骨骼匹配率 {rec['coverage']:.0%}")
        else:
            layout.label(text="动画来源：模型自带", icon="INFO")

        col = layout.column(align=True)
        col.operator("mdx.bind_animation", text="绑定到其他模型的动画…", icon="LINKED")
        col.operator("mdx.unbind_animation", text="解绑本模型动画…", icon="UNLINKED")

        layout.label(text="会直接改写文件，改写前自动备份", icon="INFO")


CLASSES = (MDX_OT_check_binding, MDX_OT_bind_animation,
           MDX_OT_unbind_animation, MDX_PT_binding)
