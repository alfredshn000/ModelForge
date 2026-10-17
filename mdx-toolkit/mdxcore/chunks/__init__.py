"""chunk 解析器集合。

新增一个 chunk 时，在这里登记并导出，其它地方不直接依赖具体模块。
"""

from . import texs

__all__ = ["texs"]
