"""客户端配置.

字段:
- server_url: 服务端基础 URL
- token: 客户端 JWT (client 角色), 或服务端签发的长期 token
- target_dir: 被同步的目录 (本地)
- snapshot_dir: 本地 snapshot.json 所在目录
- backup_dir: 备份目录
- log_level, log_file, max_bytes, backup_count
- public_key: 验签公钥 (pub.pem 路径), 为空则跳过签名校验
- verify_sign: 是否验签
- health_command: 健康检查命令 (shell)
- health_url: 健康检查 URL (二选一)
- health_timeout: 秒
- hooks_pre_replace / hooks_post_replace: shell 命令 (可空)
- batch_size: 单批拉取文件数
- retries: 单文件重试次数
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ClientConfig:
    server_url: str = "http://127.0.0.1:8000"
    token: str = ""
    target_dir: str = "./app"
    snapshot_dir: str = "./data/client/snapshot"
    backup_dir: str = "./data/client/backup"
    log_level: str = "INFO"
    log_file: str = ""
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    public_key: str = ""
    verify_sign: bool = True
    health_command: str = ""
    health_url: str = ""
    health_timeout: int = 15
    hooks_pre_replace: str = ""
    hooks_post_replace: str = ""
    batch_size: int = 20
    retries: int = 3

    @property
    def snapshot_path(self) -> str:
        import os
        return os.path.join(self.snapshot_dir, "snapshot.json")

    @staticmethod
    def from_dict(d: dict) -> "ClientConfig":
        c = ClientConfig()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c

    @staticmethod
    def from_file(path: str) -> "ClientConfig":
        with open(path, "r", encoding="utf-8") as f:
            return ClientConfig.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {
            "server_url": self.server_url,
            "token": self.token,
            "target_dir": self.target_dir,
            "snapshot_dir": self.snapshot_dir,
            "backup_dir": self.backup_dir,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "public_key": self.public_key,
            "verify_sign": self.verify_sign,
            "health_command": self.health_command,
            "health_url": self.health_url,
            "health_timeout": self.health_timeout,
            "hooks_pre_replace": self.hooks_pre_replace,
            "hooks_post_replace": self.hooks_post_replace,
            "batch_size": self.batch_size,
            "retries": self.retries,
        }
