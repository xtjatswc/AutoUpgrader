"""客户端哈希 / 签名校验."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from ..crypto import verify_manifest, verify_data


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file_hash(path: str, expected: str) -> bool:
    try:
        return sha256_of(path) == expected
    except OSError:
        return False


class SignVerifier:
    """封装公钥加载与 manifest 验签."""

    def __init__(self, public_key_pem_path: Optional[str], logger: logging.Logger):
        self.logger = logger
        self.public_key_pem = ""
        if public_key_pem_path and os.path.exists(public_key_pem_path):
            with open(public_key_pem_path, "r", encoding="utf-8") as f:
                self.public_key_pem = f.read()
            self.logger.info("loaded public key for signature verification")

    def verify(self, files_json: bytes, version: str, published_at: str, sig_b64: str) -> bool:
        if not self.public_key_pem:
            self.logger.warning("no public key configured, skipping signature check")
            return True
        ok = verify_manifest(self.public_key_pem, files_json, version, published_at, sig_b64)
        if not ok:
            self.logger.error("manifest signature verification FAILED for version=%s", version)
        else:
            self.logger.debug("manifest signature OK version=%s", version)
        return ok
