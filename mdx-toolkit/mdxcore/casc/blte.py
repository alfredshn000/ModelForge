"""BLTE 解码。

BLTE 是暴雪 CASC 的容器格式，一块数据可以被切成多个 bank，每块单独压缩。

    "BLTE"
    u32 BE  headerSize          —— 0 表示单块未压缩
    headerSize 字节:
        u8      flags            —— 固定 0x0F
        u24 BE  chunkCount
        chunkCount × {
            u32 BE  compressedSize
            u32 BE  decompressedSize
            u8[16]  md5
        }
    然后依次是每块数据:
        u8      mode             —— 'N' 原样 / 'Z' zlib / 'F' LZ4（不支持） / 'E' 加密（不支持）
        数据
"""

from __future__ import annotations

import struct
import zlib

MAGIC = b"BLTE"


class BlteError(ValueError):
    """BLTE 结构或编码方式不受支持。"""


def decode(data: bytes) -> bytes:
    """把一段 BLTE 数据解成原始字节。"""
    if len(data) < 8 or data[:4] != MAGIC:
        raise BlteError("缺少 BLTE 标识")

    header_size = struct.unpack_from(">I", data, 4)[0]
    if header_size == 0:
        # Retera 的行为：整段原样返回（单块未压缩）
        return data[8:]

    if header_size > len(data):
        raise BlteError(f"BLTE 头声明结束于 {header_size}，超出数据范围")

    # headerSize 是「头部结束的绝对偏移」，不是长度：
    # 头部区就是 data[8:header_size]，其中前 4 字节是 flags + chunkCount。
    flags = data[8]
    if flags != 0x0F:
        # 只见过 0x0F，其它值不当场拒绝，只影响后续解析
        pass

    count = int.from_bytes(data[9:12], "big")
    if count == 0:
        raise BlteError("BLTE 声明了 0 个块")

    pos = 12
    chunks: list[tuple[int, int]] = []
    for _ in range(count):
        compressed, decompressed = struct.unpack_from(">II", data, pos)
        pos += 8 + 16                     # 跳过 md5
        chunks.append((compressed, decompressed))

    if pos != header_size:
        raise BlteError(f"BLTE 头长度不符：解析到 {pos}，声明 {header_size}")

    out = bytearray()
    for compressed, decompressed in chunks:
        if pos >= len(data):
            raise BlteError("BLTE 块数据提前结束")
        mode = data[pos]
        body = data[pos + 1:pos + compressed]

        if mode == ord("N"):
            if len(body) != decompressed:
                raise BlteError(f"未压缩块长度不符：{len(body)} vs {decompressed}")
            out += body
        elif mode == ord("Z"):
            try:
                raw = zlib.decompress(body)
            except zlib.error as exc:
                raise BlteError(f"zlib 解压失败：{exc}") from None
            if len(raw) != decompressed:
                raise BlteError(f"解压后长度不符：{len(raw)} vs {decompressed}")
            out += raw
        elif mode == ord("F"):
            raise BlteError("该块用 LZ4 压缩（mode 'F'），当前实现不支持")
        elif mode == ord("E"):
            raise BlteError("该块已加密（mode 'E'），需要 keyring，当前实现不支持")
        else:
            raise BlteError(f"未知的块编码模式 {chr(mode)!r}")

        pos += compressed

    return bytes(out)
