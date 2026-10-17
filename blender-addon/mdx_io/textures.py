"""贴图解析：把 TEXS 里的路径映射到本地文件，并加载成 Blender 图像。

WC3 的贴图路径是**相对游戏数据**的，不相对模型文件；而且 Reforged 的路径常是
虚拟名（`.tif`），实际文件在别处。所以这里不假设目录结构，而是：

1. 先按原路径拼到用户指定的贴图根目录下
2. 找不到就**按文件名（去扩展名）**在索引里查一次——导出工具常常会把
   `.tif` 存成 `.dds`，扩展名靠不住
3. 还找不到就如实报告「缺失」，不猜

只有真正能读的格式才交给 Blender 加载。`.blp` 是暴雪私有格式，Blender 打不开，
这里明确跳过并记数，避免出现「导入了但全是灰模，不知道为什么」。
"""

from __future__ import annotations

from pathlib import Path

import bpy

# Blender 能直接读的格式（Reforged 资产基本是 dds / tif）
LOADABLE = {".dds", ".tif", ".tiff", ".png", ".tga", ".jpg", ".jpeg", ".bmp"}
# 认识但读不了
KNOWN_UNREADABLE = {".blp"}


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("/")


def build_index(root: Path) -> dict[str, Path]:
    """按「文件名去扩展名」建索引，用于扩展名不一致时的兜底匹配。"""
    index: dict[str, Path] = {}
    if not root or not root.is_dir():
        return index
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        if file.suffix.lower() not in LOADABLE and file.suffix.lower() not in KNOWN_UNREADABLE:
            continue
        index.setdefault(file.stem.lower(), file)
    return index


def resolve(path: str, root: Path | None, index: dict[str, Path]) -> Path | None:
    """把 TEXS 里的路径解析成本地文件；解析不到返回 None。"""
    if not path:
        return None
    rel = _norm(path)

    if root:
        direct = root / rel
        if direct.is_file():
            return direct
    else:
        direct = Path(rel)
        if direct.is_file():
            return direct

    stem = Path(rel).stem.lower()
    return index.get(stem)


def load_image(file: Path, name: str | None = None):
    """用 Blender 的图像加载器读一个文件；读不了就返回 None。"""
    if file.suffix.lower() not in LOADABLE:
        return None
    try:
        image = bpy.data.images.load(str(file), check_existing=True)
    except Exception:                              # noqa: BLE001
        return None
    if name and image.name != name:
        try:
            image.name = name
        except Exception:                          # noqa: BLE001
            pass
    return image


def set_image_on_material(material, image, uv_name: str | None = None) -> bool:
    """把贴图换到 Base Color 上已有的那个图像节点里；没有就新建一个。

    单独写一个而不是每次都 attach_to_material：反复改路径会把节点越堆越多，
    最后满树都是废弃的贴图节点。
    """
    if not material or not image or not material.use_nodes:
        return False
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False

    for node in nodes:
        if node.type != "TEX_IMAGE":
            continue
        feeds_base = any(link.to_node is bsdf and link.to_socket.name == "Base Color"
                         for link in node.outputs["Color"].links)
        if feeds_base:
            node.image = image
            node.label = image.name
            return True

    # 没找到就清掉孤立节点再新建，避免残留
    for node in [n for n in nodes if n.type == "TEX_IMAGE" and not n.outputs["Color"].links]:
        nodes.remove(node)
    return attach_to_material(material, image, uv_name)


def attach_to_material(material, image, uv_name: str | None = None) -> bool:
    """把贴图接到材质的 Base Color 上。返回是否接上了。"""
    if not material or not image or not material.use_nodes:
        return False
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.location = (-400, 300)
    tex.label = image.name
    if uv_name:
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map = uv_name
        uv.location = (-620, 300)
        links.new(uv.outputs["UV"], tex.inputs["Vector"])

    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    # WC3 的贴图基本都是自发光/无光照的观感，给一点粗糙度避免高光过强
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.85
    return True
