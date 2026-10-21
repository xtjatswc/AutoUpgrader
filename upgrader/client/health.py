"""客户端健康检查: 外部命令 或 HTTP URL."""
from __future__ import annotations

import logging
import subprocess
import urllib.request
from typing import Optional


def run_health(cfg, logger: logging.Logger) -> bool:
    """按配置执行健康检查, 返回是否通过. 未配置则视为通过."""
    timeout = max(1, int(cfg.health_timeout or 15))
    if cfg.health_command:
        return _check_command(cfg.health_command, timeout, logger)
    if cfg.health_url:
        return _check_url(cfg.health_url, timeout, logger)
    logger.debug("no health check configured, assume OK")
    return True


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
