"""crypto: 密钥生成/签名/验签/JWT."""
from __future__ import annotations

import pytest

from upgrader.crypto import (
    KeyPair,
    init_keys,
    sign_data,
    verify_data,
    sign_manifest,
    verify_manifest,
    issue_token,
    verify_token,
    has_role,
)


def test_keypair_generate_and_persist(tmp_path):
    key_dir = str(tmp_path / "keys")
    kp = init_keys(key_dir)
    assert kp.private_pem
    assert kp.public_pem
    # 再次加载应一致
    kp2 = init_keys(key_dir)
    assert kp2.private_pem == kp.private_pem
    assert kp2.public_pem == kp.public_pem


def test_sign_verify_roundtrip():
    kp = KeyPair.generate()
    data = b"hello world"
    sig = sign_data(kp.private_pem, data)
    assert verify_data(kp.public_pem, data, sig)
    # 篡改数据应失败
    assert not verify_data(kp.public_pem, b"tampered", sig)


def test_sign_verify_tampered_sig():
    kp = KeyPair.generate()
    data = b"payload"
    sig = sign_data(kp.private_pem, data)
    bad = "AAAA" + sig[4:]
    assert not verify_data(kp.public_pem, data, bad)


def test_manifest_sign_verify():
    kp = KeyPair.generate()
    files_json = b'[{"rel":"a","sha256":"x","size":1,"mtime":0}]'
    sig = sign_manifest(kp.private_pem, files_json, "v1", "2026-01-01T00:00:00Z")
    assert verify_manifest(kp.public_pem, files_json, "v1", "2026-01-01T00:00:00Z", sig)
    # version 不匹配
    assert not verify_manifest(kp.public_pem, files_json, "v2", "2026-01-01T00:00:00Z", sig)


def test_jwt_issue_verify():
    secret = "a" * 32
    tok = issue_token(secret, "alice", ["client"], ttl=60)
    payload = verify_token(secret, tok)
    assert payload["sub"] == "alice"
    assert has_role(payload, "client")
    assert not has_role(payload, "admin")


def test_jwt_wrong_secret_rejected():
    secretA = "secretA" + "a" * 25
    secretB = "secretB" + "b" * 25
    tok = issue_token(secretA, "bob", ["admin"])
    with pytest.raises(Exception):
        verify_token(secretB, tok)


def test_jwt_expired_rejected():
    secret = "c" * 32
    tok = issue_token(secret, "u", ["client"], ttl=-10)  # 已过期
    with pytest.raises(Exception):
        verify_token(secret, tok)
