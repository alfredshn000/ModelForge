"""MDX 导出。

设计原则：**二进制是真相，Blender 场景只是视图。**

导出分三条路，按「改了什么」自动选择：

| 情况 | 做法 |
|---|---|
| 什么都没改 | 原字节复制 |
| 只动了几何位置 / 动画字段 | **定点覆写**那些字段，其余字节一个不动，长度不变 |
| 增删了顶点 / 三角形 | 重建 GEOS（长度会变），但断言**只有 GEOS 和 BONE** 被改 |

任何一条路走完都要过 check_delta / check_chunk_rewrite，不通过就不写出——
宁可不导出，也不交一个可能坏掉的模型。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import bpy

from .bridge import get_core

NODE_CHANNELS = {
    "KGTR": ("location", (0, 1, 2)),
    "KGRT": ("rotation_quaternion", (1, 2, 3, 0)),   # WC3 (x,y,z,w) → Blender (w,x,y,z)
    "KGSC": ("scale", (0, 1, 2)),
}

# 浮点比较：Blender 的关键帧存 float32，读回来与文件里的 float32 应当完全相等；
# 留一点余量只为抵消 Python 侧的表达差异。
VALUE_EPS = 1e-6
# Blender 的关键帧与手柄坐标都是 float32。判断"用户有没有拖过手柄"时，
# 容差必须按坐标量级给：帧号 1500 处 float32 间隔 1.2e-4，数值 500 处 3e-5。
FLOAT32_EPS = 6e-8
HANDLE_TOL_SCALE = 8.0
# 法线比对的阈值：
#   - 基线是「导入时解码后的值」，所以未改动时应当完全相等（实测重编码是幂等的）
#   - 留 3e-3（约 0.17°）的余量，纯粹为了防止浮点层面的不幂等
#   - 文件里的法线到 Blender 的量化误差本身可达 5.3e-3，但那只影响「与文件比」，
#     不影响「与基线比」
NORMAL_REENCODE_NOISE = 3e-3


def _scene_geosets() -> dict[int, bpy.types.Object]:
    coll = bpy.data.collections.get("MDX")
    if not coll:
        return {}
    return {int(obj["mdx_geoset_index"]): obj
            for obj in coll.objects
            if obj.type == "MESH" and "mdx_geoset_index" in obj}


# ---------- 几何 ----------

def _compare_geoset(obj, geoset) -> dict:
    mesh = obj.data
    verts = [tuple(v.co) for v in mesh.vertices]
    orig = geoset.vertices

    n = min(len(verts), len(orig))
    max_dev = 0.0
    changed: dict[int, tuple] = {}
    for i in range(n):
        a, b = verts[i], orig[i]
        dev = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        if dev > 0:
            changed[i] = (a[0], a[1], a[2])
            max_dev = max(max_dev, dev)

    # 法线：拿导入时记录的基线比对。
    # 不能用「Blender 的值 vs 文件值」：自定义法线在 Blender 里是 2×int16 球面编码，
    # 解码误差可达 2e-3，任何固定阈值都会误判。基线是同一套解码结果，未改动时完全相等。
    changed_normals: dict[int, tuple] = {}
    baseline_raw = mesh.get("mdx_normals")
    if baseline_raw and geoset.normals:
        baseline = list(baseline_raw)
        per_vertex: list[tuple | None] = [None] * len(verts)
        for loop in mesh.loops:
            vi = loop.vertex_index
            if vi < len(per_vertex) and per_vertex[vi] is None:
                per_vertex[vi] = tuple(mesh.corner_normals[loop.index].vector)
        count = len(baseline) // 3
        for i in range(min(count, len(per_vertex))):
            got = per_vertex[i]
            if got is None:
                continue
            src = (baseline[i * 3], baseline[i * 3 + 1], baseline[i * 3 + 2])
            dev = max(abs(got[k] - src[k]) for k in range(3))
            # 阈值要高于「整层重新量化」的噪声（实测最大 2.1e-3），
            # 否则用户只是重设了一次法线层，就会把整块法线全重写。
            # 代价：小于约 0.2° 的改动分辨不出来——那本来就低于存储精度。
            if dev > NORMAL_REENCODE_NOISE:
                changed_normals[i] = (got[0], got[1], got[2])

    face_count = len(mesh.polygons)
    uv_ok = True
    if geoset.uv_sets and geoset.uv_sets[0] and mesh.uv_layers:
        layer = mesh.uv_layers[0]
        uvs = geoset.uv_sets[0]
        bad = 0
        for li, loop in enumerate(mesh.loops):
            vi = loop.vertex_index
            if vi >= len(uvs):
                continue
            u, v = layer.data[li].uv
            if abs(u - uvs[vi][0]) > 1e-5 or abs(v - (1.0 - uvs[vi][1])) > 1e-5:
                bad += 1
        uv_ok = bad == 0

    return {
        "geoset": geoset.offset,
        "vertex_count_scene": len(verts),
        "vertex_count_file": len(orig),
        "triangle_count_scene": face_count,
        "triangle_count_file": geoset.triangle_count,
        "vertex_mismatch": len(changed),
        "normal_mismatch": len(changed_normals),
        "max_deviation": max_dev,
        "uv_ok": uv_ok,
        "changed": changed,
        "changed_normals": changed_normals,
    }


def _pos_key(p) -> tuple:
    return (round(p[0], 6), round(p[1], 6), round(p[2], 6))


def _match_vertices(orig: list, verts: list) -> dict[int, int]:
    """新顶点索引 → 原顶点索引，按坐标完全相等匹配。

    删除顶点后 Blender 会让后面的顶点索引整体前移，所以不能用索引对应，
    只能按坐标找回「这个顶点原来是哪个」。同坐标的重复顶点按出现顺序消费。
    """
    buckets: dict[tuple, list[int]] = {}
    for i, v in enumerate(orig):
        buckets.setdefault(_pos_key(v), []).append(i)

    match: dict[int, int] = {}
    for ni, v in enumerate(verts):
        cand = buckets.get(_pos_key(v))
        if cand:
            match[ni] = cand.pop(0)
    return match


def _inherit_map(match: dict[int, int], verts: list, limit: int = 200) -> dict[int, int]:
    """新增顶点继承属性的来源：取空间上最近的已匹配顶点。

    全量重拓扑时未匹配顶点可能上万，逐点找最近是 O(n²)，这里设一个阈值：
    超过 limit 就退化为「索引最近的已匹配顶点」，只在报告里标注。
    """
    out: dict[int, int] = {}
    if not match:
        return out
    unmatched = [i for i in range(len(verts)) if i not in match]
    if not unmatched:
        return out

    keys = sorted(match)
    if len(unmatched) > limit:
        for ni in unmatched:
            pos = min(len(keys) - 1, max(0, ni))
            lo = keys[max(0, pos - 1)]
            hi = keys[min(len(keys) - 1, pos)]
            out[ni] = match[lo if abs(lo - ni) <= abs(hi - ni) else hi]
        return out

    anchors = [(verts[ni], oi) for ni, oi in match.items()]
    for ni in unmatched:
        p = verts[ni]
        out[ni] = min(anchors, key=lambda a: ((p[0] - a[0][0]) ** 2
                                              + (p[1] - a[0][1]) ** 2
                                              + (p[2] - a[0][2]) ** 2))[1]
    return out


def _quantize_skin(obj, mesh, bone_index_by_name: dict[str, int]) -> list[bytes]:
    """按 Blender 顶点组重建 SKIN：每顶点 4 个 (骨骼 u8, 权重 u8)，权重和为 255。"""
    groups = [vg.name for vg in obj.vertex_groups]
    out: list[bytes] = []
    for v in mesh.vertices:
        pairs = []
        for g in v.groups:
            if g.group >= len(groups):
                continue
            name = groups[g.group]
            idx = bone_index_by_name.get(name)
            if idx is None or g.weight <= 0:
                continue
            pairs.append((idx, g.weight))
        pairs.sort(key=lambda p: -p[1])
        pairs = pairs[:4]
        if not pairs:
            out.append(b"\x00" * 8)
            continue
        total = sum(w for _, w in pairs)
        scaled = [int(round(w / total * 255)) for _, w in pairs]
        diff = 255 - sum(scaled)
        scaled[0] += diff                      # 误差补到权重最大的那个
        bytes_ = bytearray(8)
        for k, ((idx, _), w) in enumerate(zip(pairs, scaled)):
            bytes_[k] = idx & 0xFF
            bytes_[4 + k] = max(0, min(255, w))
        out.append(bytes(bytes_))
    return out


def _rebuild_geoset(obj, geoset, bone_index_by_name: dict[str, int]) -> dict:
    """按场景重建一个 geoset 的顶点相关数组（结构变更时使用）。

    原则：**用户没碰的属性一律沿用原字节**。只有真的变了（或者顶点是新增的）
    才重算，这样即使做了拓扑编辑，未触及的部分仍然逐字节不变。
    """
    mesh = obj.data
    verts = [tuple(v.co) for v in mesh.vertices]
    match = _match_vertices(geoset.vertices, verts)
    inherit = _inherit_map(match, verts)
    unmatched = len(verts) - len(match)

    def src(ni: int) -> int:
        return match[ni] if ni in match else inherit.get(ni, 0)

    # 顶点
    geoset.vertices = [(v[0], v[1], v[2]) for v in verts]

    # 法线：导入了自定义法线就以场景为准（用户能在 Blender 里改），
    # 否则沿用原法线、新增顶点继承最近顶点——不要用 Blender 自动重算的结果，
    # 那会把整块几何的着色风格改掉。
    scene_normals = None
    if geoset.normals and getattr(mesh, "has_custom_normals", False):
        scene_normals = [None] * len(verts)
        for loop in mesh.loops:
            vi = loop.vertex_index
            if scene_normals[vi] is None:
                scene_normals[vi] = tuple(mesh.corner_normals[loop.index].vector)

    normals = []
    for ni in range(len(verts)):
        got = scene_normals[ni] if scene_normals else None
        if got is not None:
            normals.append(got)
        elif geoset.normals:
            normals.append(geoset.normals[src(ni)])
        else:
            normals.append((0.0, 0.0, 1.0))
    geoset.normals = normals

    # 顶点组索引（GNDX）
    if geoset.vertex_groups:
        geoset.vertex_groups = bytes(geoset.vertex_groups[src(ni)]
                                     for ni in range(len(verts)))

    # UV：按「第一个引用该顶点的 loop」取值；与原值一致时用原值，保证字节不变
    if geoset.uv_sets and mesh.uv_layers:
        layer = mesh.uv_layers[0]
        per_vertex: list[tuple | None] = [None] * len(verts)
        for li, loop in enumerate(mesh.loops):
            vi = loop.vertex_index
            if per_vertex[vi] is None:
                u, v = layer.data[li].uv
                per_vertex[vi] = (u, 1.0 - v)
        for si, uv_set in enumerate(geoset.uv_sets):
            new_uvs = []
            for ni in range(len(verts)):
                old = match.get(ni)
                cur = per_vertex[ni]
                if old is not None and cur is not None and old < len(uv_set):
                    a, b = uv_set[old]
                    if abs(cur[0] - a) < 1e-6 and abs(cur[1] - b) < 1e-6:
                        new_uvs.append((a, b))
                        continue
                new_uvs.append(cur or (0.0, 0.0))
            geoset.uv_sets[si] = new_uvs

    # 切线：沿用原值
    if geoset.tangents is not None:
        tangents = []
        for ni in range(len(verts)):
            s = src(ni) * 4
            tangents.extend(geoset.tangents[s:s + 4] or (0.0, 0.0, 0.0, 1.0))
        geoset.tangents = tangents

    # 蒙皮：以 Blender 顶点组为准；与原值一致时保留原字节
    if geoset.skin is not None:
        fresh = _quantize_skin(obj, mesh, bone_index_by_name)
        skin = bytearray()
        for ni in range(len(verts)):
            old = match.get(ni)
            cur = fresh[ni]
            if old is not None:
                prev = geoset.skin[old * 8:(old + 1) * 8]
                if _skin_equal(prev, cur):
                    skin += prev
                    continue
            skin += cur
        geoset.skin = bytes(skin)

    # 面：全是三角形时按原顺序取，否则用 Blender 的三角化结果
    polygons = list(mesh.polygons)
    if polygons and all(p.loop_total == 3 for p in polygons):
        faces = [mesh.loops[li].vertex_index for p in polygons for li in p.loop_indices]
    else:
        mesh.calc_loop_triangles()
        faces = [mesh.loops[li].vertex_index
                 for tri in mesh.loop_triangles for li in tri.loops]
    geoset.faces = faces
    if geoset.face_groups:
        geoset.face_groups = [len(faces)] + [0] * (len(geoset.face_groups) - 1)

    return {"matched": len(match), "unmatched": unmatched,
            "vertices": len(verts), "triangles": len(faces) // 3}


def _skin_equal(a: bytes, b: bytes) -> bool:
    """同一顶点两套蒙皮是否等价（骨骼索引顺序可能不同，按骨骼合并后比较）。"""
    def decode(raw: bytes) -> dict:
        out: dict[int, float] = {}
        for k in range(4):
            if raw[4 + k]:
                out[raw[k]] = out.get(raw[k], 0.0) + raw[4 + k] / 255.0
        return out

    da, db = decode(a), decode(b)
    if set(da) != set(db):
        return False
    return all(abs(da[k] - db[k]) <= 1.5 / 255 for k in da)


# ---------- 动画 ----------

def _moved(got, expect) -> bool:
    """手柄是否被挪动过：容差按坐标量级取（float32 存储精度）。"""
    tol_x = FLOAT32_EPS * max(abs(expect[0]), abs(got[0]), 1.0) * HANDLE_TOL_SCALE
    tol_y = FLOAT32_EPS * max(abs(expect[1]), abs(got[1]), 1.0) * HANDLE_TOL_SCALE
    return abs(got[0] - expect[0]) > tol_x or abs(got[1] - expect[1]) > tol_y


def _handles_moved(curves, order, i, orig_frames, frames, track,
                   new_value, fps: float) -> tuple[bool, bool]:
    """手柄是否真的被拖动过。

    不能直接比较「从手柄反算的切线」与文件切线：关键帧时间以 float32 存，
    时间间隔只有几十毫秒时，反算会把存储精度放大成 1e-4 级别的切线误差，
    于是"什么都没动"也会被判成改了。

    改成比较手柄坐标：导入时是按下式精确摆放的，若用户没拖过，当前位置应当
    只差 float32 舍入。用户真的拖动时位移远大于这个量级。
    """
    from mdxcore import tangents as tan_mod

    d_frame = frames[i] - orig_frames[i]
    left_moved = right_moved = False
    for comp_slot, comp in enumerate(order):
        kp = curves[comp_slot].keyframe_points[i]
        d_value = new_value[comp] - track.value[comp]
        left, right = tan_mod.to_handles(
            orig_frames, i, track.value[comp],
            track.in_tan[comp], track.out_tan[comp], fps)
        if _moved(kp.handle_left, (left[0] + d_frame, left[1] + d_value)):
            left_moved = True
        if _moved(kp.handle_right, (right[0] + d_frame, right[1] + d_value)):
            right_moved = True
    return left_moved, right_moved


def _flag_from_curves(tag, interp, global_seq, curves, order, fps):
    """把一组 F-Curve 读成一条 AnimFlag。"""
    from mdxcore import tangents as tan_mod
    from mdxcore.chunks.animflag import AnimFlag, Track

    n = len(curves[0].keyframe_points)
    frames = [kp.co[0] for kp in curves[0].keyframe_points]
    comps = len(order)
    tracks: list[Track] = []

    for i in range(n):
        value = [0.0] * comps
        in_tan = [0.0] * comps
        out_tan = [0.0] * comps
        for slot, comp in enumerate(order):
            kp = curves[slot].keyframe_points[i]
            value[comp] = kp.co[1]
            if interp > 1:
                tin, tout = tan_mod.from_handles(
                    frames, i, kp.co[1], kp.handle_left, kp.handle_right, fps)
                in_tan[comp] = tin
                out_tan[comp] = tout
        tracks.append(Track(
            time=int(round(frames[i] * 1000.0 / fps)),
            value=tuple(value),
            in_tan=tuple(in_tan) if interp > 1 else None,
            out_tan=tuple(out_tan) if interp > 1 else None,
        ))

    tracks.sort(key=lambda t: t.time)
    for a, b in zip(tracks, tracks[1:]):
        if a.time == b.time:
            raise ValueError(f"{tag}: 有两个关键帧落在同一毫秒（{a.time} ms）")

    return AnimFlag(tag=tag, interp=interp, global_seq_id=global_seq, tracks=tracks)


def _collect_anim_edits(mdx, bones, name_by_index, fps: float):
    """比对场景关键帧与文件轨道。

    返回 (字段级补丁, 问题, 统计, 结构性重建或 None)。

    关键帧数量变化时无法定点覆写（轨道块长度变了），会退化为**重建这些骨骼的
    记录**：每条记录是 `前缀 + 轨道 + 尾部`，重建后其它记录原样拼接。
    """
    from mdxcore.chunks import bone as bonemod
    from .bpy_compat import find_fcurve

    data = mdx.data(mdx.find("BONE"))
    arm = bpy.data.objects.get("MDX_Armature")
    if arm is None or not arm.animation_data or not arm.animation_data.action:
        # 文件里本来就没有动画轨道时，场景没有 Action 是正常的，不该报错
        has_tracks = any(bonemod.parse_tracks(data, b) for b in bones)
        if has_tracks:
            return [], ["文件里有动画轨道，但场景里没有 Action，无法比对改动"], {}, None
        return [], [], {}, None
    action = arm.animation_data.action

    items: list[dict] = []
    problems: list[str] = []
    stats = {"curves": 0, "keyframes": 0, "time": 0, "value": 0, "tangent": 0,
             "added_keys": 0, "removed_keys": 0, "skipped_duplicate_time": 0}
    scene_tracks: dict[int, dict] = {}
    structural_bones: set[int] = set()

    for b in bones:
        file_tracks = bonemod.parse_tracks(data, b)
        name = name_by_index.get(b.index)
        if not name:
            continue
        path_base = f'pose.bones["{name}"]'
        built: dict[str, object] = {}
        bone_structural = False

        for tag in ("KGTR", "KGRT", "KGSC"):
            prop, order = NODE_CHANNELS[tag]
            af = file_tracks.get(tag)
            curves = [find_fcurve(action, arm, f"{path_base}.{prop}", slot)
                      for slot in range(len(order))]

            if all(c is None for c in curves):
                if af is not None:
                    bone_structural = True          # 整条轨道被删掉
                continue
            if any(c is None for c in curves):
                problems.append(f"{name}.{prop}: 曲线不完整（{sum(c is not None for c in curves)}/{len(order)}）")
                continue

            stats["curves"] += len(curves)
            n = len(curves[0].keyframe_points)
            if n == 0:
                if af is not None:
                    bone_structural = True
                continue
            if any(len(c.keyframe_points) != n for c in curves):
                problems.append(f"{name}.{prop}: 各分量的关键帧数不一致")
                continue

            interp = af.interp if af is not None else 1
            global_seq = af.global_seq_id if af is not None else -1

            if interp > 1:
                kp0 = curves[0].keyframe_points[0]
                if kp0.handle_left_type != "FREE" or kp0.handle_right_type != "FREE":
                    problems.append(
                        f"{name}.{prop}: 手柄类型是 {kp0.handle_left_type}，"
                        "不是 FREE —— 切线会被 Blender 重算，拒绝写回")
                    continue

            # WC3 允许同一毫秒有两个关键帧（做瞬时跳变用），而 Blender 的
            # F-Curve 会把同帧的 key 合并，导致数量比文件里少。
            # 这种轨道不参与比对也不重建 —— 原样透传，否则一打开就会掉数据。
            if af is not None and n != len(af.tracks):
                stamps = [t.time for t in af.tracks]
                if len(stamps) != len(set(stamps)) and n == len(set(stamps)):
                    stats["skipped_duplicate_time"] = stats.get("skipped_duplicate_time", 0) + 1
                    built.pop(tag, None)
                    continue

            if af is None or n != len(af.tracks):
                bone_structural = True
                delta = n - (len(af.tracks) if af is not None else 0)
                if delta > 0:
                    stats["added_keys"] += delta
                else:
                    stats["removed_keys"] += -delta
            stats["keyframes"] += n

            try:
                built[tag] = _flag_from_curves(tag, interp, global_seq, curves, order, fps)
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
                continue

            # 结构没变时，逐字段比对（定点覆写）
            if af is not None and n == len(af.tracks):
                frames = [kp.co[0] for kp in curves[0].keyframe_points]
                orig_frames = [t.time / 1000.0 * fps for t in af.tracks]
                for i, track in enumerate(af.tracks):
                    edit: dict = {"bone": b.index, "tag": tag, "index": i}
                    touched = False

                    want_ms = int(round(frames[i] * 1000.0 / fps))
                    if want_ms != track.time:
                        edit["time"] = want_ms
                        stats["time"] += 1
                        touched = True

                    values = [c.keyframe_points[i].co[1] for c in curves]
                    new_value = tuple(values[order.index(comp)] for comp in range(len(order)))
                    if any(abs(new_value[k] - track.value[k]) > VALUE_EPS
                           for k in range(len(order))):
                        edit["value"] = new_value
                        stats["value"] += 1
                        touched = True

                    if af.has_tangents:
                        left_moved, right_moved = _handles_moved(
                            curves, order, i, orig_frames, frames, track, new_value, fps)
                        if left_moved or right_moved:
                            flag = built[tag]
                            if left_moved:
                                edit["in_tan"] = flag.tracks[i].in_tan
                            if right_moved:
                                edit["out_tan"] = flag.tracks[i].out_tan
                            stats["tangent"] += 1
                            touched = True

                    if touched:
                        items.append(edit)

        if built or file_tracks:
            scene_tracks[b.index] = built
        if bone_structural:
            structural_bones.add(b.index)

    if not structural_bones:
        return items, problems, stats, None

    # 有结构性变化 → 走记录重建。
    #
    # 关键：**没变结构的轨道直接沿用文件里解析出来的对象**，不要从曲线重新生成。
    # 从手柄反算切线会带上 float32 噪声，哪怕用户没碰，字节也会变。
    items_by_bone: dict[int, list] = {}
    for it in items:
        items_by_bone.setdefault(it["bone"], []).append(it)

    # 只重建「结构变了」或「有字段改动」的骨骼；其余记录原样透传
    needed = set(structural_bones) | set(items_by_bone)
    rebuilds: dict[int, bytes] = {}
    for b in bones:
        if b.index not in needed:
            continue
        file_tracks = bonemod.parse_tracks(data, b)
        tracks = dict(file_tracks)                       # 保持文件里的顺序
        built = scene_tracks.get(b.index, {})

        for tag, flag in built.items():
            old = file_tracks.get(tag)
            if old is None or len(old.tracks) != len(flag.tracks):
                tracks[tag] = flag                       # 新增 / 数量变化
        for tag in list(tracks):
            if tag not in built:
                tracks.pop(tag)                          # 用户删光了这条轨道

        for it in items_by_bone.get(b.index, []):
            if it["tag"] not in tracks:
                continue
            t = tracks[it["tag"]].tracks[it["index"]]
            if "time" in it:
                t.time = it["time"]
            if "value" in it:
                t.value = it["value"]
            if "in_tan" in it:
                t.in_tan = it["in_tan"]
            if "out_tan" in it:
                t.out_tan = it["out_tan"]

        rebuilds[b.index] = bonemod.build_record(data, b, tracks)

    stats["structural"] = True
    stats["rebuilt_bones"] = len(rebuilds)
    return [], problems, stats, rebuilds


# ---------- 主流程 ----------

def export_mdx(filepath: str) -> tuple[dict, str]:
    mdxcore = get_core()
    from mdxcore import rebuild
    from mdxcore.chunks import bone as bonemod
    from mdxcore.chunks import geos
    from mdxcore.diff import diff_ranges
    from mdxcore.ops import (all_vertex_spans, apply_mesh_patches, apply_vertex_patches,
                             format_version, mesh_spans, patch_tracks, track_spans)
    from mdxcore.verify import check_chunk_rewrite, check_delta
    from .importer import bone_names

    meta_raw = bpy.context.scene.get("mdx_meta")
    if not meta_raw:
        raise RuntimeError("当前场景没有 MDX 元数据，请先用 MDX 导入")

    meta = json.loads(meta_raw)
    src = Path(meta["source_path"])
    if not src.is_file():
        raise FileNotFoundError(f"原始文件不存在：{src}")

    mdx = mdxcore.load(src)
    version = format_version(mdx)
    before = src.read_bytes()

    file_geosets = []
    if any(c.tag == "GEOS" for c in mdx.chunks):
        file_geosets = geos.parse(mdx.data(mdx.find("GEOS")), version)

    # ---- 骨骼与名字映射 ----
    bones = []
    name_by_index: dict[int, str] = {}
    bone_idx_by_name: dict[str, int] = {}
    if any(c.tag == "BONE" for c in mdx.chunks):
        bones = bonemod.parse(mdx.data(mdx.find("BONE")))
        name_by_index = bone_names(bones)
        bone_idx_by_name = {v: k for k, v in name_by_index.items()}

    scene_objs = _scene_geosets()
    checks = []
    problems: list[str] = []
    patches: dict[int, dict[int, tuple]] = {}
    normal_patches: dict[int, dict[int, tuple]] = {}
    rebuilt: list[dict] = []

    # ---- 几何 ----
    for gi, g in enumerate(file_geosets):
        obj = scene_objs.get(gi)
        if obj is None:
            problems.append(f"geoset {gi} 在场景里不存在")
            continue
        r = _compare_geoset(obj, g)
        changed = r.pop("changed")
        changed_normals = r.pop("changed_normals")
        checks.append(r)

        if r["vertex_count_scene"] != r["vertex_count_file"]:
            info = _rebuild_geoset(obj, g, bone_idx_by_name)
            info["geoset"] = gi
            info["vertices_before"] = r["vertex_count_file"]
            info["vertices_after"] = r["vertex_count_scene"]
            info["triangles_before"] = r["triangle_count_file"]
            info["triangles_after"] = r["triangle_count_scene"]
            rebuilt.append(info)
        else:
            if changed:
                patches[gi] = changed
            if changed_normals:
                normal_patches[gi] = changed_normals

    # ---- 动画 ----
    fps = float(bpy.context.scene.render.fps or 24)
    anim_items: list[dict] = []
    anim_stats: dict = {}
    bone_rebuilds: dict[int, bytes] = {}
    if bones:
        anim_items, anim_problems, anim_stats, builds = _collect_anim_edits(
            mdx, bones, name_by_index, fps)
        bone_rebuilds = builds or {}
        problems.extend(anim_problems)
    else:
        anim_stats = {}

    # ---- 材质 / 贴图 / 几何动画 / 序列（定长字段，长度不变） ----
    # 这四类的改动都落在固定偏移上，文件长度不会变，所以走 check_delta。
    state_editor = None
    state_edits: list[dict] = []
    state_warnings: list[str] = []
    state_summary = ""
    if bpy.context.scene.get("mdx_state"):
        from . import state as statemod
        from . import state_export

        snapshot = statemod.load()
        if snapshot:
            state_editor, state_edits, state_problems, state_warnings = \
                state_export.build(mdx, snapshot)
            problems.extend(state_problems)
            state_summary = state_export.summarize(state_edits)

    dst = Path(filepath)
    dst.parent.mkdir(parents=True, exist_ok=True)

    has_geom = bool(patches) or bool(normal_patches) or bool(rebuilt)
    has_anim = bool(anim_items) or bool(bone_rebuilds)
    has_state = bool(state_editor)

    if problems:
        shutil.copyfile(src, dst)
        mode = "拒绝写入（存在问题），已写出原始字节"
        verified = False
        changed_bytes = 0
    elif not has_geom and not has_anim and not has_state:
        shutil.copyfile(src, dst)
        mode = "透传原始字节"
        verified = True
        changed_bytes = 0
    else:
        replacements: dict[int, bytes] = {}
        if rebuilt:
            # 重建会覆盖整个 GEOS，所以别的 geoset 的定点改动必须先灌进对象里，
            # 否则会被 build() 的结果盖掉。
            for gi, changes in patches.items():
                for vi, xyz in changes.items():
                    file_geosets[gi].vertices[vi] = xyz
            for gi, changes in normal_patches.items():
                for vi, xyz in changes.items():
                    file_geosets[gi].normals[vi] = xyz
            geos_index = next(i for i, c in enumerate(mdx.chunks) if c.tag == "GEOS")
            replacements[geos_index] = geos.build(file_geosets, version)
        elif patches or normal_patches:
            replacements.update(apply_mesh_patches(mdx, patches, normal_patches))
        if anim_items:
            replacements.update(patch_tracks(mdx, anim_items))
        if bone_rebuilds:
            bone_index = next(i for i, c in enumerate(mdx.chunks) if c.tag == "BONE")
            replacements[bone_index] = bonemod.rebuild_block(
                mdx.data(mdx.find("BONE")), bones, bone_rebuilds)
        if state_editor:
            replacements.update(state_editor.apply())

        after = rebuild(mdx, replacements)
        structural = bool(rebuilt or bone_rebuilds)
        if structural:
            allowed = set()
            if rebuilt:
                allowed.add("GEOS")
            if anim_items or bone_rebuilds:
                allowed.add("BONE")
            if state_editor:
                allowed |= state_editor.touched()
            issues = check_chunk_rewrite(before, after, mdx, allowed)
        else:
            spans = mesh_spans(mdx, patches, normal_patches) if (patches or normal_patches) else []
            if anim_items:
                spans += track_spans(mdx, anim_items)
            if state_editor:
                spans += state_editor.abs_spans()
            issues = check_delta(before, after, mdx, spans)

        changed_bytes = sum(r.length for r in diff_ranges(before, after))
        if issues:
            problems.extend(issues)
            shutil.copyfile(src, dst)
            mode = "校验失败，已写出原始字节"
            verified = False
        else:
            dst.write_bytes(after)
            parts = []
            if patches:
                parts.append(f"移动 {sum(len(v) for v in patches.values())} 个顶点")
            if normal_patches:
                parts.append(f"改 {sum(len(v) for v in normal_patches.values())} 个法线")
            if rebuilt:
                parts.append("重建几何（顶点数变化）")
            if anim_items:
                parts.append(f"动画改 {len(anim_items)} 处")
            if bone_rebuilds:
                parts.append(f"重建 {len(bone_rebuilds)} 根骨骼的动画记录")
            if state_summary:
                parts.append(state_summary)
            if anim_stats.get("skipped_duplicate_time"):
                parts.append(f"{anim_stats['skipped_duplicate_time']} 条轨道含重复时间戳，原样保留")
            parts.append(f"{len(before)} → {len(after)} B，{changed_bytes} 字节不同")
            mode = "；".join(parts)
            verified = True

    report = {
        "source": str(src),
        "written": str(dst),
        "format_version": version,
        "fps": fps,
        "byte_identical": dst.read_bytes() == src.read_bytes(),
        "geometry_unchanged": not patches and not rebuilt,
        "mode": mode,
        "verified": verified,
        "patched_vertices": sum(len(v) for v in patches.values()),
        "patched_normals": sum(len(v) for v in normal_patches.values()),
        "rebuilt_geosets": rebuilt,
        "animation": anim_stats,
        "animation_edits": len(anim_items),
        "bone_records_rebuilt": len(bone_rebuilds),
        "material_edit_count": len(state_edits),
        "material_edits": state_edits,
        "material_warnings": state_warnings,
        "changed_bytes": changed_bytes,
        "checks": checks,
        "problems": problems,
    }
    # 报告落到场景属性，面板才能画出来（不管是面板按钮还是脚本调用）
    try:
        bpy.context.scene["mdx_last_report"] = json.dumps(report, ensure_ascii=False)
    except Exception:                              # noqa: BLE001
        pass
    return report, str(dst)
