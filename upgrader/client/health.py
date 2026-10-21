"""客户端健康检查: 外部命令 或 HTTP URL.

支持失败重试: health_retries 为失败后额外重试次数 (0=只查一次),
health_retry_interval 为两次检查间隔秒。用于容忍服务启动慢/未就绪的情况。
"""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.request
from typing import Optional


def run_health(cfg, logger: logging.Logger) -> bool:
    """按配置执行健康检查 (含重试), 返回是否通过. 未配置则视为通过."""
    timeout = max(1, int(getattr(cfg, "health_timeout", 0) or 15))
    retries = max(0, int(getattr(cfg, "health_retries", 0) or 0))
    interval = max(0, int(getattr(cfg, "health_retry_interval", 0) or 0))

    check = _make_check(cfg, timeout, logger)
    total = 1 + retries
    for attempt in range(1, total + 1):
        ok = check()
        if ok:
            if attempt > 1:
                logger.info("health ok on attempt %d/%d", attempt, total)
            return True
        if attempt < total:
            logger.warning("health failed attempt %d/%d, retry in %ds", attempt, total, interval)
            if interval > 0:
                time.sleep(interval)
    logger.error("health failed after %d attempts, treating as unhealthy", total)
    return False


def _make_check(cfg, timeout: int, logger: logging.Logger):
    if cfg.health_command:
        return lambda: _check_command(cfg.health_command, timeout, logger)
    if cfg.health_url:
        return lambda: _check_url(cfg.health_url, timeout, logger)
    logger.debug("no health check configured, assume OK")

    def _noop() -> bool:
        return True

    return _noop


def _check_command(cmd: str, timeout: int, logger: logging.Logger) -> bool:
    logger.info("health: running command %r", cmd)
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            logger.error("health command failed rc=%s stderr=%s", r.returncode, r.stderr.strip()[:300])
            return False
        logger.info("health command ok")
        return True
    except subprocess.TimeoutExpired:
        logger.error("health command timeout after %ds", timeout)
        return False
    except Exception as e:
        logger.error("health command error: %s", e)
        return False


def _check_url(url: str, timeout: int, logger: logging.Logger) -> bool:
    logger.info("health: probing url %r", url)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode() if hasattr(resp, "getcode") else getattr(resp, "status", 0)
        code = int(code or 0)
        ok = 200 <= code < 300
        if not ok:
            logger.error("health url status=%s", code)
        else:
            logger.info("health url ok status=%s", code)
        return ok
    except Exception as e:
        logger.error("health url error: %s", e)
        return False
