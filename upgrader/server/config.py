"""服务端配置.

支持从 dict / JSON 文件加载. 字段:
- host, port: HTTP 监听
- publish_dir: 当前发布目录 (被签名的文件树)
- data_dir: 服务端数据目录 (版本历史, 清单持久化, 密钥)
- key_dir: 密钥目录 (priv.pem/pub.pem), 默认 data_dir/keys
- jwt_secret: JWT HS256 密钥 (客户端交互认证).
  未设置 (或为默认占位值) 时首次启动自动生成随机密钥并持久化到 data_dir/jwt_secret.key
- admin_jwt_secret: 管理面密钥 (publish 等), 未设置则与数据面密钥相同
- log_level, log_file, max_bytes, backup_count
- include_patterns / exclude_patterns: 清单过滤 (子串或 glob 模式)
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from typing import List

DEFAULT_JWT_SECRET_PLACEHOLDER = "change-me"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    publish_dir: str = "./release"
    data_dir: str = "./data/server"
    key_dir: str = ""            # 空则 data_dir/keys
    jwt_secret: str = DEFAULT_JWT_SECRET_PLACEHOLDER
    admin_jwt_secret: str = ""   # 空则与 jwt_secret 相同
    log_level: str = "INFO"
    log_file: str = ""
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    token_ttl: int = 3600

    def resolve_key_dir(self) -> str:
        return self.key_dir or os.path.join(self.data_dir, "keys")

    def resolve_jwt_secret(self) -> str:
        """数据面 JWT 密钥. 显式配置则使用; 否则加载/生成 data_dir/jwt_secret.key."""
        if self.jwt_secret and self.jwt_secret != DEFAULT_JWT_SECRET_PLACEHOLDER:
            return self.jwt_secret
        return self._ensure_generated_secret()

    def _ensure_generated_secret(self) -> str:
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, "jwt_secret.key")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                return s
        s = secrets.token_urlsafe(48)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return s

    def resolve_admin_secret(self) -> str:
        return self.admin_jwt_secret or self.resolve_jwt_secret()

    @staticmethod
    def from_dict(d: dict) -> "ServerConfig":
        c = ServerConfig()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c

    @staticmethod
    def from_file(path: str) -> "ServerConfig":
        with open(path, "r", encoding="utf-8") as f:
            return ServerConfig.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "publish_dir": self.publish_dir,
            "data_dir": self.data_dir,
            "key_dir": self.key_dir,
            "jwt_secret": self.jwt_secret,
            "admin_jwt_secret": self.admin_jwt_secret,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "token_ttl": self.token_ttl,
        }
