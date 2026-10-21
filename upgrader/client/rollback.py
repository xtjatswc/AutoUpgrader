"""客户端显式回滚 (到指定备份版本)."""
from __future__ import annotations

import logging
import os

from ..manifest import FileEntry, Snapshot
from .backup import BackupManager
from .health import run_health
from .hooks import run_hook


def rollback_version(
    cfg,
    logger: logging.Logger,
    version: str,
) -> dict:
    """回滚到指定备份版本, 恢复到该版本时的完整目录状态.

    full 备份: 全量恢复 (含删除同步后新增的文件);
    增量备份 (旧版本创建的备份): 仅恢复备份中存在的文件.
    回滚前后执行 pre/post 钩子, 并做健康检查 (结果记录在 healthy 字段).
    返回 {restored, deleted, snapshot_restored, backup_root, healthy}.
    """
    backup = BackupManager(cfg, logger)
    root = backup.backup_root_for(version)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"no backup for version {version} (looked in {root})")

    snap = Snapshot(cfg.snapshot_path)
    run_hook(cfg.hooks_pre_replace, "rollback_pre_replace", logger)

    if backup.is_full_backup(root):
        restored, deleted = backup.restore_state(root)
    else:
        # 旧格式备份: 遍历备份 files 目录恢复
        files: list[FileEntry] = []
        files_dir = os.path.join(root, "files")
        if os.path.isdir(files_dir):
            for rootdir, _dirs, fnames in os.walk(files_dir):
                for fn in fnames:
                    full = os.path.join(rootdir, fn)
                    rel = os.path.relpath(full, files_dir).replace(os.sep, "/")
                    files.append(FileEntry(rel=rel, sha256="", size=0, mtime=0))
        restored = backup.restore_files(root, files)
        deleted = 0

    backup.restore_snapshot(root, snap)
    run_hook(cfg.hooks_post_replace, "rollback_post_replace", logger)

    healthy = run_health(cfg, logger)
    if not healthy:
        logger.warning("health check failed after rollback to %s", version)
    logger.info("rollback to %s: restored %d, deleted %d, snapshot restored",
                version, restored, deleted)
    return {
        "restored": restored,
        "deleted": deleted,
        "snapshot_restored": True,
        "backup_root": root,
        "healthy": healthy,
    }
