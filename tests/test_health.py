"""健康检查: 命令 / URL / 未配置 / 失败回滚链路."""
from __future__ import annotations

import os

import pytest

from upgrader.client.health import run_health
from upgrader.logcfg import setup_logger


def test_health_command_ok(client_cfg):
    client_cfg.health_command = "exit 0"
    assert run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))


def test_health_command_fail(client_cfg):
    client_cfg.health_command = "exit 1"
    assert not run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))


def test_health_timeout(client_cfg, monkeypatch):
    import upgrader.client.health as h
    real_run = h.subprocess.run

    def fake_run(cmd, **kw):
        raise real_run.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(h.subprocess, "run", fake_run)
    client_cfg.health_command = "sleep 30"
    client_cfg.health_timeout = 1
    assert not run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))


def test_health_no_config_ok(client_cfg):
    client_cfg.health_command = ""
    client_cfg.health_url = ""
    assert run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))


def test_health_url_fail(client_cfg, monkeypatch):
    import upgrader.client.health as h

    def fake_urlopen(url, timeout=None):
        import urllib.error
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(h.urllib.request, "urlopen", fake_urlopen)
    client_cfg.health_url = "http://127.0.0.1:1/health"
    assert not run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))


def test_health_url_ok(client_cfg, monkeypatch):
    import upgrader.client.health as h

    def fake_urlopen(url, timeout=None):
        class _Resp:
            status = 200
            def read(self):
                return b"ok"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        return _Resp()

    monkeypatch.setattr(h.urllib.request, "urlopen", fake_urlopen)
    client_cfg.health_url = "http://127.0.0.1:1/health"
    assert run_health(client_cfg, setup_logger("upgrader.client", "DEBUG", None, console=False, reset=True))
