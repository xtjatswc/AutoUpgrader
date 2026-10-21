"""服务端 HTTP 应用.

基于标准库 http.server (ThreadingHTTPServer), 零第三方 HTTP 依赖.
路由:
- GET  /api/v1/manifest            当前版本清单 (含 sign)          [JWT: client]
- GET  /api/v1/version             当前版本信息                    [JWT: client]
- GET  /api/v1/files?rel=<path>    下载单文件                       [JWT: client]
- GET  /api/v1/history             版本历史                        [JWT: client]
- POST /api/v1/token              客户端 token 换取管理面 token    [JWT: client]
- POST /api/v1/publish           发布新版本 (签名 manifest)         [JWT: admin]
- GET  /healthz                  存活探针 (无需认证)

认证: Authorization: Bearer <JWT>. 管理面使用 admin 密钥签发的 token.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import jwt

from ..crypto import (
    KeyPair,
    init_keys,
    sign_manifest,
    verify_token,
)
from ..manifest import build_manifest
from ..logcfg import setup_logger
from .config import ServerConfig
from .version_store import VersionStore


class UpgraderServer:
    """可注入/可测试的服务端核心. 持有配置, 密钥, 版本存储."""

    def __init__(self, config: ServerConfig, logger: Optional[logging.Logger] = None):
        self.cfg = config
        self.logger = logger or setup_logger("upgrader.server", config.log_level, config.log_file)
        os.makedirs(config.publish_dir, exist_ok=True)
        os.makedirs(config.data_dir, exist_ok=True)
        key_dir = config.resolve_key_dir()
        self.keypair = init_keys(key_dir)
        self.store = VersionStore(config.data_dir)
        # 校验 JWT 密钥长度 (PyJWT RFC 7518: HMAC-SHA256 建议 >= 32 字节)
        for name, secret in (("jwt_secret", config.jwt_secret),
                             ("admin_jwt_secret", config.resolve_admin_secret())):
            if len(secret) < 32:
                self.logger.warning(
                    "%s is %d bytes (<32), PyJWT may reject tokens; "
                    "please use a >=32-char secret in production", name, len(secret),
                )

    # ---- 管理面 ----

    def publish(self, version: str, published_at: str = "") -> dict:
        """扫描 publish_dir → 生成清单 → Ed25519 签名 → 持久化 → 设为当前."""
        m = build_manifest(
            self.cfg.publish_dir,
            version=version,
            published_at=published_at,
            include_patterns=self.cfg.include_patterns,
            exclude_patterns=self.cfg.exclude_patterns,
        )
        sig = sign_manifest(self.keypair.private_pem, m.files_json(), version, m.published_at)
        m.sign = sig
        self.store.save(m, sig)
        self.store.set_current(version)
        self.logger.info("publish version=%s files=%d", version, len(m.files))
        return {
            "version": version,
            "published_at": m.published_at,
            "files": len(m.files),
            "sign": sig,
        }

    def current_manifest(self) -> Optional[dict]:
        v = self.store.current()
        if not v:
            return None
        data = self.store.load(v)
        if data is None:
            return None
        return data

    def history(self, limit: int = 50) -> list[str]:
        return self.store.history(limit)

    # ---- 文件下载 ----

    def read_file_bytes(self, rel: str) -> Optional[bytes]:
        """读取发布目录下的相对路径文件. 防目录穿越."""
        rel = rel.lstrip("/")
        norm = os.path.normpath(rel)
        if norm.startswith("..") or os.path.isabs(norm):
            return None
        full = os.path.join(self.cfg.publish_dir, norm)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            return f.read()


# ---------- HTTP 路由处理 ----------

def _auth_token(headers) -> Optional[str]:
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


def _check_role(secret: str, token: Optional[str], role: str,
                logger: Optional[logging.Logger] = None) -> Optional[dict]:
    if not token:
        if logger is not None:
            logger.warning("auth: no token provided")
        return None
    try:
        payload = verify_token(secret, token)
    except jwt.PyJWTError as e:
        if logger is not None:
            logger.warning("jwt verify failed: %s (secret_len=%d)", e, len(secret or ""))
        return None
    if role not in payload.get("roles", []):
        if logger is not None:
            logger.warning("token role mismatch: need=%s got=%s", role, payload.get("roles"))
        return None
    return payload


class _Handler(BaseHTTPRequestHandler):
    server_version = "Upgrader/1.0"

    # 由 make_handler 注入 (Python 3.13+ PEP 709 影响闭包类体, 改用 instance 注入)
    core: Optional[UpgraderServer] = None
    jwt_secret: str = ""
    admin_secret: str = ""
    logger: Optional[logging.Logger] = None

    def __init__(self, *args, **kwargs):
        # 注入必须在 super().__init__() 之前完成:
        # BaseHTTPRequestHandler.__init__ 会立刻读取 self.server 并 handle() 请求,
        # 若 state 在 super 之后才设置, do_GET/do_POST 里 self.logger/self.core 均为类默认 None.
        handler_server = kwargs.get("server") or (args[2] if len(args) > 2 else None)
        if handler_server is not None and hasattr(handler_server, "_upgrader_inject"):
            for k, v in handler_server._upgrader_inject.items():
                setattr(self, k, v)
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # 静默默认访问日志, 改用 logger
        try:
            self.logger.debug("http %s", fmt % args)
        except Exception:
            pass

    # ---- 公共响应 ----
    def _send_json(self, status: int, obj: dict | list | None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, data: bytes, etag: str = "") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self, msg: str = "not found") -> None:
        self._send_json(404, {"error": msg})

    def _unauthorized(self, msg: str = "unauthorized") -> None:
        self._send_json(401, {"error": msg})

    def _forbidden(self, msg: str = "forbidden") -> None:
        self._send_json(403, {"error": msg})

    # ---- GET ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/api/v1/manifest":
            token = _auth_token(self.headers)
            if not _check_role(self.jwt_secret, token, "client", self.logger):
                self._unauthorized("client token required")
                return
            m = self.core.current_manifest()
            if not m:
                self._not_found("no current version published")
                return
            self._send_json(200, m)
            return

        if path == "/api/v1/version":
            token = _auth_token(self.headers)
            if not _check_role(self.jwt_secret, token, "client", self.logger):
                self._unauthorized("client token required")
                return
            v = self.core.store.current()
            self._send_json(200, {"version": v or "", "has_release": v is not None})
            return

        if path == "/api/v1/files":
            token = _auth_token(self.headers)
            if not _check_role(self.jwt_secret, token, "client", self.logger):
                self._unauthorized("client token required")
                return
            rel = (q.get("rel", [""])[0] or "").strip()
            if not rel:
                self._send_json(400, {"error": "rel query param required"})
                return
            data = self.core.read_file_bytes(rel)
            if data is None:
                self._not_found(f"file {rel!r} not available")
                return
            self._send_file(data, etag="")
            return

        if path == "/api/v1/history":
            token = _auth_token(self.headers)
            if not _check_role(self.jwt_secret, token, "client", self.logger):
                self._unauthorized("client token required")
                return
            self._send_json(200, {"history": self.core.history(int(q.get("limit", ["50"])[0] or 50))})
            return

        self._not_found()

    # ---- POST ----
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/v1/publish":
            token = _auth_token(self.headers)
            if not _check_role(self.admin_secret, token, "admin", self.logger):
                self._forbidden("admin token required for publish")
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode()) if raw else {}
            except Exception:
                self._send_json(400, {"error": "invalid json body"})
                return
            version = (body.get("version") or "").strip()
            if not version:
                self._send_json(400, {"error": "version required"})
                return
            result = self.core.publish(version, published_at=body.get("published_at", ""))
            self._send_json(200, result)
            return

        if path == "/api/v1/token":
            # 客户端用 client token 换取一个 (或确认) 长期 token; 管理面 token 由离线签发.
            token = _auth_token(self.headers)
            if not _check_role(self.jwt_secret, token, "client", self.logger):
                self._unauthorized("client token required")
                return
            self._send_json(200, {"ok": True, "note": "client token is valid"})
            return

        self._not_found()


def make_handler(core: UpgraderServer, jwt_secret: str, admin_secret: str, logger: logging.Logger) -> type:
    """生成绑定 core 的 handler 类.

    Python 3.13+ PEP 709 改变了闭包在类体中的绑定行为, 因此把 state
    放到 ThreadingHTTPServer 实例属性上, 在 handler __init__ 时注入.
    返回的 _Handler 类本身无额外 state, 兼容 ThreadingHTTPServer.
    """
    return _Handler


def run_server(config: ServerConfig) -> ThreadingHTTPServer:
    """启动服务端, 阻塞运行. 返回 server 实例以便测试 .shutdown()."""
    core = UpgraderServer(config)
    httpd = ThreadingHTTPServer((config.host, config.port), _Handler)
    # 注入 state 到 server 实例, handler __init__ 时会拷贝到 self
    httpd._upgrader_inject = {
        "core": core,
        "jwt_secret": config.jwt_secret,
        "admin_secret": config.resolve_admin_secret(),
        "logger": core.logger,
    }
    core.logger.info(
        "upgrader server listening on %s:%d publish_dir=%s",
        config.host, config.port, config.publish_dir,
    )
    return httpd
