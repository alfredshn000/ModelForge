"""定点写入器：在 chunk 的指定偏移覆写定长字段，并记录每处改动的绝对范围。

为什么不用 `ops.set_*`：那些函数是"一次调用返回整个 chunk 的新 bytes"，
每次都从原始数据重算，连续调用会互相覆盖（第二次把第一次的结果丢掉）。
批量改材质 / 贴图时动辄几十处，需要先攒、后一次性交出去。

用法：

    ed = ChunkEditor(mdx)
    ed.write("TEXS", i * 268 + 4, "<256s", raw)
    ed.write("MTLS", layer.record_offset + 24, "<f", alpha)
    replacements = ed.apply()          # {chunk 序号: 新 bytes}
    spans = ed.abs_spans()             # 文件绝对偏移，交给 check_delta

所有字段都是定长的，所以文件长度不会变——这正是能用 `check_delta`
（"只允许这些字节变化"）而不是 `check_chunk_rewrite` 的前提。
"""

from __future__ import annotations

import struct

from .container import MdxFile


class ChunkEditor:
    def __init__(self, mdx: MdxFile):
        self.mdx = mdx
        self._index: dict[str, int] = {}
        self._buffers: dict[int, bytearray] = {}
        self._inner: dict[int, list[tuple[int, int]]] = {}
        self._notes: list[str] = []

    # ---- 定位 ----

    def chunk_index(self, tag: str) -> int:
        if tag not in self._index:
            hits = [i for i, c in enumerate(self.mdx.chunks) if c.tag == tag]
            if not hits:
                raise KeyError(f"模型里没有 {tag} 块")
            if len(hits) > 1:
                raise KeyError(f"模型里有 {len(hits)} 个 {tag} 块，暂不支持")
            self._index[tag] = hits[0]
        return self._index[tag]

    def has(self, tag: str) -> bool:
        try:
            self.chunk_index(tag)
            return True
        except KeyError:
            return False

    def _buf(self, tag: str) -> bytearray:
        idx = self.chunk_index(tag)
        buf = self._buffers.get(idx)
        if buf is None:
            buf = bytearray(self.mdx.data(self.mdx.chunks[idx]))
            self._buffers[idx] = buf
        return buf

    # ---- 写 ----

    def write(self, tag: str, offset: int, fmt: str, *values) -> None:
        """在 chunk 内 `offset` 处按 `fmt` 打包写入，并记录覆盖的字节范围。"""
        buf = self._buf(tag)
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(buf):
            raise ValueError(
                f"{tag} 写入越界：偏移 {offset}+{size} 超出块长度 {len(buf)}")
        # 先打包到临时缓冲区：pack_into 失败时不会留下半截数据
        payload = struct.pack(fmt, *values)
        buf[offset:offset + size] = payload
        idx = self.chunk_index(tag)
        self._inner.setdefault(idx, []).append((offset, offset + size))

    def write_raw(self, tag: str, offset: int, payload: bytes) -> None:
        """写入一段已编码好的字节（如 256 字节的路径字段）。"""
        buf = self._buf(tag)
        if offset < 0 or offset + len(payload) > len(buf):
            raise ValueError(
                f"{tag} 写入越界：偏移 {offset}+{len(payload)} 超出块长度 {len(buf)}")
        buf[offset:offset + len(payload)] = payload
        idx = self.chunk_index(tag)
        self._inner.setdefault(idx, []).append((offset, offset + len(payload)))

    def note(self, text: str) -> None:
        self._notes.append(text)

    # ---- 交付 ----

    def apply(self) -> dict[int, bytes]:
        """返回 `{chunk 序号: 新 bytes}`；没改过的 chunk 不出现。"""
        return {idx: bytes(buf) for idx, buf in self._buffers.items()}

    def abs_spans(self) -> list[tuple[int, int]]:
        """每处改动的文件绝对偏移范围，供 `check_delta` 断言。"""
        out: list[tuple[int, int]] = []
        for idx, spans in self._inner.items():
            base = self.mdx.chunks[idx].data_offset
            out.extend((base + lo, base + hi) for lo, hi in spans)
        return out

    def touched(self) -> set[str]:
        return {self.mdx.chunks[i].tag for i in self._buffers}

    def touched_bytes(self) -> int:
        return sum(hi - lo for spans in self._inner.values() for lo, hi in spans)

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    def __bool__(self) -> bool:
        return bool(self._buffers)


def pack_path_field(path: str, width: int = 256) -> bytes:
    """把路径编码成定长 NUL 填充字段（TEXS 的 path[256]）。"""
    raw = (path or "").encode("latin-1")
    if len(raw) > width - 1:
        raise ValueError(f"路径超过 {width - 1} 字节：{path!r}")
    return raw.ljust(width, b"\x00")
