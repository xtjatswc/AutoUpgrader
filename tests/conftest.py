"""pytest fixtures: 提供临时目录、服务端核心、客户端引擎、FakeTransport."""
from __future__ import annotations

import json
import logging
import os
import pytest

from upgrader.server.config import ServerConfig
from upgrader.server.app import UpgraderServer
from upgrader.client.config import ClientConfig
from upgrader.client.httpclient import Transport
from upgrader.client.sync import SyncEngine
from upgrader.logcfg import setup_logger
from upgrader.crypto import init_keys


@pytest.fixture
def tmp_env(tmp_path):
    """构建临时环境: release 目录 + 服务端配置 + 密钥."""
    release = tmp_path / "release"
    release.mkdir()
    (release / "a.txt").write_text("alpha v1", encoding="utf-8")
    (release / "sub").mkdir()
    (release / "sub" / "b.txt").write_text("beta v1", encoding="utf-8")

    server_data = tmp_path / "server_data"
    server_data.mkdir()
    key_dir = server_data / "keys"
    # 使用 32 字节密钥避免 PyJWT InsecureKeyLengthWarning (Python 3.15 下升级为异常)
    cfg = ServerConfig(
        host="127.0.0.1",
        port=0,
        publish_dir=str(release),
        data_dir=str(server_data),
        key_dir=str(key_dir),
        jwt_secret="test-jwt-secret-000000000000000000",
        admin_jwt_secret="test-admin-secret-00000000000000",
        log_level="DEBUG",
        log_file="",
    )
    init_keys(str(key_dir))
    return {"release": release, "cfg": cfg, "key_dir": key_dir, "server_data": server_data}


@pytest.fixture
def server_core(tmp_env):
    cfg = tmp_env["cfg"]
    logger = setup_logger("upgrader.server", cfg.log_level, None, console=False, reset=True)
    return UpgraderServer(cfg, logger)


@pytest.fixture
def client_cfg(tmp_path, server_core):
    """客户端配置. 关联 server_core 以便 FakeTransport 拉取 manifest."""
    target = tmp_path / "client_target"
    target.mkdir()
    snap_dir = tmp_path / "client_snap"
    snap_dir.mkdir()
    backup_dir = tmp_path / "client_backup"
    backup_dir.mkdir()
    # 默认发布 v1, 使 manifest 存在
    if server_core.store.current() is None:
        server_core.publish("v1")
    cfg = ClientConfig(
        server_url="http://127.0.0.1:0",
        token="test-token",
        target_dir=str(target),
        snapshot_dir=str(snap_dir),
        backup_dir=str(backup_dir),
        log_level="DEBUG",
        log_file="",
        verify_sign=False,
        retries=2,
    )
    return cfg


def make_logger(name, level="DEBUG"):
    return setup_logger(name, level, None, console=False, reset=True)


# ---------- Fake Transport: 把服务端核心"伪装"成 HTTP 端 ----------

class FakeTransport(Transport):
    """离线模拟服务端 HTTP 端, 直接调用 UpgraderServer.core."""

    def __init__(self, core: UpgraderServer, public_key_pem: str = ""):
        self.core = core
        self.public_key_pem = public_key_pem
        self.calls: list[str] = []

    def _manifest_dict(self) -> dict:
        m = self.core.current_manifest()
        if m is None:
            raise FileNotFoundError("no current version")
        return m

    def get_json(self, url: str, token: str, params: dict | None = None) -> dict:
        self.calls.append(f"GET {url}")
        if url.endswith("/manifest"):
            return self._manifest_dict()
        if url.endswith("/version"):
            return {"version": self.core.store.current() or ""}
        raise KeyError(url)

    def get_bytes(self, url: str, token: str, params: dict | None = None):
        self.calls.append(f"GET {url}?{params}")
        rel = (params or {}).get("rel", "")
        data = self.core.read_file_bytes(rel)
        if data is None:
            raise FileNotFoundError(rel)
        return data, 200
