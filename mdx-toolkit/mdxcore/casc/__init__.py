"""只读 CASC（WC3 Reforged 的游戏数据容器）。

    from mdxcore.casc import open_casc, find_install

    install = find_install()                      # 自动探测
    with open_casc(install) as casc:
        data = casc.read("units\\\\humanfootman\\\\footman.mdx")
        for path in casc.find(r"footman.*\\.mdx"):
            print(path)

只支持 Reforged（CASC 存储）。经典版的 MPQ 不在范围内。
"""

from .storage import (
    BuildInfo,
    Casc,
    CascError,
    deepest_install,
    find_install,
    open_casc,
)

__all__ = [
    "Casc",
    "CascError",
    "BuildInfo",
    "open_casc",
    "find_install",
    "deepest_install",
]
