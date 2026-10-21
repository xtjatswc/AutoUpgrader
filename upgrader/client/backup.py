"""客户端备份 / 回滚.

- 备份: 受影响文件 (或全量) + 旧 snapshot 拷贝到 backup_dir/<version>/,
  并写入 meta.json 标记备份模式.
- 恢复: full 备份按旧 snapshot 全量恢复 (含删除多余文件), 保证目录状态一致;
  增量备份仅恢复备份中存在的受影响文件 (旧行为).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Set, Tuple

from ..manifest import FileEntry, Snapshot

STAGING_DIRNAME = ".upgrader_staging"  # 下载暂存目录, 位于 target_dir 内


class BackupManager:
    def __init__(self, cfg, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        os.makedirs(cfg.backup_dir, exist_ok=True)

    def backup_root_for(self, version: str) -> str:
        safe = "".join(c for c in version if c.isalnum() or c in "-._") or "v0"
        return os.path.join(self.cfg.backup_dir, safe)

    def backup(self, target_version: str, files: List[FileEntry], snap: Snapshot,
               full: bool = False) -> str:
        """备份将被修改/删除的文件 (full=True 时传完整 snapshot 文件集) + 当前 snapshot.

        返回 backup root. 备份目录写入 meta.json: {"version", "full"}.
        """
        root = self.backup_root_for(target_version)
        files_dir = os.path.join(root, "files")
        os.makedirs(files_dir, exist_ok=True)
        snap_dir = os.path.join(root, "snapshot")
        os.makedirs(snap_dir, exist_ok=True)

        copied = 0
        if full:
            # 全量备份: 遍历目标目录备份所有现存文件 (含用户自置文件),
            # 使回滚/崩溃恢复后目录状态与同步前完全一致
            copied = self._copy_all_files(files_dir)
        else:
            for e in files:
                src = os.path.join(self.cfg.target_dir, e.rel.replace("/", os.sep))
                dst = os.path.join(files_dir, e.rel.replace("/", os.sep))
                if os.path.isfile(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1

        # 保存旧 snapshot
        old_snap_path = snap.path
        if os.path.exists(old_snap_path):
            shutil.copy2(old_snap_path, os.path.join(snap_dir, "snapshot.json"))

        meta = {"version": target_version, "full": bool(full)}
        tmp = os.path.join(root, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(root, "meta.json"))

        self.logger.info("backup done root=%s files=%d/%d full=%s",
                         root, copied, len(files), full)
        self._apply_retention(root)
        return root

    def _copy_all_files(self, files_dir: str) -> int:
        """遍历 target_dir 复制全部文件到 files_dir (跳过暂存目录)."""
        target = os.path.abspath(self.cfg.target_dir)
        skipped = {os.path.abspath(os.path.join(target, STAGING_DIRNAME))}
        copied = 0
        for root, dirs, fnames in os.walk(target, topdown=True):
            dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) not in skipped]
            for fn in fnames:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, target).replace(os.sep, "/")
                dst = os.path.join(files_dir, rel.replace("/", os.sep))
                if os.path.isfile(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1
        return copied

    def is_full_backup(self, backup_root: str) -> bool:
        meta = self._load_meta(backup_root)
        return bool(meta and meta.get("full"))

    def _load_meta(self, backup_root: str) -> Optional[dict]:
        p = os.path.join(backup_root, "meta.json")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _apply_retention(self, current_root: str) -> None:
        keep = int(getattr(self.cfg, "backup_keep", 0) or 0)
        if keep <= 0:
            return
        # versions 为"新→旧"且不含本次备份; 共保留 keep 份 (本次 + 最近 keep-1 个旧备份)
        versions = [v for v in self.list_versions()
                    if os.path.abspath(self.backup_root_for(v)) != os.path.abspath(current_root)]
        victims = versions[keep - 1:]
        for v in victims:
            p = self.backup_root_for(v)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                self.logger.info("backup retention removed %s", p)

    def restore_files(self, backup_root: str, files: List[FileEntry]) -> int:
        """从 backup_root 恢复指定文件 (增量备份旧行为). 返回恢复数量.

        备份中不存在的文件按"新增文件"处理: 删除本地目标.
        """
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

    def restore_state(self, backup_root: str) -> Tuple[int, int]:
        """按备份记录的全量 snapshot 完整恢复目录状态 (仅 full 备份).

        - 恢复备份 files/ 目录下的每个文件 (同步前目标目录的完整快照)
        - 删除目标目录中不在备份里的多余文件
        返回 (restored, deleted). 保护 snapshot/backup/staging 目录不被误删.
        """
        files_dir = os.path.join(backup_root, "files")
        # 备份 files/ 目录即同步前目标目录的完整镜像; 全量恢复 = 按它复原
        restored = 0
        keep_rels: Set[str] = set()
        if os.path.isdir(files_dir):
            for root, _dirs, fnames in os.walk(files_dir):
                for fn in fnames:
                    src = os.path.join(root, fn)
                    rel = os.path.relpath(src, files_dir).replace(os.sep, "/")
                    dst = os.path.join(self.cfg.target_dir, rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    keep_rels.add(rel)
                    restored += 1
        deleted = self._delete_extra_files(keep_rels)
        return restored, deleted

    def _protected_paths(self) -> List[str]:
        out = []
        for p in (getattr(self.cfg, "snapshot_dir", ""),
                  getattr(self.cfg, "backup_dir", ""),
                  os.path.join(self.cfg.target_dir, STAGING_DIRNAME)):
            if p:
                out.append(os.path.abspath(p))
        return out

    @staticmethod
    def _under_any(path: str, prefixes: List[str]) -> bool:
        path = os.path.abspath(path)
        for p in prefixes:
            if path == p or path.startswith(p + os.sep):
                return True
        return False

    def _delete_extra_files(self, keep_rels: Set[str]) -> int:
        target = os.path.abspath(self.cfg.target_dir)
        protected = self._protected_paths()
        deleted = 0
        for root, dirs, fnames in os.walk(target, topdown=True):
            dirs[:] = [d for d in dirs if not self._under_any(os.path.join(root, d), protected)]
            for fn in fnames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, target).replace(os.sep, "/")
                if rel not in keep_rels:
                    try:
                        os.remove(full)
                        deleted += 1
                    except OSError:
                        self.logger.warning("failed to remove extra file %s", full)
        self._prune_empty_dirs(target, protected)
        return deleted

    def _prune_empty_dirs(self, target: str, protected: List[str]) -> None:
        for root, dirs, fnames in os.walk(target, topdown=False):
            if os.path.abspath(root) == target:
                continue
            if self._under_any(root, protected):
                continue
            if not dirs and not fnames:
                try:
                    os.rmdir(root)
                except OSError:
                    pass

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
