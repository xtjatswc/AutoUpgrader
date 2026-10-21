"""客户端显式回滚 (到指定备份版本)."""
from __future__ import annotations

import logging
import os

from ..manifest import FileEntry, Snapshot
from .backup import BackupManager


def rollback_version(
    cfg,
    logger: logging.Logger,
    version: str,
) -> dict:
    """回滚到指定备份版本. 返回 {restored, snapshot_restored, backup_root}."""
    backup = BackupManager(cfg, logger)
    root = backup.backup_root_for(version)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"no backup for version {version} (looked in {root})")

    snap = Snapshot(cfg.snapshot_path)

    # 解析备份中的文件清单
    files: list[FileEntry] = []
    files_dir = os.path.join(root, "files")
    if os.path.isdir(files_dir):
        for rootdir, _dirs, fnames in os.walk(files_dir):
            for fn in fnames:
                full = os.path.join(rootdir, fn)
                rel = os.path.relpath(full, files_dir).replace(os.sep, "/")
                files.append(FileEntry(rel=rel, sha256="", size=0, mtime=0))

    restored = backup.restore_files(root, files)
    backup.restore_snapshot(root, snap)
    logger.info("rollback to %s: restored %d files, snapshot restored", version, restored)
    return {
        "restored": restored,
        "snapshot_restored": True,
        "backup_root": root,
    }
