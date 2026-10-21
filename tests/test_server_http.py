"""服务端 HTTP API 测试: 用 ThreadingHTTPServer 跑在随机端口, 真实 HTTP 调用.

覆盖: 认证 (JWT client/admin)、manifest 拉取、文件下载、版本、历史、发布.
"""
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error

import pytest

from upgrader.crypto import issue_token, verify_token
from upgrader.server.app import UpgraderServer
from upgrader.logcfg import setup_logger


class _ServerRunner:
    def __init__(self, core, jwt_secret, admin_secret):
        self.core = core
        self.jwt_secret = jwt_secret
        self.admin_secret = admin_secret
        self.logger = setup_logger("upgrader.server.test", "DEBUG", None, console=False, reset=True)
        from http.server import ThreadingHTTPServer
        from upgrader.server.app import _Handler
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        # 注入 state 到 server 实例, handler __init__ 时会拷贝到 self
        self.httpd._upgrader_inject = {
            "core": core,
            "jwt_secret": jwt_secret,
            "admin_secret": admin_secret,
            "logger": self.logger,
        }
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def runner(tmp_env):
    cfg = tmp_env["cfg"]
    logger = setup_logger("upgrader.server.test", "DEBUG", None, console=False, reset=True)
    core = UpgraderServer(cfg, logger)
    # handler 使用 core 上解析出的密钥 (显式配置优先, 否则自动生成)
    r = _ServerRunner(core, core.jwt_secret, core.admin_secret)
    # 先发布一个版本, 使 current 存在
    core.publish("v1")
    yield r
    r.stop()


def _req(url, token=None, method="GET", data=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if resp.status < 300:
                if "application/json" in ctype:
                    return resp.status, json.loads(raw.decode())
                return resp.status, raw
            return resp.status, raw
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body.decode())
        except Exception:
            return e.code, body


def test_healthz_no_auth(runner):
    code, body = _req(runner.base() + "/healthz")
    assert code == 200
    assert body["status"] == "ok"


def test_manifest_requires_token(runner, tmp_env):
    cfg = tmp_env["cfg"]
    code, _ = _req(runner.base() + "/api/v1/manifest", token=None)
    assert code == 401
    # 错误密钥 token
    bad = issue_token("wrong-secret-0000000000000000000", "u", ["client"])
    code, _ = _req(runner.base() + "/api/v1/manifest", token=bad)
    assert code == 401


def test_manifest_with_client_token(runner, tmp_env):
    cfg = tmp_env["cfg"]
    tok = issue_token(cfg.jwt_secret, "client1", ["client"])
    code, body = _req(runner.base() + "/api/v1/manifest", token=tok)
    assert code == 200
    assert body["version"] == "v1"
    assert any(f["rel"] == "a.txt" for f in body["files"])
    assert body["sign"]


def test_version_endpoint(runner, tmp_env):
    cfg = tmp_env["cfg"]
    tok = issue_token(cfg.jwt_secret, "c", ["client"])
    code, body = _req(runner.base() + "/api/v1/version", token=tok)
    assert code == 200
    assert body["version"] == "v1"


def test_file_download(runner, tmp_env):
    cfg = tmp_env["cfg"]
    tok = issue_token(cfg.jwt_secret, "c", ["client"])
    code, body = _req(runner.base() + "/api/v1/files?rel=a.txt", token=tok)
    assert code == 200
    assert isinstance(body, (str, bytes))
    assert b"alpha v1" in body if isinstance(body, bytes) else "alpha v1" in body


def test_publish_requires_admin(runner, tmp_env):
    cfg = tmp_env["cfg"]
    # 仅 client 角色 token 不能发布
    client_tok = issue_token(cfg.jwt_secret, "c", ["client"])
    code, _ = _req(runner.base() + "/api/v1/publish", token=client_tok, method="POST",
                   data={"version": "v2"})
    assert code == 403
    # admin 角色可以
    admin_tok = issue_token(cfg.admin_jwt_secret, "adm", ["admin"])
    code, body = _req(runner.base() + "/api/v1/publish", token=admin_tok, method="POST",
                      data={"version": "v2"})
    assert code == 200
    assert body["version"] == "v2"


def test_history(runner, tmp_env):
    cfg = tmp_env["cfg"]
    tok = issue_token(cfg.jwt_secret, "c", ["client"])
    code, body = _req(runner.base() + "/api/v1/history", token=tok)
    assert code == 200
    assert "v1" in body["history"]


def test_file_traversal_blocked(runner, tmp_env):
    cfg = tmp_env["cfg"]
    tok = issue_token(cfg.jwt_secret, "c", ["client"])
    # 目录穿越应返回 404
    code, _ = _req(runner.base() + "/api/v1/files?rel=..%2F..%2Fetc%2Fpasswd", token=tok)
    assert code == 404
