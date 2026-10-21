"""客户端同步引擎.

流程:
1. 获取同步锁 (防止并发 sync 互相破坏)
2. 清理残留暂存目录; 若发现上次同步的 pending 标记 (写入 snapshot 后、健康检查
   完成前崩溃), 先从备份恢复到上次同步前状态
3. 拉取 manifest, 验签 (fail-closed: 开启验签但无公钥直接失败)
4. diff (服务端清单 vs 本地 snapshot)
5. 备份 (backup_full=True 时全量备份, 保证可回滚到任意历史版本)
6. 钩子 pre_replace (stop_on_hook_failure 开启时失败即中止)
7. 下载差异到暂存目录 → 校验 sha256 (失败重试) → 原子 rename
8. 删除 to_delete 文件, 写新 snapshot, 记录 pending 标记
9. 钩子 post_replace
10. 健康检查: 通过 → 清除 pending; 失败 → 回滚 (恢复文件 + 旧 snapshot + 钩子) 并清除 pending
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..manifest import FileEntry, Manifest, Snapshot, diff_manifest
from .backup import STAGING_DIRNAME, BackupManager
from .health import run_health
from .hooks import run_hook
from .httpclient import Transport
from .verify import SignVerifier, verify_file_hash


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


class SyncLock:
    """基于 O_CREAT|O_EXCL 的进程互斥锁 (跨平台), 带过期自动清理."""

    def __init__(self, path: str, stale_seconds: int = 3600):
        self.path = path
        self.stale_seconds = stale_seconds
        self._held = False

    def acquire(self) -> bool:
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self._held = True
                return True
            except FileExistsError:
                if self._is_stale():
                    try:
                        os.remove(self.path)
                    except OSError:
                        return False
                    continue
                return False

    def _is_stale(self) -> bool:
        try:
            return time.time() - os.path.getmtime(self.path) > self.stale_seconds
        except OSError:
            return False

    def release(self) -> None:
        if self._held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self._held = False


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
        # verify_sign 开启但无可用公钥时在此直接失败 (fail-closed)
        self.sign_verifier = SignVerifier(cfg.public_key, logger, verify_sign=cfg.verify_sign)

    # ---- 工具 ----
    def _resolve(self, rel: str) -> str:
        return os.path.join(self.cfg.target_dir, rel.replace("/", os.sep))

    @property
    def staging_dir(self) -> str:
        return os.path.join(self.cfg.target_dir, STAGING_DIRNAME)

    @property
    def lock_path(self) -> str:
        return os.path.join(self.cfg.snapshot_dir, ".sync.lock")

    @property
    def pending_path(self) -> str:
        return os.path.join(self.cfg.snapshot_dir, "pending.json")

    # ---- 拉取 ----
    def fetch_manifest(self) -> Manifest:
        assert self.transport is not None
        base = self.cfg.server_url.rstrip("/")
        url = f"{base}/api/v1/manifest"
        data = self.transport.get_json(url, self.cfg.token)
        m = Manifest.from_dict(data)
        if not self.sign_verifier.verify(m.files_json(), m.version, m.published_at, m.sign):
            raise ValueError("manifest signature verification failed")
        return m

    # ---- pending 崩溃恢复 ----
    def _write_pending(self, version: str, backup_root: str, affected_rels: List[str]) -> None:
        d = os.path.dirname(self.pending_path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.pending_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": version, "backup_root": backup_root,
                       "affected": affected_rels}, f, ensure_ascii=False)
        os.replace(tmp, self.pending_path)

    def _clear_pending(self) -> None:
        try:
            os.remove(self.pending_path)
        except OSError:
            pass

    def _recover_pending(self, snap: Snapshot) -> None:
        """上次同步在写 snapshot 之后、健康检查完成之前崩溃 → 恢复到同步前状态."""
        if not os.path.exists(self.pending_path):
            return
        try:
            with open(self.pending_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception as e:
            self.logger.error("pending marker unreadable, removing: %s", e)
            self._clear_pending()
            return
        root = info.get("backup_root") or ""
        version = info.get("version") or "?"
        if not root or not os.path.isdir(root):
            raise RuntimeError(
                f"previous sync of {version} crashed before health check and its backup "
                f"is missing ({root}); run `upgrader rollback` manually"
            )
        self.logger.error(
            "detected unfinished sync of version=%s (crashed before health check); "
            "restoring pre-sync state from %s", version, root,
        )
        affected = [FileEntry(rel=r, sha256="", size=0, mtime=0) for r in info.get("affected", [])]
        self._restore_backup(root, affected)
        self.backup.restore_snapshot(root, snap)
        self._clear_pending()
        self.logger.warning("pending sync recovered from backup %s", root)

    def _restore_backup(self, backup_root: str, affected: List[FileEntry]) -> None:
        """full 备份 → 全状态恢复; 增量备份 → 仅恢复受影响文件."""
        if self.backup.is_full_backup(backup_root):
            self.backup.restore_state(backup_root)
        else:
            self.backup.restore_files(backup_root, affected)

    # ---- 同步主流程 ----
    def sync(self) -> SyncResult:
        assert self.transport is not None
        os.makedirs(self.cfg.snapshot_dir, exist_ok=True)
        lock = SyncLock(self.lock_path)
        if not lock.acquire():
            raise RuntimeError(f"another sync is already running (lock: {self.lock_path})")
        try:
            return self._sync_locked()
        finally:
            shutil.rmtree(self.staging_dir, ignore_errors=True)
            lock.release()

    def _sync_locked(self) -> SyncResult:
        t0 = time.time()
        shutil.rmtree(self.staging_dir, ignore_errors=True)
        snap = Snapshot(self.cfg.snapshot_path)
        self._recover_pending(snap)

        m = self.fetch_manifest()
        server_files_dict: Dict[str, FileEntry] = {e.rel: e for e in m.files}

        client_snap = snap.load()
        diff = diff_manifest(server_files_dict, client_snap)

        result = SyncResult(version=m.version)
        result.unchanged = diff.unchanged

        if diff.empty():
            if snap.current_version() == m.version:
                self.logger.info("no diff, already up to date version=%s", m.version)
                return result
            self.logger.info("no diff, updating recorded version to %s", m.version)
            snap.save(m.version, client_snap, m.published_at)
            return result

        affected = diff.files_to_download()
        # 备份 (以当前本地版本命名, 便于回滚到该版本)
        current_version = snap.current_version() or "v0"
        full = bool(getattr(self.cfg, "backup_full", False))
        if full:
            # 全量备份: 当前 snapshot 的全部文件, 保证回滚到任意历史版本时状态一致
            backup_files = list(client_snap.values())
        else:
            backup_files = affected + [FileEntry(rel=r, sha256="", size=0, mtime=0)
                                       for r in diff.to_delete]
        backup_root = self.backup.backup(current_version, backup_files, snap, full=full)

        # 钩子 pre
        if not run_hook(self.cfg.hooks_pre_replace, "pre_replace", self.logger):
            if getattr(self.cfg, "stop_on_hook_failure", False):
                raise RuntimeError("pre_replace hook failed, sync aborted (stop_on_hook_failure=true)")
            self.logger.warning("pre_replace hook failed, continuing sync")

        # 下载 + 校验 + 原子替换
        for e in diff.to_add:
            self._apply_file(e)
            result.added += 1
        for e in diff.to_update:
            self._apply_file(e)
            result.updated += 1
        for rel in diff.to_delete:
            p = self._resolve(rel)
            if os.path.isfile(p):
                os.remove(p)
                result.deleted += 1
            # 清理空目录
            self._prune_empty_parents(rel)

        # 写新 snapshot + pending 标记 (健康检查通过后清除)
        new_snap: Dict[str, FileEntry] = dict(server_files_dict)
        snap.save(m.version, new_snap, m.published_at)
        self._write_pending(
            m.version, backup_root,
            [e.rel for e in affected] + list(diff.to_delete),
        )
        self.logger.info(
            "applied version=%s +%d ~%d -%d unchanged=%d",
            m.version, result.added, result.updated, result.deleted, result.unchanged,
        )

        # 钩子 post
        run_hook(self.cfg.hooks_post_replace, "post_replace", self.logger)

        # 健康检查
        healthy = run_health(self.cfg, self.logger)
        if not healthy:
            self.logger.error("health check failed, rolling back version=%s", m.version)
            self._rollback(backup_root, affected, snap, m.version)
            self._clear_pending()
            result.rolled_back = True
            result.ok = False
            result.error = "health check failed, rolled back"
        else:
            self._clear_pending()
            self.logger.info(
                "sync done version=%s added=%d updated=%d deleted=%d unchanged=%d in %.2fs",
                m.version, result.added, result.updated, result.deleted, result.unchanged,
                time.time() - t0,
            )
        return result

    def _apply_file(self, e: FileEntry) -> None:
        assert self.transport is not None
        base = self.cfg.server_url.rstrip("/")
        url = f"{base}/api/v1/files"
        dst = self._resolve(e.rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = os.path.join(self.staging_dir, e.rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(tmp), exist_ok=True)

        last_err: Optional[Exception] = None
        for attempt in range(1, self.cfg.retries + 1):
            try:
                status = self.transport.get_file(url, self.cfg.token, {"rel": e.rel}, tmp)
                if status != 200:
                    raise RuntimeError(f"unexpected HTTP status {status}")
                if not verify_file_hash(tmp, e.sha256):
                    raise RuntimeError(f"hash mismatch (expected {e.sha256})")
                break
            except Exception as ex:
                last_err = ex
                self.logger.warning("download %s attempt %d/%d failed: %s",
                                    e.rel, attempt, self.cfg.retries, ex)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                time.sleep(0.2 * attempt)
        if last_err:
            self.logger.error("download %s failed after %d attempts", e.rel, self.cfg.retries)
            raise RuntimeError(f"download failed for {e.rel}: {last_err}")

        # 原子替换 (暂存目录与 target_dir 同卷, rename 原子生效)
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

    def _rollback(self, backup_root: str, affected: List[FileEntry], snap: Snapshot, version: str) -> None:
        self.logger.warning("rolling back version=%s", version)
        run_hook(self.cfg.hooks_pre_replace, "rollback_pre", self.logger)
        self._restore_backup(backup_root, affected)
        self.backup.restore_snapshot(backup_root, snap)
        run_hook(self.cfg.hooks_post_replace, "rollback_post", self.logger)
        self.logger.warning("rollback done from %s", backup_root)
