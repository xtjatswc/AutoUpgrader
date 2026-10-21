"""客户端同步引擎.

流程:
1. 拉取 manifest, 验签
2. diff (服务端清单 vs 本地 snapshot)
3. 备份受影响文件
4. 下载差异 → 临时目录 → 校验 sha256 → 原子 rename
5. 删除 to_delete 文件
6. 写新 snapshot, 记录版本
7. 钩子 post_replace
8. 健康检查, 失败 → 回滚 (恢复文件 + 旧 snapshot + 钩子 pre)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..crypto import verify_manifest
from ..manifest import FileEntry, Manifest, Snapshot, diff_manifest
from .backup import BackupManager
from .health import run_health
from .httpclient import Transport


@dataclass
class SyncResult:
    version: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    ok: bool = True
    rolled_back: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "ok": self.ok,
            "rolled_back": self.rolled_back,
            "error": self.error,
        }


class SyncEngine:
    def __init__(
        self,
        cfg,
        logger: logging.Logger,
        transport: Optional[Transport] = None,
        backup_manager: Optional[BackupManager] = None,
    ):
        self.cfg = cfg
        self.logger = logger
        self.transport = transport
        self.backup = backup_manager or BackupManager(cfg, logger)

    # ---- 工具 ----
    def _resolve(self, rel: str) -> str:
        return os.path.join(self.cfg.target_dir, rel.replace("/", os.sep))

    def _load_pubkey(self) -> str:
        if not self.cfg.public_key:
            return ""
        p = self.cfg.public_key
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    # ---- 拉取 ----
    def fetch_manifest(self) -> Manifest:
        assert self.transport is not None
        base = self.cfg.server_url.rstrip("/")
        url = f"{base}/api/v1/manifest"
        data = self.transport.get_json(url, self.cfg.token)
        m = Manifest.from_dict(data)
        # 验签
        if self.cfg.verify_sign:
            pub = self._load_pubkey()
            if pub:
                ok = verify_manifest(pub, m.files_json(), m.version, m.published_at, m.sign)
                if not ok:
                    self.logger.error("manifest signature invalid, abort")
                    raise ValueError("manifest signature verification failed")
            else:
                self.logger.warning("verify_sign enabled but no public key, skipping check")
        return m

    # ---- 同步主流程 ----
    def sync(self) -> SyncResult:
        assert self.transport is not None
        t0 = time.time()
        snap = Snapshot(self.cfg.snapshot_path)
        server_files_dict: Dict[str, FileEntry] = {}

        m = self.fetch_manifest()
        server_files_dict = {e.rel: e for e in m.files}

        client_snap = snap.load()
        diff = diff_manifest(server_files_dict, client_snap)

        result = SyncResult(version=m.version)
        result.unchanged = diff.unchanged

        if diff.empty():
            self.logger.info("no diff, already up to date version=%s", m.version)
            snap.save(m.version, client_snap, m.published_at)
            return result

        affected = diff.files_to_download()
        # 备份受影响文件 (以当前本地版本命名, 便于回滚到该版本)
        current_version = snap.current_version() or "v0"
        backup_root = self.backup.backup(current_version, affected + [FileEntry(rel=r, sha256="", size=0, mtime=0) for r in diff.to_delete], snap)

        # 钩子 pre
        self._run_hook(self.cfg.hooks_pre_replace, "pre_replace")

        # 下载 + 校验 + 原子替换
        for e in diff.to_add:
            self._apply_file(e, expect_exists=False)
            result.added += 1
        for e in diff.to_update:
            self._apply_file(e, expect_exists=True)
            result.updated += 1
        for rel in diff.to_delete:
            p = self._resolve(rel)
            if os.path.isfile(p):
                os.remove(p)
                result.deleted += 1
            # 清理空目录
            self._prune_empty_parents(rel)

        # 写新 snapshot
        new_snap: Dict[str, FileEntry] = dict(server_files_dict)
        snap.save(m.version, new_snap, m.published_at)
        self.logger.info(
            "applied version=%s +%d ~%d -%d unchanged=%d",
            m.version, result.added, result.updated, result.deleted, result.unchanged,
        )

        # 钩子 post
        self._run_hook(self.cfg.hooks_post_replace, "post_replace")

        # 健康检查
        healthy = run_health(self.cfg, self.logger)
        if not healthy:
            self.logger.error("health check failed, rolling back version=%s", m.version)
            self._rollback(backup_root, affected + [FileEntry(rel=r, sha256="", size=0, mtime=0) for r in diff.to_delete], snap, m.version)
            result.rolled_back = True
            result.ok = False
            result.error = "health check failed, rolled back"
        else:
            self.logger.info(
                "sync done version=%s added=%d updated=%d deleted=%d unchanged=%d in %.2fs",
                m.version, result.added, result.updated, result.deleted, result.unchanged,
                time.time() - t0,
            )
        return result

    def _apply_file(self, e: FileEntry, expect_exists: bool) -> None:
        assert self.transport is not None
        base = self.cfg.server_url.rstrip("/")
        url = f"{base}/api/v1/files"
        dst = self._resolve(e.rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".upgrader_tmp"

        last_err: Optional[Exception] = None
        for attempt in range(1, self.cfg.retries + 1):
            try:
                data, status, etag = self.transport.get_bytes(url, self.cfg.token, {"rel": e.rel})
                if status in (200, 201, 202, 203, 204, 304, 305, 306, 307, 308, 310, 314):
                    with open(tmp, "wb") as f:
                        f.write(data)
                break
            except Exception as ex:
                last_err = ex
                self.logger.warning("download %s attempt %d failed: %s", e.rel, attempt, ex)
                time.sleep(0.2 * attempt)
        if last_err:
            self.logger.error("download %s failed after %d retries", e.rel, self.cfg.retries)
            raise RuntimeError(f"download failed for {e.rel}: {last_err}")

        # 校验 sha256
        if not verify_file_hash_local(tmp, e.sha256):
            self.logger.error("hash mismatch for %s expected=%s", e.rel, e.sha256)
            raise RuntimeError(f"hash mismatch for {e.rel}")

        # 原子替换
        os.replace(tmp, dst)
        self.logger.debug("applied %s (%d bytes)", e.rel, e.size)

    def _prune_empty_parents(self, rel: str) -> None:
        d = self._resolve(os.path.dirname(rel) or ".")
        target = self.cfg.target_dir
        # 向上清理空目录, 不越过 target_dir
        while d and d != target and d.startswith(target):
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                parent = os.path.dirname(d)
                if not parent or parent == d:
                    break
                d = parent
            else:
                break

    def _run_hook(self, cmd: str, phase: str) -> None:
        if not cmd:
            return
        self.logger.info("hook %s: %r", phase, cmd)
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                self.logger.warning("hook %s rc=%s stderr=%s", phase, r.returncode, r.stderr.strip()[:300])
            else:
                self.logger.debug("hook %s ok", phase)
        except Exception as e:
            self.logger.error("hook %s error: %s", phase, e)

    def _rollback(self, backup_root: str, affected: List[FileEntry], snap: Snapshot, version: str) -> None:
        self.logger.warning("rolling back version=%s", version)
        self._run_hook(self.cfg.hooks_pre_replace, "rollback_pre")
        restored = self.backup.restore_files(backup_root, affected)
        self.backup.restore_snapshot(backup_root, snap)
        self._run_hook(self.cfg.hooks_post_replace, "rollback_post")
        self.logger.warning("rollback done, restored %d files from %s", restored, backup_root)


def verify_file_hash_local(path: str, expected: str) -> bool:
    from .verify import verify_file_hash
    return verify_file_hash(path, expected)
