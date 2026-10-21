"""备份 / 回滚: 备份受影响文件 + snapshot; 恢复文件与 snapshot."""
from __future__ import annotations

import os

import pytest

from upgrader.client.backup import BackupManager
from upgrader.client.rollback import rollback_version
from upgrader.logcfg import setup_logger
from upgrader.manifest import FileEntry, Snapshot


def test_backup_and_restore(client_cfg):
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    bm = BackupManager(client_cfg, logger)
    snap = Snapshot(client_cfg.snapshot_path)

    # 准备本地文件
    target = client_cfg.target_dir
    os.makedirs(os.path.join(target, "sub"), exist_ok=True)
    with open(os.path.join(target, "a.txt"), "w", encoding="utf-8") as f:
        f.write("v1")
    with open(os.path.join(target, "sub", "b.txt"), "w", encoding="utf-8") as f:
        f.write("v1b")
    snap.save("v1", {"a.txt": FileEntry(rel="a.txt", sha256="h1", size=2, mtime=0)})

    # 备份
    affected = [FileEntry(rel="a.txt", sha256="h1", size=2, mtime=0),
                FileEntry(rel="sub/b.txt", sha256="h2", size=3, mtime=0)]
    root = bm.backup("v2", affected, snap)
    assert os.path.isdir(root)
    assert os.path.isfile(os.path.join(root, "files", "a.txt"))
    assert os.path.isfile(os.path.join(root, "files", "sub", "b.txt"))
    assert os.path.isfile(os.path.join(root, "snapshot", "snapshot.json"))

    # 模拟文件被改动 → 恢复
    with open(os.path.join(target, "a.txt"), "w", encoding="utf-8") as f:
        f.write("v2-changed")
    restored = bm.restore_files(root, affected)
    assert restored == 2
    with open(os.path.join(target, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "v1"
    with open(os.path.join(target, "sub", "b.txt"), encoding="utf-8") as f:
        assert f.read() == "v1b"

    # 恢复 snapshot
    snap2 = Snapshot(client_cfg.snapshot_path)
    # 先改 snapshot
    snap2.save("v2", {"a.txt": FileEntry(rel="a.txt", sha256="h2", size=10, mtime=0)})
    assert snap2.current_version() == "v2"
    bm.restore_snapshot(root, snap2)
    assert snap2.current_version() == "v1"


def test_rollback_version_api(client_cfg):
    """通过 rollback_version 回滚到某备份版本."""
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    bm = BackupManager(client_cfg, logger)
    snap = Snapshot(client_cfg.snapshot_path)
    target = client_cfg.target_dir
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "a.txt"), "w", encoding="utf-8") as f:
        f.write("old")
    snap.save("old", {"a.txt": FileEntry(rel="a.txt", sha256="o", size=3, mtime=0)})
    root = bm.backup("new", [FileEntry(rel="a.txt", sha256="o", size=3, mtime=0)], snap)
    assert os.path.isdir(root)

    r = rollback_version(client_cfg, logger, "new")
    assert r["restored"] >= 1
    assert r["snapshot_restored"]


def test_rollback_missing_version(client_cfg):
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    with pytest.raises(FileNotFoundError):
        rollback_version(client_cfg, logger, "no-such-version")


def test_list_versions(client_cfg):
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    bm = BackupManager(client_cfg, logger)
    snap = Snapshot(client_cfg.snapshot_path)
    os.makedirs(client_cfg.target_dir, exist_ok=True)
    with open(os.path.join(client_cfg.target_dir, "a.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    snap.save("v1", {"a.txt": FileEntry(rel="a.txt", sha256="x", size=1, mtime=0)})
    bm.backup("v2", [FileEntry(rel="a.txt", sha256="x", size=1, mtime=0)], snap)
    versions = bm.list_versions()
    assert "v2" in versions
