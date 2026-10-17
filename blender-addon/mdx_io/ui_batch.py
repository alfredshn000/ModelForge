"""批量操作：对整个目录的 mdx 批量改贴图路径 / 解绑动画 / 体检。

和单文件面板的根本区别：**这里不碰当前打开的场景**，纯内核读写。
每个文件单独走一遍「操作 → 校验 → 写出」，任何一个失败就跳过它继续下一个，
失败原因全部记下来——批量最怕的就是"跑完了不知道哪个坏了"。

默认**不覆盖原文件**，输出到单独的目录并保持相对结构。
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

REPORT_KEY = "mdx_batch_report"


def _collect(folder: Path, recursive: bool) -> list[Path]:
    if not folder.is_dir():
        return []
    pattern = "**/*.mdx" if recursive else "*.mdx"
    return sorted(p for p in folder.glob(pattern) if p.is_file())


def _write_report(rows: list[dict], summary: str) -> None:
    bpy.context.scene[REPORT_KEY] = json.dumps(
        {"rows": rows[:200], "summary": summary, "total": len(rows)},
        ensure_ascii=False)


def _copy_to(src: Path, out_dir: Path, base: Path) -> Path:
    rel = src.relative_to(base)
    dst = out_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


class MDX_OT_batch_textures(bpy.types.Operator):
    bl_idname = "mdx.batch_textures"
    bl_label = "批量改外部贴图路径"
    bl_description = "目录内每个 mdx 都做一次路径改写；只改判定为外部的条目"

    folder: StringProperty(name="源目录", subtype="DIR_PATH")
    out_dir: StringProperty(name="输出目录", subtype="DIR_PATH")
    recursive: BoolProperty(name="含子目录", default=True)
    mode: EnumProperty(name="方式", items=[
        ("PREFIX", "统一前缀", "改成 前缀\\文件名"),
        ("REPLACE", "查找替换", "把路径里的某段字面换掉"),
    ])
    prefix: StringProperty(name="新前缀", default="war3mapImported\\")
    flatten: BoolProperty(name="压平目录", default=True)
    find: StringProperty(name="查找")
    repl: StringProperty(name="替换为")

    def invoke(self, context, _event):
        if not self.folder:
            self.folder = context.scene.get("mdx_batch_src", "")
        if not self.out_dir:
            self.out_dir = context.scene.get("mdx_batch_out", "")
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "folder")
        col.prop(self, "out_dir")
        col.prop(self, "recursive")
        col.prop(self, "mode", expand=True)
        if self.mode == "PREFIX":
            col.prop(self, "prefix")
            col.prop(self, "flatten")
        else:
            col.prop(self, "find")
            col.prop(self, "repl")
        col.label(text="不覆盖原文件；每个文件仍走完整校验，失败即跳过", icon="INFO")

    def execute(self, context):
        from mdxcore import load, paths as pathmod, rebuild
        from mdxcore.ops import list_textures, rename_textures_bulk, texture_spans_bulk
        from mdxcore.verify import check_delta

        src_dir = Path(self.folder)
        out_dir = Path(self.out_dir) if self.out_dir else src_dir / "_贴图路径改写"
        files = _collect(src_dir, self.recursive)
        if not files:
            self.report({"ERROR"}, f"{src_dir} 里没有 mdx")
            return {"CANCELLED"}

        rule = (pathmod.Rule(mode="prefix", prefix=self.prefix, flatten=self.flatten)
                if self.mode == "PREFIX"
                else pathmod.Rule(mode="replace", find=self.find, repl=self.repl))
        rows = []
        changed_files = changed_entries = 0

        for src in files:
            try:
                mdx = load(src)
                texs = list_textures(mdx)
                mapping = pathmod.apply_rule(texs, rule)
                if not mapping:
                    rows.append({"file": src.name, "status": "跳过", "detail": "没有外部贴图"})
                    continue
                before = src.read_bytes()
                replacements = rename_textures_bulk(mdx, mapping)
                after = rebuild(mdx, replacements)
                spans = texture_spans_bulk(mdx, mapping.keys())
                problems = check_delta(before, after, mdx, spans)
                if problems:
                    rows.append({"file": src.name, "status": "失败",
                                 "detail": "；".join(problems[:2])})
                    continue
                dst = _copy_to(src, out_dir, src_dir)
                dst.write_bytes(after)
                changed_files += 1
                changed_entries += len(mapping)
                rows.append({"file": src.name, "status": "改好",
                             "detail": f"{len(mapping)} 条 → {dst.name}"})
            except Exception as exc:               # noqa: BLE001
                rows.append({"file": src.name, "status": "失败", "detail": str(exc)[:120]})

        context.scene["mdx_batch_src"] = str(src_dir)
        context.scene["mdx_batch_out"] = str(out_dir)
        _write_report(rows, f"{changed_files} 个文件 / {changed_entries} 条路径改好，"
                            f"输出到 {out_dir}")
        self.report({"INFO"}, f"{changed_files} 个文件改了 {changed_entries} 条贴图路径")
        return {"FINISHED"}


class MDX_OT_batch_verify(bpy.types.Operator):
    bl_idname = "mdx.batch_verify"
    bl_label = "批量体检"
    bl_description = "目录内每个 mdx 都跑一遍「解析 → 重建 → 逐字节比对」，不改任何文件"

    folder: StringProperty(name="源目录", subtype="DIR_PATH")
    recursive: BoolProperty(name="含子目录", default=True)

    def invoke(self, context, _event):
        if not self.folder:
            self.folder = context.scene.get("mdx_batch_src", "")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "folder")
        col.prop(self, "recursive")
        col.label(text="只读，不写任何文件", icon="INFO")

    def execute(self, context):
        from mdxcore import load, rebuild

        src_dir = Path(self.folder)
        files = _collect(src_dir, self.recursive)
        if not files:
            self.report({"ERROR"}, f"{src_dir} 里没有 mdx")
            return {"CANCELLED"}

        rows = []
        ok = 0
        for src in files:
            try:
                mdx = load(src)
                same = rebuild(mdx, {}) == mdx.raw
                if same:
                    ok += 1
                    rows.append({"file": src.name, "status": "一致",
                                 "detail": f"{len(mdx.chunks)} chunk / {len(mdx.raw):,} B"})
                else:
                    rows.append({"file": src.name, "status": "不一致",
                                 "detail": "重建后字节有差异（解析器漏字段）"})
            except Exception as exc:               # noqa: BLE001
                rows.append({"file": src.name, "status": "失败", "detail": str(exc)[:120]})

        _write_report(rows, f"{ok}/{len(files)} 个文件「解析→重建」逐字节一致")
        self.report({"INFO"}, f"{ok}/{len(files)} 通过")
        return {"FINISHED"}


class MDX_OT_batch_unbind(bpy.types.Operator):
    bl_idname = "mdx.batch_unbind"
    bl_label = "批量解绑动画"
    bl_description = "目录内每个 mdx 都清空内嵌动画（骨骼保留）"

    folder: StringProperty(name="源目录", subtype="DIR_PATH")
    out_dir: StringProperty(name="输出目录", subtype="DIR_PATH")
    recursive: BoolProperty(name="含子目录", default=True)

    def invoke(self, context, _event):
        if not self.folder:
            self.folder = context.scene.get("mdx_batch_src", "")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "folder")
        col.prop(self, "out_dir")
        col.prop(self, "recursive")
        col.label(text="不覆盖原文件；骨骼与蒙皮不动，只清关键帧", icon="INFO")

    def execute(self, context):
        from mdxcore import load, rebuild, transfer
        from mdxcore.verify import check_chunk_rewrite

        src_dir = Path(self.folder)
        out_dir = Path(self.out_dir) if self.out_dir else src_dir / "_解绑动画"
        files = _collect(src_dir, self.recursive)
        if not files:
            self.report({"ERROR"}, f"{src_dir} 里没有 mdx")
            return {"CANCELLED"}

        rows = []
        done = 0
        for src in files:
            try:
                mdx = load(src)
                replacements, stats = transfer.clear_animation(mdx)
                before = src.read_bytes()
                after = rebuild(mdx, replacements)
                problems = check_chunk_rewrite(before, after, mdx,
                                              transfer.allowed_tags(replacements, mdx))
                if problems:
                    rows.append({"file": src.name, "status": "失败",
                                 "detail": "；".join(problems[:2])})
                    continue
                dst = _copy_to(src, out_dir, src_dir)
                dst.write_bytes(after)
                done += 1
                rows.append({"file": src.name, "status": "解绑",
                             "detail": f"{stats['cleared_keys']:,} 键 / "
                                       f"{stats['cleared_bones']} 骨"})
            except ValueError as exc:
                rows.append({"file": src.name, "status": "跳过", "detail": str(exc)[:100]})
            except Exception as exc:               # noqa: BLE001
                rows.append({"file": src.name, "status": "失败", "detail": str(exc)[:120]})

        _write_report(rows, f"{done}/{len(files)} 个文件已解绑，输出到 {out_dir}")
        self.report({"INFO"}, f"{done} 个文件已解绑")
        return {"FINISHED"}


class MDX_PT_batch(bpy.types.Panel):
    bl_label = "批量操作"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MDX"
    bl_parent_id = "MDX_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("mdx.batch_verify", text="批量体检（只读）…", icon="CHECKMARK")
        col.operator("mdx.batch_textures", text="批量改外部贴图路径…", icon="FILE_REFRESH")
        col.operator("mdx.batch_unbind", text="批量解绑动画…", icon="UNLINKED")

        raw = context.scene.get(REPORT_KEY)
        if not raw:
            layout.label(text="不碰当前场景，只处理目录；默认不覆盖原文件", icon="INFO")
            return
        report = json.loads(raw)
        box = layout.box()
        box.label(text=report.get("summary", ""), icon="INFO")

        counts: dict[str, int] = {}
        for row in report.get("rows") or []:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        box.label(text="　".join(f"{k} {v}" for k, v in counts.items()))

        for row in (report.get("rows") or [])[:12]:
            icon = {"一致": "CHECKMARK", "改好": "CHECKMARK", "解绑": "CHECKMARK",
                    "跳过": "INFO", "失败": "ERROR", "不一致": "ERROR"}.get(
                row["status"], "DOT")
            box.label(text=f"{row['status']}  {row['file'][:26]}  {row['detail'][:26]}",
                      icon=icon)
        if report.get("total", 0) > 12:
            box.label(text=f"…共 {report['total']} 个文件，详见控制台")
            box.operator("mdx.batch_print", text="打印完整结果")


class MDX_OT_batch_print(bpy.types.Operator):
    bl_idname = "mdx.batch_print"
    bl_label = "打印批量结果"

    def execute(self, context):
        raw = context.scene.get(REPORT_KEY) or "{}"
        print("=== 批量操作结果 ===")
        print(raw)
        self.report({"INFO"}, "已打印到系统控制台")
        return {"FINISHED"}


CLASSES = (MDX_OT_batch_textures, MDX_OT_batch_verify,
           MDX_OT_batch_unbind, MDX_OT_batch_print, MDX_PT_batch)
