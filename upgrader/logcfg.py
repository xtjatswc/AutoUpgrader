"""日志配置模块: 支持级别、文件轮转、结构化字段, 控制台/文件双输出.

- 默认 INFO, 可通过 config 设置 DEBUG/INFO/WARNING/ERROR/CRITICAL.
- 文件日志使用 RotatingFileHandler (默认 10MB x 5).
- 结构化字段通过 extra 注入: version, phase, file, action, duration_ms, error_code.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional

# 全局 logger 名
SERVER_LOGGER = "upgrader.server"
CLIENT_LOGGER = "upgrader.client"


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def setup_logger(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
    reset: bool = False,
) -> logging.Logger:
    """配置并返回 logger.

    Args:
        name: logger 名.
        level: 级别字符串 (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        log_file: 文件路径 (可选, 提供则写文件 + 轮转).
        max_bytes: 单个文件上限.
        backup_count: 保留备份数.
        console: 是否同时输出到 stderr.
        reset: True 时清除旧 handler (用于测试/重复初始化).
    """
    logger = logging.getLogger(name)
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(lvl)

    if reset:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console and not any(isinstance(h, logging.StreamHandler) for h in logger.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)):
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(fmt)
        ch.setLevel(lvl)
        logger.addHandler(ch)

    if log_file:
        _ensure_dir(log_file)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        # 避免重复添加 (相同路径)
        if not any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            and getattr(h, "baseFilename", None) == os.path.abspath(log_file)
            for h in logger.handlers
        ):
            logger.addHandler(fh)

    return logger


def get_logger(name: str = CLIENT_LOGGER) -> logging.Logger:
    return logging.getLogger(name)
