"""客户端备份 / 回滚.

- 备份: 受影响文件 + 旧 snapshot 拷贝到 backup_dir/<version>/
- 回滚: 从 backup_dir/<version>/ 恢复受影响文件, 并恢复旧 snapshot.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Dict, List, Optional

from ..manifest import FileEntry, Snapshot


class BackupManager:
    def __init__(self, cfg, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        os.makedirs(cfg.backup_dir, exist_ok=True)

    def backup_root_for(self, version: str) -> str:
        safe = "".join(c for c in version if c.isalnum() or c in "-._") or "v0"
        return os.path.join(self.cfg.backup_dir, safe)

    def backup(self, target_version: str, files: List[FileEntry], snap: Snapshot) -> str:
        """备份将被修改/删除的受影响文件 + 当前 snapshot. 返回 backup root."""
        root = self.backup_root_for(target_version)
        files_dir = os.path.join(root, "files")
        os.makedirs(files_dir, exist_ok=True)
        snap_dir = os.path.join(root, "snapshot")
        os.makedirs(snap_dir, exist_ok=True)

        for e in files:
            src = os.path.join(self.cfg.target_dir, e.rel.replace("/", os.sep))
            dst = os.path.join(files_dir, e.rel.replace("/", os.sep))
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

        # 保存旧 snapshot
        old_snap_path = snap.path
        if os.path.exists(old_snap_path):
            shutil.copy2(old_snap_path, os.path.join(snap_dir, "snapshot.json"))
        self.logger.info("backup done root=%s files=%d", root, len(files))
        return root

    def restore_files(self, backup_root: str, files: List[FileEntry]) -> int:
        """从 backup_root 恢复受影响文件. 返回恢复数量."""
        files_dir = os.path.join(backup_root, "files")
        restored = 0
        for e in files:
            src = os.path.join(files_dir, e.rel.replace("/", os.sep))
            dst = os.path.join(self.cfg.target_dir, e.rel.replace("/", os.sep))
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored += 1
            else:
                # 服务端无此文件 (to_add 情况) 或备份无 → 删除本地
                if os.path.isfile(dst):
                    os.remove(dst)
        return restored

    def restore_snapshot(self, backup_root: str, snap: Snapshot) -> None:
        snap_dir = os.path.join(backup_root, "snapshot")
        old = os.path.join(snap_dir, "snapshot.json")
        if os.path.exists(old):
            os.makedirs(os.path.dirname(snap.path), exist_ok=True)
            shutil.copy2(old, snap.path)

    def list_versions(self) -> List[str]:
        base = self.cfg.backup_dir
        if not os.path.isdir(base):
            return []
        out = []
        for d in os.listdir(base):
            p = os.path.join(base, d)
            if os.path.isdir(p):
                out.append(d)
        return sorted(out, reverse=True)
