"""让 `import mdxcore` 可用，并暴露最常用入口。"""

from .container import (
    Chunk,
    MdxFile,
    MdxFormatError,
    load,
    parse,
    rebuild,
)
from .diff import DiffRange, diff_ranges, locate

__all__ = [
    "Chunk",
    "MdxFile",
    "MdxFormatError",
    "load",
    "parse",
    "rebuild",
    "DiffRange",
    "diff_ranges",
    "locate",
]
