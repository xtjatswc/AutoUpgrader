"""客户端配置.

字段:
- server_url: 服务端基础 URL
- token: 客户端 JWT (client 角色), 或服务端签发的长期 token
- target_dir: 被同步的目录 (本地)
- snapshot_dir: 本地 snapshot.json 所在目录
- backup_dir: 备份目录
- backup_full: 备份整个目标目录 (True, 默认) 或仅受影响文件 (False).
  全量备份才能保证回滚到任意历史版本时目录状态一致.
- backup_keep: 最多保留的备份版本数, 0 表示不限制
- log_level, log_file, max_bytes, backup_count
- public_key: 验签公钥, 支持 PEM 内容 (-----BEGIN 开头) 或 PEM 文件路径
- verify_sign: 是否验签; 开启但无法取得公钥时同步直接失败 (fail-closed)
- health_command: 健康检查命令 (shell)
- health_url: 健康检查 URL (二选一)
- health_timeout: 秒
- health_retries: 健康检查失败后重试次数 (0 表示只检查一次)
- health_retry_interval: 重试间隔秒
- hooks_pre_replace / hooks_post_replace: shell 命令 (可空)
- stop_on_hook_failure: pre_replace 钩子失败时是否中止同步 (默认仅告警继续)
- http_timeout: 单次 HTTP 请求超时秒
- retries: 单文件下载重试次数
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields


@dataclass
class ClientConfig:
    server_url: str = "http://127.0.0.1:8000"
    token: str = ""
    target_dir: str = "./app"
    snapshot_dir: str = "./data/client/snapshot"
    backup_dir: str = "./data/client/backup"
    backup_full: bool = True
    backup_keep: int = 0
    log_level: str = "INFO"
    log_file: str = ""
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    public_key: str = ""
    verify_sign: bool = True
    health_command: str = ""
    health_url: str = ""
    health_timeout: int = 15
    health_retries: int = 0
    health_retry_interval: int = 2
    hooks_pre_replace: str = ""
    hooks_post_replace: str = ""
    stop_on_hook_failure: bool = False
    http_timeout: int = 60
    retries: int = 3

    @property
    def snapshot_path(self) -> str:
        return os.path.join(self.snapshot_dir, "snapshot.json")

    @classmethod
    def from_dict(cls, d: dict) -> "ClientConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @staticmethod
    def from_file(path: str) -> "ClientConfig":
        with open(path, "r", encoding="utf-8") as f:
            return ClientConfig.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
