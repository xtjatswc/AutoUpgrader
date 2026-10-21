"""客户端同步引擎: 增量同步 / 哈希校验 / 失败回滚 / 验签拒绝."""
from __future__ import annotations

import json
import os

import pytest

from upgrader.client.httpclient import Transport
from upgrader.client.sync import SyncEngine
from upgrader.logcfg import setup_logger
from upgrader.server.app import UpgraderServer
from upgrader.crypto import init_keys
from tests.conftest import FakeTransport


def _engine(cfg, transport, logger=None):
    return SyncEngine(cfg, logger or setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True), transport)


def test_full_sync_add_update_delete(server_core, client_cfg, tmp_env):
    """客户端空 target → 全量; 之后改一个、删一个 → 增量."""
    transport = FakeTransport(server_core)
    eng = _engine(client_cfg, transport)

    # 发布 v1 并同步
    server_core.publish("v1")
    r1 = eng.sync()
    assert r1.ok and not r1.rolled_back
    assert r1.added == 2 and r1.updated == 0 and r1.deleted == 0
    assert os.path.isfile(os.path.join(client_cfg.target_dir, "a.txt"))
    assert os.path.isfile(os.path.join(client_cfg.target_dir, "sub", "b.txt"))

    # 第二次同步应无 diff
    r2 = eng.sync()
    assert r2.ok
    assert r2.added == 0 and r2.updated == 0 and r2.deleted == 0

    # 改 a.txt, 删 b.txt, 加 c.txt → 发布 v2
    (tmp_env["release"] / "a.txt").write_text("alpha v2", encoding="utf-8")
    os.remove(tmp_env["release"] / "sub" / "b.txt")
    (tmp_env["release"] / "c.txt").write_text("gamma v2", encoding="utf-8")
    server_core.publish("v2")
    r3 = eng.sync()
    assert r3.ok
    assert r3.updated == 1  # a.txt
    assert r3.deleted == 1  # b.txt
    assert r3.added == 1    # c.txt
    # 本地校验
    with open(os.path.join(client_cfg.target_dir, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "alpha v2"
    assert not os.path.exists(os.path.join(client_cfg.target_dir, "sub", "b.txt"))
    assert os.path.isfile(os.path.join(client_cfg.target_dir, "c.txt"))


def test_no_change_is_noop(server_core, client_cfg, tmp_env):
    """无 diff 时不触发文件下载, 仅重新拉取 manifest."""
    transport = FakeTransport(server_core)
    eng = _engine(client_cfg, transport)
    eng.sync()
    base_calls = len(transport.calls)
    r = eng.sync()
    assert r.ok
    assert r.unchanged >= 1
    new_calls = len(transport.calls)
    # 仅 1 次 manifest 拉取, 无文件下载
    download_calls = [c for c in transport.calls[base_calls:] if "/files" in c]
    assert len(download_calls) == 0


class _CorruptTransport(Transport):
    """返回错误内容导致哈希校验失败."""

    def __init__(self, core):
        self.core = core
        self.calls = []

    def get_json(self, url, token, params=None):
        return self.core.current_manifest()

    def get_bytes(self, url, token, params=None):
        rel = (params or {}).get("rel", "")
        data = self.core.read_file_bytes(rel)
        return b"CORRUPTED" + (data or b""), 200


def _fresh_server():
    """独立临时目录建一个服务端核心, 含一个 a.txt 文件."""
    import tempfile
    base = tempfile.mkdtemp()
    release = os.path.join(base, "release")
    os.makedirs(release, exist_ok=True)
    with open(os.path.join(release, "a.txt"), "w", encoding="utf-8") as f:
        f.write("data")
    from upgrader.server.config import ServerConfig
    key_dir = os.path.join(base, "keys")
    init_keys(key_dir)
    s = ServerConfig(
        publish_dir=release,
        data_dir=os.path.join(base, "data"),
        key_dir=key_dir,
        jwt_secret="x",
        admin_jwt_secret="x",
    )
    return UpgraderServer(s, setup_logger("upgrader.server", "DEBUG", None, console=False, reset=True))


def test_hash_mismatch_raises(client_cfg):
    """下载内容损坏 → sha256 校验失败 → 抛出 RuntimeError."""
    # 新建一个服务端核心 (独立临时目录), 避免污染 server_core fixture
    core = _fresh_server()
    core.publish("v1")
    transport = _CorruptTransport(core)
    eng = _engine(client_cfg, transport)
    with pytest.raises(RuntimeError):
        eng.sync()


def test_signature_mismatch_rejected(client_cfg, server_core, tmp_env):
    """验签开启 + 客户端公钥与服务端不匹配 → 拒绝同步 (ValueError)."""
    from upgrader.client.config import ClientConfig
    import tempfile
    base = tempfile.mkdtemp()
    # 服务端用 tmp_env 的 key_dir (keyA) 签名; 客户端配另一对 keyB 的公钥
    keyb_dir = os.path.join(base, "keyb")
    init_keys(keyb_dir)
    client_cfg2 = ClientConfig(
        server_url="http://x",
        token="t",
        target_dir=os.path.join(base, "tgt"),
        snapshot_dir=os.path.join(base, "snap"),
        backup_dir=os.path.join(base, "bak"),
        verify_sign=True,
        public_key=os.path.join(keyb_dir, "pub.pem"),
        retries=2,
    )
    os.makedirs(client_cfg2.target_dir, exist_ok=True)
    transport = FakeTransport(server_core)
    eng = SyncEngine(client_cfg2, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True), transport)
    with pytest.raises(ValueError):
        eng.sync()
