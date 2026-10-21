"""统一 CLI.

子命令:
- server        启动服务端 (HTTP + JWT 认证)
- publish       发布新版本 (扫描 publish_dir → 签名 → 设为当前)
- sync          客户端同步
- rollback      客户端回滚到指定备份版本
- token         签发 JWT (client / admin 角色)
- init-keys     生成 Ed25519 密钥对
- version       查看当前版本

配置通过 --config <path> 或环境变量 UPGRADER_CONFIG 加载 (JSON).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import __version__
from .crypto import init_keys, issue_token
from .logcfg import setup_logger
from .server.app import UpgraderServer, run_server
from .server.config import ServerConfig
from .client.config import ClientConfig
from .client.httpclient import UrllibTransport
from .client.sync import SyncEngine
from .client.rollback import rollback_version
from .client.backup import BackupManager


def _load_server_cfg(args) -> ServerConfig:
    if getattr(args, "config", None):
        return ServerConfig.from_file(args.config)
    return ServerConfig()


def _load_client_cfg(args) -> ClientConfig:
    if getattr(args, "config", None):
        return ClientConfig.from_file(args.config)
    return ClientConfig()


def _setup_logger(cfg, name, console=True):
    return setup_logger(name, cfg.log_level, cfg.log_file, cfg.max_bytes, cfg.backup_count, console=console)


# ---------- 命令实现 ----------

def cmd_server(args):
    cfg = _load_server_cfg(args)
    logger = _setup_logger(cfg, "upgrader.server")
    httpd = run_server(cfg)
    logger.info("server started pid=%d", os.getpid())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("server stopping")
    finally:
        httpd.server_close()
    return 0


def cmd_publish(args):
    cfg = _load_server_cfg(args)
    logger = _setup_logger(cfg, "upgrader.server")
    core = UpgraderServer(cfg, logger)
    if not args.version:
        args.version = time.strftime("v%Y%m%d%H%M%S", time.localtime())
    result = core.publish(args.version, published_at=args.published_at or "")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_token(args):
    cfg = _load_server_cfg(args)
    secret = cfg.resolve_admin_secret() if args.role == "admin" else cfg.resolve_jwt_secret()
    token = issue_token(secret, args.subject, [args.role], ttl=args.ttl)
    print(token)
    return 0


def cmd_init_keys(args):
    cfg = _load_server_cfg(args)
    kp = init_keys(cfg.resolve_key_dir())
    print(json.dumps({"key_dir": cfg.resolve_key_dir(), "public_pem": kp.public_pem[:60] + "..."}, ensure_ascii=False))
    return 0


def cmd_sync(args):
    cfg = _load_client_cfg(args)
    logger = _setup_logger(cfg, "upgrader.client")
    if not cfg.token:
        logger.error("client token not configured; cannot authenticate to server")
        return 2
    engine = SyncEngine(cfg, logger, transport=UrllibTransport(timeout=cfg.http_timeout))
    try:
        result = engine.sync()
    except Exception as e:
        logger.error("sync failed: %s", e)
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.ok else 1


def cmd_rollback(args):
    cfg = _load_client_cfg(args)
    logger = _setup_logger(cfg, "upgrader.client")
    try:
        r = rollback_version(cfg, logger, args.version)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    except Exception as e:
        logger.error("rollback failed: %s", e)
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1


def cmd_version(args):
    cfg = _load_client_cfg(args)
    logger = _setup_logger(cfg, "upgrader.client")
    from .manifest import Snapshot
    snap = Snapshot(cfg.snapshot_path)
    print(json.dumps({"local_version": snap.current_version()}, ensure_ascii=False))
    return 0


# ---------- argparse ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="upgrader", description="AutoUpgrader CLI")
    p.add_argument("--version", action="version", version=f"upgrader {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # server
    s = sub.add_parser("server", help="run server")
    s.add_argument("--config", help="server config json path")
    s.set_defaults(func=cmd_server)

    # publish
    s = sub.add_parser("publish", help="publish a new version")
    s.add_argument("--config", help="server config json path")
    s.add_argument("--version", help="version tag")
    s.add_argument("--published-at", dest="published_at", help="ISO timestamp (optional)")
    s.set_defaults(func=cmd_publish)

    # token
    s = sub.add_parser("token", help="issue a JWT")
    s.add_argument("--config", help="server config json path")
    s.add_argument("--role", choices=["client", "admin"], default="client")
    s.add_argument("--subject", default="user")
    s.add_argument("--ttl", type=int, default=3600)
    s.set_defaults(func=cmd_token)

    # init-keys
    s = sub.add_parser("init-keys", help="generate Ed25519 keypair")
    s.add_argument("--config", help="server config json path")
    s.set_defaults(func=cmd_init_keys)

    # sync
    s = sub.add_parser("sync", help="client sync")
    s.add_argument("--config", help="client config json path")
    s.set_defaults(func=cmd_sync)

    # rollback
    s = sub.add_parser("rollback", help="client rollback to a backup version")
    s.add_argument("--config", help="client config json path")
    s.add_argument("--version", required=True, help="backup version to roll back to")
    s.set_defaults(func=cmd_rollback)

    # version
    s = sub.add_parser("version", help="show local version")
    s.add_argument("--config", help="client config json path")
    s.set_defaults(func=cmd_version)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
