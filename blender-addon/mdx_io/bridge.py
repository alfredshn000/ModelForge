"""定位并导入 mdxcore。

插件本身不复制解析代码，而是复用 `mdx-toolkit/mdxcore`，
查找顺序：
1. 环境变量 MDX_TOOLKIT
2. 插件目录往上两级（即工作区根）下的 mdx-toolkit
3. 用户目录下的 mdx-toolkit
4. 已经能 import 就直接用
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 工作区根目录：<workspace>/blender-addon/mdx_io/bridge.py 往上三级
# 所有中间产物都落在工作区内，不写系统临时目录。
WORKSPACE = Path(__file__).resolve().parents[2]


def _candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("MDX_TOOLKIT")
    if env:
        out.append(Path(env))
    here = Path(__file__).resolve()
    # __file__ = <workspace>/blender-addon/mdx_io/bridge.py
    out.append(here.parents[2] / "mdx-toolkit")
    out.append(here.parents[1] / "mdx-toolkit")
    out.append(Path.home() / "mdx-toolkit")
    return out


def get_core():
    try:
        import mdxcore  # noqa: F401
        return mdxcore
    except ImportError:
        pass

    for path in _candidates():
        if (path / "mdxcore" / "__init__.py").is_file():
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
            import mdxcore  # noqa: F811
            return mdxcore

    tried = "\n".join(f"  - {p}" for p in _candidates())
    raise ImportError(
        "找不到 mdxcore。请设置环境变量 MDX_TOOLKIT 指向 mdx-toolkit 目录。\n"
        f"已尝试：\n{tried}"
    )
