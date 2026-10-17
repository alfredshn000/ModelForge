"""DDS 解码（DXT1 / DXT3 / DXT5 / ATI2-BC5 / 未压缩 RGBA）+ 手写 PNG 编码。

只管"字节 → 像素"，不依赖 Pillow 之类的库。用途是把 WC3 的贴图塞进预览渲染。

支持范围按实测到的格式定：本地 57 张贴图里 DXT5 42 张、ATI2 8 张、DXT1 7 张。
"""

from __future__ import annotations

import struct
import zlib

HEADER_SIZE = 128


class DdsError(ValueError):
    """DDS 结构不支持或损坏。"""


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def info(data: bytes) -> dict:
    """读头部信息，不解码。"""
    if data[:4] != b"DDS ":
        raise DdsError("不是 DDS 文件")
    return {
        "height": _u32(data, 12),
        "width": _u32(data, 16),
        "mipmaps": _u32(data, 28) or 1,
        "fourcc": data[84:88].decode("latin-1"),
        "rgb_bits": _u32(data, 88),
        "data_offset": HEADER_SIZE,
    }


# ---------------------------------------------------------------- 颜色表
def _c565(v: int) -> tuple:
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _dxt1_table(c0: int, c1: int, dxt1: bool) -> list:
    """返回 4 个 (r,g,b,a)。DXT1 在 c0<=c1 时第三个是透明黑。"""
    a = _c565(c0)
    b = _c565(c1)
    if (not dxt1) or c0 > c1:
        c2 = tuple((2 * a[i] + b[i]) // 3 for i in range(3))
        c3 = tuple((a[i] + 2 * b[i]) // 3 for i in range(3))
        return [a + (255,), b + (255,), c2 + (255,), c3 + (255,)]
    c2 = tuple((a[i] + b[i]) // 2 for i in range(3))
    return [a + (255,), b + (255,), c2 + (255,), (0, 0, 0, 0)]


def _alpha_table(a0: int, a1: int) -> list:
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


# ---------------------------------------------------------------- 解码
def _decode_color_block(data: bytes, p: int, out: bytearray, w: int, h: int,
                        bx: int, by: int, dxt1: bool) -> None:
    c0, c1, bits = struct.unpack_from("<HHI", data, p)
    table = _dxt1_table(c0, c1, dxt1)
    for py in range(4):
        y = by * 4 + py
        if y >= h:
            continue
        row = bits >> (py * 8)
        base = y * w
        for px in range(4):
            x = bx * 4 + px
            if x >= w:
                continue
            r, g, b, a = table[(row >> (px * 2)) & 3]
            o = (base + x) * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a


def _decode_alpha_block(data: bytes, p: int) -> list:
    a0, a1 = data[p], data[p + 1]
    table = _alpha_table(a0, a1)
    bits = int.from_bytes(data[p + 2:p + 8], "little")
    return [table[(bits >> (i * 3)) & 7] for i in range(16)]


BLOCK_BYTES = {"DXT1": 8, "DXT3": 16, "DXT5": 16, "ATI2": 16}


def _decode_dxt(data: bytes, off: int, w: int, h: int, kind: str,
                step: int = 1) -> tuple[int, int, bytes]:
    """解 DXT 块。

    `step > 1` 时**每 step 个块只解一个**，直接得到缩小的图——不是"先全解再缩"。
    DXT 本来就是 4×4 块压缩，跳块等于免费降采样：1024² 请求 256px 时
    只解 1/16 的块，耗时从 487 ms 降到 ~35 ms。

    返回 (输出宽, 输出高, RGBA)。尺寸是 4 的倍数，可能略大于请求值。
    """
    bw, bh = (w + 3) // 4, (h + 3) // 4
    ow = ((bw + step - 1) // step) * 4
    oh = ((bh + step - 1) // step) * 4
    out = bytearray(ow * oh * 4)
    stride = BLOCK_BYTES.get(kind)
    if stride is None:
        raise DdsError(f"不支持的压缩格式 {kind}")

    for oby in range(0, bh, step):
        for obx in range(0, bw, step):
            ox, oy = obx // step, oby // step
            p = off + (oby * bw + obx) * stride

            if kind == "DXT1":
                _decode_color_block(data, p, out, ow, oh, ox, oy, True)

            elif kind == "DXT3":
                abits = int.from_bytes(data[p:p + 8], "little")
                _decode_color_block(data, p + 8, out, ow, oh, ox, oy, False)
                for py in range(4):
                    y = oy * 4 + py
                    if y >= oh:
                        continue
                    for px in range(4):
                        x = ox * 4 + px
                        if x >= ow:
                            continue
                        v = (abits >> ((py * 4 + px) * 4)) & 0xF
                        out[(y * ow + x) * 4 + 3] = v * 17

            elif kind == "DXT5":
                alphas = _decode_alpha_block(data, p)
                _decode_color_block(data, p + 8, out, ow, oh, ox, oy, False)
                for py in range(4):
                    y = oy * 4 + py
                    if y >= oh:
                        continue
                    for px in range(4):
                        x = ox * 4 + px
                        if x >= ow:
                            continue
                        out[(y * ow + x) * 4 + 3] = alphas[py * 4 + px]

            else:  # ATI2
                rs = _decode_alpha_block(data, p)
                gs = _decode_alpha_block(data, p + 8)
                for py in range(4):
                    y = oy * 4 + py
                    if y >= oh:
                        continue
                    for px in range(4):
                        x = ox * 4 + px
                        if x >= ow:
                            continue
                        i = py * 4 + px
                        r, g = rs[i], gs[i]
                        nx = r / 127.5 - 1.0
                        ny = g / 127.5 - 1.0
                        nz2 = 1.0 - nx * nx - ny * ny
                        nz = (nz2 ** 0.5) if nz2 > 0 else 0.0
                        o = (y * ow + x) * 4
                        out[o] = r
                        out[o + 1] = g
                        out[o + 2] = int((nz * 0.5 + 0.5) * 255)
                        out[o + 3] = 255

    return ow, oh, bytes(out)


def _decode_raw(data: bytes, off: int, w: int, h: int, bits: int,
                masks: tuple) -> bytes:
    rm, gm, bm, am = masks
    step = max(1, bits // 8)
    out = bytearray(w * h * 4)
    for i in range(w * h):
        p = off + i * step
        if step >= 4:
            v = int.from_bytes(data[p:p + step], "little")
        else:
            v = int.from_bytes(data[p:p + step], "little")
        for ch, mask in enumerate((rm, gm, bm, am)):
            if mask and ch < 4:
                shift = (mask & -mask).bit_length() - 1
                width = bin(mask).count("1")
                raw = (v & mask) >> shift
                out[i * 4 + ch] = (raw * 255) // ((1 << width) - 1)
        if not am:
            out[i * 4 + 3] = 255
    return bytes(out)


def decode(data: bytes, max_size: int = 0) -> tuple[int, int, bytes]:
    """返回 (width, height, RGBA bytes)。只解第一层（最大 mip）。

    `max_size > 0` 时按块跳步解码，直接产出不超过该尺寸的图（尺寸为 4 的倍数）。
    给前端缩略图用时能快一个数量级：1024² → 256 从 487 ms 降到 ~35 ms。
    """
    h = _u32(data, 12)
    w = _u32(data, 16)
    fourcc = data[84:88]
    bits = _u32(data, 88)
    off = HEADER_SIZE

    if fourcc in (b"DXT1", b"DXT3", b"DXT5", b"ATI2", b"BC5U"):
        kind = "ATI2" if fourcc in (b"ATI2", b"BC5U") else fourcc.decode()
        step = 1
        if max_size > 0:
            while max(w, h) // (step * 2) >= max_size:
                step *= 2
        return _decode_dxt(data, off, w, h, kind, step)
    if fourcc == b"\x00\x00\x00\x00" and bits > 0:
        masks = (_u32(data, 92), _u32(data, 96), _u32(data, 100), _u32(data, 104))
        return w, h, _decode_raw(data, off, w, h, bits, masks)
    raise DdsError(f"不支持的格式 fourcc={fourcc!r} bits={bits}")


# ---------------------------------------------------------------- 缩放 / PNG
def downscale(w: int, h: int, rgba: bytes, max_size: int) -> tuple[int, int, bytes]:
    """整数倍盒式降采样，保持长宽比。比 max_size 还小就原样返回。"""
    if max(w, h) <= max_size:
        return w, h, rgba
    factor = 1
    while max(w, h) // (factor * 2) >= max_size:
        factor *= 2
    nw, nh = w // factor, h // factor
    out = bytearray(nw * nh * 4)
    area = factor * factor
    for y in range(nh):
        sy = y * factor
        for x in range(nw):
            sx = x * factor
            r = g = b = a = 0
            for dy in range(factor):
                row = (sy + dy) * w
                for dx in range(factor):
                    o = (row + sx + dx) * 4
                    r += rgba[o]
                    g += rgba[o + 1]
                    b += rgba[o + 2]
                    a += rgba[o + 3]
            o2 = (y * nw + x) * 4
            out[o2] = r // area
            out[o2 + 1] = g // area
            out[o2 + 2] = b // area
            out[o2 + 3] = a // area
    return nw, nh, bytes(out)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def encode_png(w: int, h: int, rgba: bytes) -> bytes:
    """RGBA → PNG。每行前面加 filter byte 0（None）。"""
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def average_color(w: int, h: int, rgba: bytes) -> tuple:
    """取样求平均色，贴图缺失时用它兜底，比纯灰好看。"""
    step = max(1, (w * h) // 4096)
    r = g = b = n = 0
    for i in range(0, w * h, step):
        o = i * 4
        if rgba[o + 3] < 32:
            continue
        r += rgba[o]
        g += rgba[o + 1]
        b += rgba[o + 2]
        n += 1
    if not n:
        return (128, 128, 128)
    return (r // n, g // n, b // n)
