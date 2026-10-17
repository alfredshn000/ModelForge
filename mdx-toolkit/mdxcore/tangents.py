"""WC3 切线 ↔ Blender 贝塞尔手柄的换算。

两边的量纲不一样，这是动画写回唯一的硬骨头：

| 项 | WC3 | Blender |
|---|---|---|
| 时间单位 | 毫秒 | 帧（1 帧 = 1000/fps 毫秒） |
| 切线 | **数值 / 毫秒**（Hermite 导数） | 手柄是曲线上的一个点，斜率 = Δ值/Δ帧 |

于是：

    slope(值/帧) = tangent(值/毫秒) × fps / 1000
    tangent      = slope × 1000 / fps

**为什么手柄只放 1/3 间距**：三次 Hermite 样条在区间 h、两端导数 m0/m1 下，
等价于控制点 P1 = P0 + h/3·m0、P2 = P3 − h/3·m1 的三次贝塞尔。所以按 1/3 放手柄，
Blender 的曲线与 WC3 的 Hermite 曲线**逐点相同**，不是近似。

本模块是纯函数、不依赖 bpy，可以被自测直接覆盖。
"""

from __future__ import annotations

FPS_DEFAULT = 24.0


def tangent_to_slope(tangent: float, fps: float) -> float:
    """值/毫秒 → 值/帧。"""
    return tangent * fps / 1000.0


def slope_to_tangent(slope: float, fps: float) -> float:
    """值/帧 → 值/毫秒。"""
    return slope * 1000.0 / fps


def hand_span(frames: list[float], i: int) -> tuple[float, float]:
    """第 i 个关键帧左右两侧可用的帧间距（帧）。"""
    n = len(frames)
    if n < 2:
        return 1.0, 1.0
    if i <= 0:
        d = frames[1] - frames[0]
        return d, d
    if i >= n - 1:
        d = frames[-1] - frames[-2]
        return d, d
    return frames[i] - frames[i - 1], frames[i + 1] - frames[i]


def to_handles(frames: list[float], i: int, value: float,
               in_tan: float, out_tan: float, fps: float) -> tuple[tuple, tuple]:
    """WC3 切线 → Blender 手柄坐标 ((lx, ly), (rx, ry))。"""
    h_in, h_out = hand_span(frames, i)
    dl = h_in / 3.0
    dr = h_out / 3.0
    f = frames[i]
    left = (f - dl, value - tangent_to_slope(in_tan, fps) * dl)
    right = (f + dr, value + tangent_to_slope(out_tan, fps) * dr)
    return left, right


def from_handles(frames: list[float], i: int, value: float,
                 left: tuple, right: tuple, fps: float) -> tuple[float, float]:
    """Blender 手柄坐标 → WC3 切线 (in, out)。"""
    f = frames[i]

    def slope(handle) -> float:
        dx = handle[0] - f
        if abs(dx) < 1e-12:
            return 0.0
        return (handle[1] - value) / dx

    return slope_to_tangent(slope(left), fps), slope_to_tangent(slope(right), fps)


def shift(frame: float, delta: float, points: list[tuple]) -> list[tuple]:
    """整体平移一组 (x, y) 点（关键帧连同手柄一起移动时斜率不变）。"""
    return [(x + delta, y) for x, y in points]
