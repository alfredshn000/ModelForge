"""MDX 导入：GEOS → 网格、BONE → Armature、SKIN/矩阵组 → 顶点权重。

v1 的取舍（先保证"读对"，再谈"写回"）：
- 几何：顶点 / 三角面 / 第一套 UV
- 骨骼：按 BONE 层级建 Armature，骨头头部 = 轴心点（与 WC3 变换原点一致）
- 蒙皮：SKIN 子块（Reforged）或矩阵组（legacy）→ 顶点组权重
- 法线：不导入（Blender 4+/5.x 自定义法线 API 变动大，留到后续）
- 材质：每 geoset 一个材质槽，贴图路径写进自定义属性
- 元数据：整份 JSON 存在场景上，导出时用来定位原文件
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from .bridge import get_core


def _clear_previous():
    coll = bpy.data.collections.get("MDX")
    if coll:
        for obj in list(coll.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(coll)

    # 上一条动作不清掉的话，新建同名 Action 会变成 MDX_Main.001，
    # 而按名字取到的仍是旧的那条（跨模型批量导入时会串）。
    for action in list(bpy.data.actions):
        if action.name.startswith("MDX_"):
            bpy.data.actions.remove(action)

    # 换模型了，上一个模型的绑定记录与匹配结果就不再有意义
    for key in ("mdx_binding", "mdx_binding_plan"):
        if key in bpy.context.scene:
            del bpy.context.scene[key]


def _apply_sequence_markers(mdx, fps: float) -> list[dict]:
    """把 SEQS 的每个动画序列标到时间轴上。

    WC3 的所有动作共用一条时间轴（Stand 在 0–2000，Attack 在 7867–9100 …），
    所以用 Blender 的 timeline marker 表示最直观：点一下就跳到对应动作起点。
    """
    try:
        from mdxcore.ops import list_sequences
        seqs = list_sequences(mdx)
    except Exception:                              # noqa: BLE001
        return []

    scene = bpy.context.scene
    for marker in list(scene.timeline_markers):
        scene.timeline_markers.remove(marker)

    info: list[dict] = []
    end_frame = 0
    for s in seqs:
        start = s.start / 1000.0 * fps
        end = s.end / 1000.0 * fps
        end_frame = max(end_frame, int(round(end)))
        scene.timeline_markers.new(f"{s.name}", frame=int(round(start)))
        info.append({"name": s.name, "start": s.start, "end": s.end,
                     "start_frame": round(start, 3), "end_frame": round(end, 3),
                     "looping": not s.non_looping})

    if info:
        scene.frame_start = 0
        scene.frame_end = end_frame
    return info


def _per_vertex_normals(mesh):
    """从 mesh 的角法线取每个顶点的法线（取该顶点第一个角）。"""
    out = [None] * len(mesh.vertices)
    for loop in mesh.loops:
        vi = loop.vertex_index
        if out[vi] is None:
            out[vi] = tuple(mesh.corner_normals[loop.index].vector)
    return out


def _uv_flip(v: float) -> float:
    """WC3 的 V 轴与 Blender 相反，显示时翻转（写回时再翻回来）。"""
    return 1.0 - v


def _safe_name(raw: str, fallback: str) -> str:
    """WC3 里的名字是 latin-1 字节，可能不是合法 UTF-8；Blender 要求 UTF-8。

    这里把它转成可打印 ASCII，非 ASCII 字符替换成下划线。
    """
    out = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in raw).strip()
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or fallback


def bone_names(bones) -> dict[int, str]:
    """文件里的骨骼序号 → Blender 中实际使用的名字（ASCII 安全、唯一）。"""
    used: set[str] = set()
    out: dict[int, str] = {}
    for b in bones:
        base = _safe_name(b.name, f"bone_{b.index}")
        name = base
        k = 2
        while name in used:
            name = f"{base}_{k}"
            k += 1
        used.add(name)
        out[b.index] = name
    return out


def _build_armature(coll, bones, points) -> tuple[bpy.types.Object | None, dict[int, str]]:
    """按 BONE 层级建骨架，返回 (骨架对象, 节点序号→骨骼名)。"""
    if not bones:
        return None, {}

    from mdxcore import tangents
    from mdxcore.chunks import bone as bonemod

    arm_data = bpy.data.armatures.new("MDX_Armature")
    arm = bpy.data.objects.new("MDX_Armature", arm_data)
    coll.objects.link(arm)

    view_layer = bpy.context.view_layer
    view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    # 先算好唯一且 ASCII 安全的名字，避免 Blender 内部按 UTF-8 解码炸掉
    final_names = bone_names(bones)

    edit_bones = {}
    for b in bones:
        pivot = points[b.index].xyz if b.index < len(points) else (0.0, 0.0, 0.0)
        eb = arm_data.edit_bones.new(final_names[b.index])
        eb.head = pivot
        # 让骨骼指向 +Y：Blender 骨骼的局部 Y 轴即骨向，
        # WC3 节点在静止姿态下局部轴与世界轴同向（Z-up / X-right / Y-forward），
        # 这样节点的位移/旋转可以按分量直接映射，不用做轴向换算。
        eb.tail = (pivot[0], pivot[1] + 5.0, pivot[2])
        eb.use_connect = False
        edit_bones[b.index] = eb

    for b in bones:
        parent = bonemod.parent_of(bones, b)
        if parent is not None:
            edit_bones[b.index].parent = edit_bones[parent.index]

    bpy.ops.object.mode_set(mode="OBJECT")

    # 记住「骨骼序号 → Blender 骨骼名」：轴心点面板改 PIVT 时要靠它把骨骼头也挪过去，
    # 而 Blender 的 bones 集合顺序不可依赖，只能显式存一份。
    import json as _json
    arm["mdx_bone_names"] = _json.dumps({str(k): v for k, v in final_names.items()})

    return arm, final_names


def _build_action(arm, bones, bone_data, name_by_index, fps: float):
    """把骨骼动画轨道写成一条 Action 的关键帧。

    WC3 的时间单位是毫秒，这里按场景 fps 换算成 Blender 帧（可为小数）。
    旋转四元数 WC3 是 (x,y,z,w)，Blender 是 (w,x,y,z)。
    """
    import struct as _struct

    from mdxcore import tangents
    from mdxcore.chunks import bone as bonemod

    total = 0
    for b in bones:
        tracks = bonemod.parse_tracks(bone_data, b)
        total += sum(len(af.tracks) for af in tracks.values())
    if not total:
        return None, 0

    from .bpy_compat import bind_action, ensure_fcurve_container

    action = bpy.data.actions.new("MDX_Main")
    fcurves, slot = ensure_fcurve_container(action, arm)
    bind_action(arm, action, slot)

    CHANNELS = {
        "KGTR": ("location", (0, 1, 2)),
        "KGRT": ("rotation_quaternion", (1, 2, 3, 0)),   # x,y,z,w → w,x,y,z
        "KGSC": ("scale", (0, 1, 2)),
    }
    INTERP = {"DontInterp": 0, "Linear": 1, "Hermite": 2, "Bezier": 2}
    written = 0

    for b in bones:
        tracks = bonemod.parse_tracks(bone_data, b)
        if not tracks:
            continue
        bone_name = name_by_index[b.index]
        path_base = f'pose.bones["{bone_name}"]'
        for tag, af in tracks.items():
            if tag not in CHANNELS or not af.tracks:
                continue
            prop, order = CHANNELS[tag]
            frames = [t.time / 1000.0 * fps for t in af.tracks]
            interp = INTERP.get(af.interp_name, 1)
            n = len(frames)
            for comp_slot, comp in enumerate(order):
                values = [t.value[comp] for t in af.tracks]
                fc = fcurves.new(data_path=f"{path_base}.{prop}", index=comp_slot)
                fc.keyframe_points.add(n)

                flat = []
                for f, v in zip(frames, values):
                    flat.extend((f, v))
                fc.keyframe_points.foreach_set("co", flat)
                fc.keyframe_points.foreach_set("interpolation", [interp] * n)

                if af.has_tangents:
                    # WC3 的切线是「值/毫秒」的导数，Blender 用曲线上的控制点表示。
                    # 按 1/3 间距摆放时，三次贝塞尔与三次 Hermite 逐点等价，
                    # 所以这里是精确转换，不是拟合。
                    lefts, rights = [], []
                    for i, t in enumerate(af.tracks):
                        left, right = tangents.to_handles(
                            frames, i, values[i],
                            t.in_tan[comp], t.out_tan[comp], fps)
                        lefts.extend(left)
                        rights.extend(right)
                    fc.keyframe_points.foreach_set("handle_left", lefts)
                    fc.keyframe_points.foreach_set("handle_right", rights)
                    # 默认是 AUTO_CLAMPED：不改成 FREE 的话 Blender 会自己重算手柄，
                    # 我们算出来的切线会被丢掉。
                    fc.keyframe_points.foreach_set("handle_left_type", [0] * n)
                    fc.keyframe_points.foreach_set("handle_right_type", [0] * n)

                fc.update()
                written += n
    return action, written


def _assign_weights(obj, geoset, name_by_index, stats: dict) -> None:
    """把蒙皮权重灌进顶点组。按 (组, 量化权重) 批量写，避免逐顶点调用。"""
    from mdxcore import skin

    infos = skin.influences(geoset)
    if not infos:
        return

    buckets: dict[tuple[str, int], list[int]] = {}
    for vi, infl in enumerate(infos):
        for one in infl:
            name = name_by_index.get(one.node_index)
            if not name:
                stats["skipped_influences"] = stats.get("skipped_influences", 0) + 1
                continue
            key = (name, round(one.weight * 255))
            buckets.setdefault(key, []).append(vi)

    groups: dict[str, bpy.types.VertexGroup] = {}
    for (name, q), verts in buckets.items():
        if name not in groups:
            groups[name] = obj.vertex_groups.new(name=name)
        groups[name].add(verts, q / 255.0, "REPLACE")
    stats["vertex_groups"] = len(groups)


def _layer_diffuse_path(materials, material_id: int, tex_items):
    """取材质第一层的**漫反射**贴图路径与索引。

    1100/1200 的贴图在图层尾部的合并槽里，前缀 `textureId` 是历史字段——
    直接读前缀会拿到错路径（实测 HD 模型会全部指到 0 号贴图）。
    """
    if not (0 <= material_id < len(materials)) or not materials[material_id].layers:
        return "", -1
    layer = materials[material_id].layers[0]
    index = layer.texture_id
    if getattr(layer, "hd_slots", None):
        slot = layer.slot_of_type(0)               # 0 = Diffuse
        if slot is not None and slot.texture_id >= 0:
            index = slot.texture_id
    if 0 <= index < len(tex_items):
        return tex_items[index].path, index
    return "", index


def import_mdx(filepath: str, texture_root: str | Path | None = None,
               casc_install: str | Path | None = None) -> dict:
    """导入 MDX。

    `casc_install` 有值时，`filepath` 被当作 **CASC 内路径**
    （如 `units\\humanfootman\\footman.mdx`），会先把模型和它引用的贴图
    从游戏数据里取出来落到工作区，再走正常导入流程。
    """
    casc_info: dict | None = None
    if casc_install:
        from .casc_source import extract

        result = extract(Path(casc_install), str(filepath))
        filepath = str(result["model"])
        texture_root = result["texture_root"]
        casc_info = result["report"]

    mdxcore = get_core()
    from mdxcore import tangents
    from mdxcore.chunks import bone as bonemod
    from mdxcore.chunks import geos as geosmod
    from mdxcore.chunks import modl
    from mdxcore.ops import (format_version, list_materials, list_pivots,
                         list_sequences, list_textures)

    path = Path(filepath)
    mdx = mdxcore.load(path)
    version = format_version(mdx)

    geosets = []
    if any(c.tag == "GEOS" for c in mdx.chunks):
        geosets = geosmod.parse(mdx.data(mdx.find("GEOS")), version)

    bones = []
    if any(c.tag == "BONE" for c in mdx.chunks):
        bones = bonemod.parse(mdx.data(mdx.find("BONE")))

    try:
        tex_items = list_textures(mdx)
    except Exception:
        tex_items = []

    try:
        mats = list_materials(mdx)
    except Exception:
        mats = []

    try:
        points, slots, _ = list_pivots(mdx)
    except Exception:
        points, slots = [], []

    header = None
    try:
        header = modl.parse(mdx.data(mdx.find("MODL")))
    except Exception:
        pass

    from . import textures

    if texture_root in (None, ""):
        texture_root = bpy.context.scene.get("mdx_texture_root", "")
    texture_root = Path(texture_root) if texture_root else None
    tex_index_map = textures.build_index(texture_root) if texture_root else {}

    _clear_previous()
    coll = bpy.data.collections.new("MDX")
    bpy.context.scene.collection.children.link(coll)

    # ---- 节点空物体（便于查看非骨骼节点，如附着点/发射器） ----
    for i, point in enumerate(points):
        ref = slots[i] if i < len(slots) else None
        is_attach = bool(ref and ref.tag == "ATCH")
        if is_attach:
            # 附着点是"挂武器"要动的东西，名字里直接带上 ATCH_，
            # 视口和 Outliner 里一眼能挑出来，不用在几十个 node_ 里翻。
            empty = bpy.data.objects.new(f"ATCH_{_safe_name(ref.name, str(i))}", None)
            empty.empty_display_type = "SINGLE_ARROW"
            empty.empty_display_size = 6.0
            empty.show_name = True
        else:
            empty = bpy.data.objects.new(f"node_{i}" + (f"_{ref.name}" if ref else ""), None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 1.0
        empty.location = point.xyz
        empty["mdx_node_index"] = i
        if ref:
            empty["mdx_node_tag"] = ref.tag
        coll.objects.link(empty)

    # ---- 骨架 ----
    arm, name_by_index = _build_armature(coll, bones, points)

    # ---- 几何 ----
    stats: dict = {}
    for gi, g in enumerate(geosets):
        mesh = bpy.data.meshes.new(f"GEOS_{gi}")
        faces = [tuple(g.faces[i:i + 3]) for i in range(0, len(g.faces) - 2, 3)]
        mesh.from_pydata([tuple(v) for v in g.vertices], [], faces)
        mesh.update()

        if g.uv_sets and g.uv_sets[0]:
            uvs = g.uv_sets[0]
            layer = mesh.uv_layers.new(name="UVMap")
            for poly in mesh.polygons:
                for li in poly.loop_indices:
                    vi = mesh.loops[li].vertex_index
                    if vi < len(uvs):
                        u, v = uvs[vi]
                        layer.data[li].uv = (u, _uv_flip(v))

        # 法线：写进自定义分裂法线，保证 Blender 里的着色与游戏一致。
        # 不设的话 Blender 会按面角自己重算，导出时容易被误判成「用户改过法线」。
        if g.normals and len(g.normals) == len(g.vertices):
            try:
                mesh.normals_split_custom_set_from_vertices(
                    [tuple(n) for n in g.normals])
                # Blender 把自定义法线压成 2×int16 的球面坐标，解码误差可达 2e-3。
                # 把「解码后的值」存成基线，导出时按基线比对：
                # 没动过 → 完全相等；动过 → 一定不等。不依赖任何阈值猜测。
                baseline = _per_vertex_normals(mesh)
                if baseline:
                    mesh["mdx_normals"] = [c for n in baseline for c in n]
            except Exception:                      # noqa: BLE001
                stats["custom_normals_failed"] = stats.get("custom_normals_failed", 0) + 1

        obj = bpy.data.objects.new(f"GEOS_{gi}", mesh)
        coll.objects.link(obj)
        obj["mdx_geoset_index"] = gi
        obj["mdx_material_id"] = g.material_id

        mat = bpy.data.materials.new(f"MAT_{g.material_id}_{gi}")
        mat.use_nodes = True
        tex_path, tex_index = _layer_diffuse_path(mats, g.material_id, tex_items)
        mat["mdx_texture_path"] = tex_path
        mat["mdx_texture_index"] = tex_index
        mat["mdx_material_id"] = g.material_id
        mat["mdx_layer_index"] = 0
        mesh.materials.append(mat)

        file = textures.resolve(tex_path, texture_root, tex_index_map)
        if file is not None:
            image = textures.load_image(file)
            if image is not None and textures.attach_to_material(mat, image, "UVMap"):
                stats["textures_loaded"] = stats.get("textures_loaded", 0) + 1
                mat["mdx_texture_file"] = str(file)
            else:
                stats["textures_unreadable"] = stats.get("textures_unreadable", 0) + 1
                mat["mdx_texture_file"] = ""
        else:
            stats["textures_missing"] = stats.get("textures_missing", 0) + 1
            mat["mdx_texture_file"] = ""

        if arm and bones:
            _assign_weights(obj, g, name_by_index, stats)
            obj.parent = arm
            modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
            modifier.object = arm

    # ---- 动画 ----
    fps = float(bpy.context.scene.render.fps or 24)
    anim_action = None
    anim_keys = 0
    if arm and bones:
        bone_data = mdx.data(mdx.find("BONE"))
        anim_action, anim_keys = _build_action(arm, bones, bone_data, name_by_index, fps)

    # ---- 动画序列标记 ----
    sequences = _apply_sequence_markers(mdx, fps)

    # ---- 可编辑状态快照（材质 / 贴图 / 几何动画 / 序列） ----
    # 存进场景，导出时拿它和文件比对；没动的字段一个字节都不写。
    from . import state as statemod

    try:
        snapshot = statemod.snapshot(mdx, str(path))
        statemod.save(snapshot)
    except Exception as exc:                       # noqa: BLE001
        snapshot = {}
        stats["state_failed"] = str(exc)[:120]

    # ---- 元数据 ----
    meta = {
        "source_path": str(path),
        "format_version": version,
        "model_name": header.name if header else "",
        "texture_paths": [t.path for t in tex_items],
        "geoset_count": len(geosets),
        "vertex_counts": [g.vertex_count for g in geosets],
        "triangle_counts": [g.triangle_count for g in geosets],
        "node_count": len(points),
        "bone_count": len(bones),
        "skinned": bool(arm and bones),
        "vertex_groups": stats.get("vertex_groups", 0),
        "animation_action": anim_action.name if anim_action else "",
        "animation_keys": anim_keys,
        "skipped_influences": stats.get("skipped_influences", 0),
        "custom_normals": True,
        "sequences": sequences,
        "textures_loaded": stats.get("textures_loaded", 0),
        "textures_missing": stats.get("textures_missing", 0),
        "textures_unreadable": stats.get("textures_unreadable", 0),
        "texture_root": str(texture_root or ""),
        "casc": casc_info,
    }
    bpy.context.scene["mdx_meta"] = json.dumps(meta, ensure_ascii=False)
    return meta
