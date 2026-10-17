"""结构变更的落盘通道：校验 → 备份 → 写文件 → 重新导入。

凡是会改变块长度或 chunk 序列的操作（动画移植、附着点增删）都必须走这里。
只改场景状态是不够的：下一次导出会拿 Blender 里的旧数据去比对，
把刚改的结构又写回去。所以文件必须立刻成为新的真相，场景跟着重建。
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy


class CommitError(RuntimeError):
    """写入前置条件不满足，或校验没通过。"""


def target_path() -> Path:
    meta = bpy.context.scene.get("mdx_meta")
    if not meta:
        raise CommitError("当前场景没有 MDX 模型，请先导入")
    path = Path(json.loads(meta)["source_path"])
    if not path.is_file():
        raise CommitError(f"原始文件不存在：{path}")
    return path


def commit(mdx, replacements: dict, *, allowed=None, reimport: bool = True) -> dict:
    """校验并写出，然后重新导入。

    返回 {"path", "backup", "bytes_before", "bytes_after"}。
    """
    from mdxcore import rebuild
    from mdxcore.verify import check_chunk_rewrite

    dst = target_path()
    before = dst.read_bytes()

    after = rebuild(mdx, replacements)
    tags = allowed or {mdx.chunks[i].tag for i in replacements}
    problems = check_chunk_rewrite(before, after, mdx, tags)
    if problems:
        raise CommitError("校验没通过，已放弃写入：" + "；".join(problems[:3]))

    backup = dst.with_suffix(dst.suffix + ".bak")
    try:
        backup.write_bytes(before)
    except OSError as exc:
        raise CommitError(f"备份失败，未改写：{exc}") from None

    dst.write_bytes(after)

    if reimport:
        from .importer import import_mdx

        try:
            import_mdx(str(dst))
        except Exception as exc:                   # noqa: BLE001
            raise CommitError(
                f"已写入 {dst.name}，但重新导入失败：{exc}（备份在 {backup.name}）") from None

    try:
        bpy.context.scene["mdx_dirty"] = False
    except Exception:                              # noqa: BLE001
        pass

    return {"path": dst, "backup": backup,
            "bytes_before": len(before), "bytes_after": len(after)}
