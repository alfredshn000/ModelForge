"""Blender 版本兼容层。

Blender 4.4 起 Action 改成分层槽位结构，5.x 里 `action.fcurves` 已被移除。
这里把「往某个对象上挂 F-Curve」的差异收在一处。

- 旧 API（<= 4.3）：`action.fcurves.new(...)`，直接挂到对象
- 新 API（>= 4.4）：`action.slots` → `action.layers` → `strips` → `channelbag(slot).fcurves`

新的槽位还会通过 `animation_data.action_slot` 绑定到对象上。
"""

from __future__ import annotations

import bpy


def is_layered(action) -> bool:
    return not hasattr(action, "fcurves")


def ensure_fcurve_container(action, obj, layer_name: str = "Layer"):
    """返回 (fcurves 容器, slot 或 None)。"""
    if not is_layered(action):
        return action.fcurves, None

    slot = action.slots.new(id_type="OBJECT", name=obj.name)
    layer = action.layers.new(layer_name)
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    return bag.fcurves, slot


def bind_action(obj, action, slot) -> None:
    """把 Action（以及槽位）绑定到对象。"""
    if not obj.animation_data:
        obj.animation_data_create()
    obj.animation_data.action = action
    if slot is not None:
        obj.animation_data.action_slot = slot


def find_fcurves(action, obj) -> list:
    """取出该对象在 Action 里的全部 F-Curve（兼容两种 API）。"""
    if not is_layered(action):
        return list(action.fcurves)

    out = []
    for slot in action.slots:
        for layer in action.layers:
            for strip in layer.strips:
                bag = strip.channelbag(slot)
                if bag:
                    out.extend(bag.fcurves)
    return out


def find_fcurve(action, obj, data_path: str, index: int):
    for fc in find_fcurves(action, obj):
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    return None
