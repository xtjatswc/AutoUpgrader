"""钩子命令执行 (同步 / 回滚共用)."""
from __future__ import annotations

import logging
import subprocess

HOOK_TIMEOUT = 120  # 秒


def run_hook(cmd: str, phase: str, logger: logging.Logger) -> bool:
    """执行 shell 钩子. 返回是否成功 (未配置视为成功, 失败仅记录不抛出)."""
    if not cmd:
        return True
    logger.info("hook %s: %r", phase, cmd)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        if r.returncode != 0:
            logger.warning("hook %s rc=%s stderr=%s", phase, r.returncode, r.stderr.strip()[:300])
            return False
        logger.debug("hook %s ok", phase)
        return True
    except Exception as e:
        logger.error("hook %s error: %s", phase, e)
        return False
