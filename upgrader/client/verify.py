"""客户端哈希 / 签名校验."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from ..crypto import verify_manifest
from ..manifest import hash_file


def sha256_of(path: str) -> str:
    return hash_file(path)


def verify_file_hash(path: str, expected: str) -> bool:
    try:
        return hash_file(path) == expected
    except OSError:
        return False


def resolve_public_key(public_key: str) -> str:
    """解析 public_key 配置: PEM 内容 (-----BEGIN 开头) 或 PEM 文件路径. 返回 PEM 文本."""
    if not public_key:
        return ""
    if "-----BEGIN" in public_key:
        return public_key
    if os.path.exists(public_key):
        with open(public_key, "r", encoding="utf-8") as f:
            return f.read()
    return ""


class SignVerifier:
    """封装公钥解析与 manifest 验签.

    verify_sign 开启但解析不到公钥时, 构造即抛 ValueError (fail-closed):
    验签是同步的核心安全保证, 不允许静默跳过.
    """

    def __init__(self, public_key: Optional[str], logger: logging.Logger,
                 verify_sign: bool = True):
        self.logger = logger
        self.verify_sign = bool(verify_sign)
        self.public_key_pem = resolve_public_key(public_key or "") if self.verify_sign else ""
        if self.verify_sign and not self.public_key_pem:
            raise ValueError(
                "verify_sign is enabled but public_key is missing or unreadable "
                "(expect PEM content or a PEM file path); refusing to sync without signature check"
            )
        if self.public_key_pem:
            self.logger.info("loaded public key for signature verification")

    def verify(self, files_json: bytes, version: str, published_at: str, sig_b64: str) -> bool:
        if not self.verify_sign:
            self.logger.debug("signature verification disabled")
            return True
        ok = verify_manifest(self.public_key_pem, files_json, version, published_at, sig_b64)
        if not ok:
            self.logger.error("manifest signature verification FAILED for version=%s", version)
        else:
            self.logger.debug("manifest signature OK version=%s", version)
        return ok
