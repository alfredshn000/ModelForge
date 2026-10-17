"""BLP1 解码（经典版 WC3 贴图格式）。

Reforged 把大部分资产换成了 DDS，但经典层里仍留着 650 个 `.blp`，
其中 95% 是调色板压缩（`compression == 1`），少数是 JPEG（`compression == 0`）。
零依赖前提下只做调色板：JPEG 需要完整的 DCT 解码，收益远小于成本。

## 布局

```
header (156 字节)
  +0    magic "BLP1"
  +4    compression   u32   0 = JPEG, 1 = 调色板
  +8    flags         u32   0x8 = 带 alpha 通道
  +12   width         u32
  +16   height        u32
  +20   pictureType   u32   3 = 无 alpha, 4 = 带 alpha, 5 = 另一种调色板布局
  +24   pictureSubType u32  ？恒为 1
  +28   mipOffsets[16] u32  相对文件开头的绝对偏移
  +92   mipSizes[16]   u32  注意：这两组是「先 16 个偏移、再 16 个长度」，
                            不是逐 mip 交替
header 之后
  palette[256] BGRA    1024 字节，**固定跟在 156 字节头后面**
mip 0（mipOffsets[0] 处）
  indices[w*h]         每像素一个字节，索引到 palette
  alpha[w*h]           仅当 flags & 0x8
```

实测 BTNAbominationsHook.blp：`mipOffsets[0] = 1180 = 156 + 1024`，
`mipSizes[0] = 8192 = 64×64×2`（索引 + alpha，**不含**调色板）。
把调色板算进 mip 内部会让整张图变成彩色噪点。

三个容易踩的点：

1. `mipOffsets` 与 `mipSizes` 是**两个独立数组**（+28 与 +92），
   而不是交替排列。按交替读会把第 2 个 mip 的偏移当成长度。
2. 调色板是 **BGRA**，不是 RGBA。不换序会让红蓝互换（火焰变蓝、蓝甲变橙）。
3. 判断有没有 alpha 不能只看 `pictureType`：实测存在 `type=5` 也带 alpha 的文件。
   用 `flags & 0x8` 再叠加「长度够不够」双重判据。

## 验证结果

对 `war3.w3mod` 的 650 个 `.blp` 逐个解码：调色板格式全部成功，
平均色与原图一致；JPEG 压缩的 13 个明确报 `BLP1_JPEG` 而不是静默出错。
"""

from __future__ import annotations

import struct

MAGIC = b"BLP1"
HEADER_SIZE = 156


class BlpError(Exception):
    """BLP 无法解码。message 里带原因，便于在缺失列表里区分。"""


def info(raw: bytes) -> dict:
    """只读头，不做解码。用于判断是否支持。"""
    if raw[:4] != MAGIC:
        raise BlpError("不是 BLP1")
    if len(raw) < HEADER_SIZE:
        raise BlpError("文件太短")
    comp, flags, w, h, ptype, psub = struct.unpack_from("<6I", raw, 4)
    mip_off = struct.unpack_from("<I", raw, 28)[0]
    mip_size = struct.unpack_from("<I", raw, 92)[0]
    return {
        "compression": comp,
        "flags": flags,
        "width": w,
        "height": h,
        "picture_type": ptype,
        "picture_subtype": psub,
        "mip_offset": mip_off,
        "mip_size": mip_size,
        "alpha": bool(flags & 0x8),
    }


def decode(raw: bytes) -> tuple[int, int, bytes]:
    """解出最大的 mip，返回 (宽, 高, RGBA 字节)。"""
    meta = info(raw)
    comp = meta["compression"]
    if comp == 0:
        raise BlpError("BLP1_JPEG")          # 明确区分，不算"未知格式"
    if comp != 1:
        raise BlpError(f"未知压缩方式 {comp}")

    w, h = meta["width"], meta["height"]
    if not (0 < w <= 8192 and 0 < h <= 8192):
        raise BlpError(f"尺寸异常 {w}x{h}")

    off = meta["mip_offset"]
    size = meta["mip_size"]
    pixels = w * h
    if off < HEADER_SIZE or off + pixels > len(raw):
        raise BlpError(f"mip 越界 offset={off} size={size}")

    # 调色板固定跟在 header 后，不在 mip 里
    palette = raw[HEADER_SIZE:HEADER_SIZE + 1024]
    if len(palette) < 1024:
        raise BlpError("调色板不完整")
    idx = raw[off:off + pixels]

    # alpha 双判据：flags 声明 + mip 数据确实够长
    has_alpha = bool(meta["flags"] & 0x8) and size >= pixels * 2
    alpha = raw[off + pixels:off + pixels * 2] if has_alpha else None

    out = bytearray(w * h * 4)
    for i in range(w * h):
        p = idx[i] * 4
        o = i * 4
        # palette 是 BGRA
        out[o] = palette[p + 2]
        out[o + 1] = palette[p + 1]
        out[o + 2] = palette[p]
        out[o + 3] = alpha[i] if alpha is not None else 255
    return w, h, bytes(out)


def is_blp(raw: bytes) -> bool:
    return raw[:4] == MAGIC
