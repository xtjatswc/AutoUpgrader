"""日志系统: 级别 / 文件轮转 / 重复初始化去重."""
from __future__ import annotations

import logging
import os

from upgrader.logcfg import setup_logger


def test_log_level_controls_output(tmp_path):
    import logging
    logger = setup_logger("t.level", "WARNING", None, console=False, reset=True)
    # 加一个内存 handler 记录实际通过的日志
    recs: list[logging.LogRecord] = []
    class _MemHandler(logging.Handler):
        def emit(self, record):
            recs.append(record)
    h = _MemHandler()
    h.setLevel(logging.DEBUG)
    logger.addHandler(h)
    logger.debug("dbg")
    logger.info("info")
    logger.warning("warn")
    logger.error("err")
    names = [r.levelname for r in recs]
    assert "DEBUG" not in names
    assert "INFO" not in names
    assert "WARNING" in names
    assert "ERROR" in names


def test_log_to_rotating_file(tmp_path):
    logf = str(tmp_path / "logs" / "app.log")
    logger = setup_logger("t.file", "INFO", logf, max_bytes=2048, backup_count=2, console=False, reset=True)
    logger.info("hello file")
    for h in logger.handlers:
        h.flush()
    assert os.path.isfile(logf)
    with open(logf, encoding="utf-8") as f:
        assert "hello file" in f.read()


def test_no_duplicate_file_handler(tmp_path):
    logf = str(tmp_path / "x" / "a.log")
    setup_logger("t.dedup", "INFO", logf, console=False, reset=True)
    setup_logger("t.dedup", "INFO", logf, console=False, reset=False)  # 不重置
    logger = logging.getLogger("t.dedup")
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1


def test_all_levels_accepted():
    for lvl in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        setup_logger(f"t.{lvl.lower()}", lvl, None, console=False, reset=True)
    # 无效级别回退到 INFO
    import logging
    logger = setup_logger("t.invalid", "NOT_A_LEVEL", None, console=False, reset=True)
    assert logger.level == logging.INFO
