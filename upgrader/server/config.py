"""服务端配置.

支持从 dict / JSON 文件加载. 字段:
- host, port: HTTP 监听
- publish_dir: 当前发布目录 (被签名的文件树)
- data_dir: 服务端数据目录 (版本历史, 清单持久化, 密钥)
- key_dir: 密钥目录 (priv.pem/pub.pem), 默认 data_dir/keys
- jwt_secret: JWT HS256 密钥 (客户端交互认证)
- admin_jwt_secret: 管理面密钥 (publish 等), 未设置则与 jwt_secret 相同
- log_level, log_file, max_bytes, backup_count
- include_patterns / exclude_patterns: 清单过滤
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    publish_dir: str = "./release"
    data_dir: str = "./data/server"
    key_dir: str = ""            # 空则 data_dir/keys
    jwt_secret: str = "change-me"
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

    def resolve_admin_secret(self) -> str:
        return self.admin_jwt_secret or self.jwt_secret

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
