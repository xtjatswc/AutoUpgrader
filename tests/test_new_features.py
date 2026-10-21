"""新增特性测试: fail-closed 验签 / 内联 PEM / 并发锁 / pending 崩溃恢复 /
全量备份与回滚一致性 / 备份保留策略 / 钩子失败中止 / 自然排序 / JWT 密钥自动生成.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from upgrader.client.config import ClientConfig
from upgrader.client.httpclient import UrllibTransport
from upgrader.client.sync import SyncEngine, SyncLock
from upgrader.client.rollback import rollback_version
from upgrader.crypto import init_keys
from upgrader.logcfg import setup_logger
from upgrader.manifest import FileEntry, Snapshot
from upgrader.server.version_store import _version_sort_key
from tests.conftest import FakeTransport


def _logger():
    return setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)


# ---------- 验签 fail-closed / 内联 PEM ----------

def test_fail_closed_missing_public_key(server_core, client_cfg):
    """verify_sign=True 但未配置公钥 → 构造引擎即失败."""
    cfg = ClientConfig(
        server_url="http://x", token="t",
        target_dir=client_cfg.target_dir,
        snapshot_dir=client_cfg.snapshot_dir,
        backup_dir=client_cfg.backup_dir,
        verify_sign=True,
        public_key="",
    )
    with pytest.raises(ValueError):
        SyncEngine(cfg, _logger(), FakeTransport(server_core))


def test_inline_pem_public_key(tmp_env, server_core):
    """public_key 为 PEM 内容 (而非路径) 时验签通过."""
    server_core.publish("v1")
    kp = init_keys(str(tmp_env["key_dir"]))
    cfg = ClientConfig(
        server_url="http://127.0.0.1:0", token="t",
        target_dir=os.path.join(tempfile.mkdtemp(), "tgt"),
        snapshot_dir=os.path.join(tempfile.mkdtemp(), "snap"),
        backup_dir=os.path.join(tempfile.mkdtemp(), "bak"),
        verify_sign=True,
        public_key=kp.public_pem,
    )
    eng = SyncEngine(cfg, _logger(), FakeTransport(server_core))
    r = eng.sync()
    assert r.ok


# ---------- 并发锁 ----------

def test_sync_lock_exclusive(tmp_path):
    p = str(tmp_path / ".sync.lock")
    l1 = SyncLock(p)
    l2 = SyncLock(p)
    assert l1.acquire()
    assert not l2.acquire()
    l1.release()
    assert l2.acquire()
    l2.release()
    assert not os.path.exists(p)


def test_sync_lock_stale_reclaim(tmp_path):
    p = str(tmp_path / ".sync.lock")
    l1 = SyncLock(p, stale_seconds=0)
    assert l1.acquire()
    # 模拟上一个持有者崩溃: 文件还在且已"过期"
    l2 = SyncLock(p, stale_seconds=0)
    time.sleep(0.01)
    assert l2.acquire()  # 过期后被清理并接管
    l2.release()


# ---------- pending 崩溃恢复 ----------

def test_pending_recovery(tmp_env, server_core):
    """上次同步在写 snapshot 后崩溃 (pending 残留) → 下次同步先恢复同步前状态."""
    base = tempfile.mkdtemp()
    tgt = os.path.join(base, "tgt")
    os.makedirs(tgt, exist_ok=True)
    with open(os.path.join(tgt, "old.txt"), "w", encoding="utf-8") as f:
        f.write("old-content")
    snap_dir = os.path.join(base, "snap")
    bak = os.path.join(base, "bak")
    os.makedirs(snap_dir, exist_ok=True)

    server_core.publish("v1")
    cfg1 = ClientConfig(
        server_url="http://x", token="t",
        target_dir=tgt, snapshot_dir=snap_dir, backup_dir=bak,
        verify_sign=False, retries=1,
    )
    eng1 = SyncEngine(cfg1, _logger(), FakeTransport(server_core))
    r1 = eng1.sync()
    assert r1.ok
    assert os.path.exists(os.path.join(tgt, "old.txt"))
    assert os.path.exists(os.path.join(tgt, "a.txt"))

    # 模拟 v2 同步中途崩溃: snapshot 已更新但 pending 标记仍在
    release = tmp_env["release"]
    (release / "a.txt").write_text("alpha v2", encoding="utf-8")
    server_core.publish("v2")

    # 人为制造 pending 标记: 模拟同步 v2 中途崩溃, 指向同步 v1 前的全量备份 v0
    pending_dir = snap_dir
    with open(os.path.join(pending_dir, "pending.json"), "w", encoding="utf-8") as f:
        json.dump({
            "version": "v2",
            "backup_root": os.path.join(bak, "v0"),
            "affected": ["a.txt", "old.txt"],
        }, f)

    cfg2 = ClientConfig(
        server_url="http://x", token="t",
        target_dir=tgt, snapshot_dir=snap_dir, backup_dir=bak,
        verify_sign=False, retries=1,
    )
    eng2 = SyncEngine(cfg2, _logger(), FakeTransport(server_core))
    r2 = eng2.sync()
    assert r2.ok
    # pending 恢复逻辑: 同步前本地是 v1 状态 (old.txt 在, a.txt=v1), 再应用 v2 diff
    assert not os.path.exists(os.path.join(pending_dir, "pending.json"))
    with open(os.path.join(tgt, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "alpha v2"


# ---------- 全量备份与回滚一致性 ----------

def test_full_backup_rollback_removes_new_files(tmp_env, server_core):
    """全量备份: 同步 v1→v2 后本地多出文件 c.txt, 回滚 v1 后 c.txt 被删除."""
    base = tempfile.mkdtemp()
    tgt = os.path.join(base, "tgt")
    snap_dir = os.path.join(base, "snap")
    bak = os.path.join(base, "bak")
    os.makedirs(tgt, exist_ok=True)
    with open(os.path.join(tgt, "extra.txt"), "w") as f:
        f.write("user-added")

    release = tmp_env["release"]
    server_core.publish("v1")
    cfg1 = ClientConfig(
        server_url="http://x", token="t",
        target_dir=tgt, snapshot_dir=snap_dir, backup_dir=bak,
        verify_sign=False, retries=1, backup_full=True,
    )
    r1 = SyncEngine(cfg1, _logger(), FakeTransport(server_core)).sync()
    assert r1.ok

    # v2: 服务端侧没有 extra.txt
    (release / "a.txt").write_text("alpha v2", encoding="utf-8")
    server_core.publish("v2")

    cfg2 = ClientConfig(
        server_url="http://x", token="t",
        target_dir=tgt, snapshot_dir=snap_dir, backup_dir=bak,
        verify_sign=False, retries=1, backup_full=True,
    )
    r2 = SyncEngine(cfg2, _logger(), FakeTransport(server_core)).sync()
    assert r2.ok
    # 同步 v2 之后用户又放了个文件 (备份 "v1" 中不含它)
    with open(os.path.join(tgt, "user-added-later.txt"), "w") as f:
        f.write("later")

    # 回滚到 v1: v1 备份 (全量) 记录了同步 v2 前所有文件 (含 extra.txt)
    r = rollback_version(cfg2, _logger(), "v1")
    assert r["snapshot_restored"]
    with open(os.path.join(tgt, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "alpha v1"
    # 全量恢复按 v1 的旧 snapshot 清掉同步期间新增文件
    assert not os.path.exists(os.path.join(tgt, "user-added-later.txt"))
    snap = Snapshot(os.path.join(snap_dir, "snapshot.json"))
    assert snap.current_version() == "v1"


def test_backup_retention(tmp_env, server_core):
    """backup_keep=2: 三次同步后旧备份被清理, 仅保留最近 2 份."""
    base = tempfile.mkdtemp()
    tgt = os.path.join(base, "tgt")
    snap_dir = os.path.join(base, "snap")
    bak = os.path.join(base, "bak")
    os.makedirs(tgt, exist_ok=True)
    with open(os.path.join(tgt, "f.txt"), "w", encoding="utf-8") as f:
        f.write("f")

    release = tmp_env["release"]
    def mkcfg():
        return ClientConfig(
            server_url="http://x", token="t",
            target_dir=tgt, snapshot_dir=snap_dir, backup_dir=bak,
            verify_sign=False, retries=1, backup_keep=2,
        )

    server_core.publish("v1")
    assert SyncEngine(mkcfg(), _logger(), FakeTransport(server_core)).sync().ok
    (release / "a.txt").write_text("A2", encoding="utf-8")
    server_core.publish("v2")
    assert SyncEngine(mkcfg(), _logger(), FakeTransport(server_core)).sync().ok
    (release / "a.txt").write_text("A3", encoding="utf-8")
    server_core.publish("v3")
    assert SyncEngine(mkcfg(), _logger(), FakeTransport(server_core)).sync().ok

    versions = sorted(d for d in os.listdir(bak) if os.path.isdir(os.path.join(bak, d)))
    # 每次同步以"同步前版本"命名备份: v1 (同步v2前), v2 (同步v3前), 保留最近 2 份
    assert versions[-1] in ("v1", "v2")
    assert len(versions) == 2


# ---------- 钩子失败中止 ----------

def test_stop_on_hook_failure_aborts(tmp_env, server_core):
    base = tempfile.mkdtemp()
    tgt = os.path.join(base, "tgt")
    os.makedirs(tgt, exist_ok=True)
    server_core.publish("v1")
    cfg = ClientConfig(
        server_url="http://x", token="t",
        target_dir=tgt, snapshot_dir=os.path.join(base, "snap"),
        backup_dir=os.path.join(base, "bak"),
        verify_sign=False, retries=1,
        hooks_pre_replace="exit 1",
        stop_on_hook_failure=True,
    )
    eng = SyncEngine(cfg, _logger(), FakeTransport(server_core))
    with pytest.raises(RuntimeError, match="hook failed"):
        eng.sync()
    # 同步未执行: 本地无 a.txt
    assert not os.path.exists(os.path.join(tgt, "a.txt"))


# ---------- 版本历史自然排序 ----------

def test_version_natural_sort():
    keys = ["v2", "v10", "v1", "v1.2", "v9"]
    out = sorted(keys, key=_version_sort_key, reverse=True)
    # 数字段按数值: v10 > v9 > v2 > v1.2 > v1
    assert out == ["v10", "v9", "v2", "v1.2", "v1"]


# ---------- JWT 密钥自动生成 ----------

def test_jwt_secret_auto_generated(tmp_path):
    from upgrader.server.config import ServerConfig
    cfg = ServerConfig(data_dir=str(tmp_path / "srv"), jwt_secret="")
    s1 = cfg.resolve_jwt_secret()
    s2 = cfg.resolve_jwt_secret()
    assert s1 == s2 and len(s1) >= 32
    # 生成的密钥已持久化
    assert os.path.isfile(os.path.join(str(tmp_path / "srv"), "jwt_secret.key"))
    # 显式配置的密钥优先
    cfg2 = ServerConfig(data_dir=str(tmp_path / "srv2"), jwt_secret="explicit" + "x" * 32)
    assert cfg2.resolve_jwt_secret() == "explicit" + "x" * 32


def test_http_timeout_applied():
    t = UrllibTransport(timeout=5)
    assert t.timeout == 5
    t2 = UrllibTransport()
    assert t2.timeout is None
