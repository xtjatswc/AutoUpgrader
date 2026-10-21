"""端到端: 服务端发布 → 客户端同步 → 健康检查失败 → 自动回滚.

使用 FakeTransport 离线模拟 HTTP 层, 验证完整链路.
"""
from __future__ import annotations

import os

import pytest

from upgrader.client.config import ClientConfig
from upgrader.client.sync import SyncEngine
from upgrader.logcfg import setup_logger
from upgrader.manifest import Snapshot
from tests.conftest import FakeTransport


class _FailingHealthSync(SyncEngine):
    """覆盖健康检查为失败, 以验证自动回滚链路."""

    def __init__(self, cfg, logger, transport):
        super().__init__(cfg, logger, transport)

    # 注入: 让 run_health 总是失败
    @property
    def _fail_health(self):
        return self._fh

    # 简单覆盖: 直接 monkeypatch run_health
    def sync(self):
        import upgrader.client.sync as sync_mod
        orig = sync_mod.run_health
        sync_mod.run_health = lambda cfg, logger: False
        try:
            return super().sync()
        finally:
            sync_mod.run_health = orig


def test_auto_rollback_on_health_failure(server_core, client_cfg, tmp_env):
    """发布 v1 (正常同步), 再发 v2 但健康检查失败 → 自动回滚到 v1."""
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    transport = FakeTransport(server_core)

    # 第一次同步 v1 (健康检查未配置 → 通过)
    server_core.publish("v1")
    eng1 = SyncEngine(client_cfg, logger, transport)
    r1 = eng1.sync()
    assert r1.ok
    snap = Snapshot(client_cfg.snapshot_path)
    assert snap.current_version() == "v1"

    # 第二次发布 v2, 强制健康检查失败 → 应回滚
    (tmp_env["release"] / "a.txt").write_text("alpha v2", encoding="utf-8")
    server_core.publish("v2")
    eng2 = _FailingHealthSync(client_cfg, logger, transport)
    r2 = eng2.sync()
    assert not r2.ok
    assert r2.rolled_back
    assert "health check failed" in r2.error

    # 回滚后本地文件应恢复为 v1 内容
    with open(os.path.join(client_cfg.target_dir, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "alpha v1"
    # snapshot 应回到 v1
    assert snap.current_version() == "v1"


def test_unchanged_version_no_work(server_core, client_cfg):
    logger = setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True)
    transport = FakeTransport(server_core)
    server_core.publish("v1")
    r = SyncEngine(client_cfg, logger, transport).sync()
    assert r.ok and r.added == 2
    # 再次同步, 无 diff
    r2 = SyncEngine(client_cfg, logger, transport).sync()
    assert r2.ok
    assert r2.added == 0 and r2.updated == 0 and r2.deleted == 0
