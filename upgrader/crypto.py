"""签名/验签 + JWT 认证 (最小依赖).

- 文件清单签名: Ed25519 (cryptography).
- 访问令牌: JWT (PyJWT, HS256), 服务端持有密钥; 管理面 (publish) 使用更高权限 token.
- 密钥生成与读写均落盘, 权限 0600.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass
class KeyPair:
    private_pem: str  # PKCS8 PEM
    public_pem: str   # SubjectPublicKeyInfo PEM

    @staticmethod
    def generate() -> "KeyPair":
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        return KeyPair(private_pem=priv_pem, public_pem=pub_pem)

    @staticmethod
    def load_private(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def load_public(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


def _write_pem(path: str, content: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def init_keys(key_dir: str) -> KeyPair:
    """生成并持久化密钥对到 key_dir (priv.pem / pub.pem). 已存在则复用."""
    os.makedirs(key_dir, exist_ok=True)
    priv_path = os.path.join(key_dir, "priv.pem")
    pub_path = os.path.join(key_dir, "pub.pem")
    if os.path.exists(priv_path) and os.path.exists(pub_path):
        return KeyPair(
            private_pem=KeyPair.load_private(priv_path),
            public_pem=KeyPair.load_public(pub_path),
        )
    kp = KeyPair.generate()
    _write_pem(priv_path, kp.private_pem)
    _write_pem(pub_path, kp.public_pem)
    return kp


# ---------- Ed25519 签名 (用于 manifest) ----------

def sign_data(private_pem: str, data: bytes) -> str:
    """对 data 做 Ed25519 签名, 返回 base64."""
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    sig = key.sign(data)
    return base64.b64encode(sig).decode()


def verify_data(public_pem: str, data: bytes, sig_b64: str) -> bool:
    """验证 Ed25519 签名. 异常视为无效."""
    try:
        key = serialization.load_pem_public_key(public_pem.encode())
        sig = base64.b64decode(sig_b64)
        key.verify(sig, data)
        return True
    except Exception:
        return False


# ---------- JWT (HS256) ----------

def issue_token(secret: str, subject: str, roles: list[str], ttl: int = 3600) -> str:
    """签发 HS256 JWT. roles 写入 claim 'roles'."""
    now = int(time.time())
    payload = {
        "sub": subject,
        "roles": roles,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(secret: str, token: str) -> dict:
    """验证 JWT, 返回 payload. 失败抛 jwt.PyJWTError."""
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})


def has_role(payload: dict, role: str) -> bool:
    return role in payload.get("roles", [])


# ---------- Manifest 签名封装 ----------

MANIFEST_SALT = b"upgrader-manifest-v1"


def sign_manifest(private_pem: str, files_json: bytes, version: str, published_at: str) -> str:
    """对清单整体签名. 输入: 文件清单 JSON 字节 + version + published_at."""
    blob = MANIFEST_SALT + version.encode() + b"\x00" + published_at.encode() + b"\x00" + files_json
    return sign_data(private_pem, blob)


def verify_manifest(
    public_pem: str,
    files_json: bytes,
    version: str,
    published_at: str,
    sig_b64: str,
) -> bool:
    blob = MANIFEST_SALT + version.encode() + b"\x00" + published_at.encode() + b"\x00" + files_json
    return verify_data(public_pem, blob, sig_b64)
