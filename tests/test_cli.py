"""CLI: 参数解析 / token 签发 / init-keys / version."""
from __future__ import annotations

import json
import os

import pytest

from upgrader.cli import build_parser, main
from upgrader.crypto import verify_token


def test_parser_required_cmd():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_token_issue_and_verify(tmp_path):
    cfg_file = tmp_path / "cfg.json"
    # 使用 32 字节密钥避免 PyJWT 长度警告
    cfg_file.write_text(json.dumps({"jwt_secret": "a" * 32, "admin_jwt_secret": "b" * 32}))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["token", "--config", str(cfg_file), "--role", "client", "--subject", "u1"])
    assert rc == 0
    tok = buf.getvalue().strip()
    payload = verify_token("a" * 32, tok)
    assert payload["sub"] == "u1"
    assert "client" in payload["roles"]


def test_init_keys_cli(tmp_path, capsys):
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"data_dir": str(tmp_path / "sd"), "key_dir": str(tmp_path / "keys")}))
    rc = main(["init-keys", "--config", str(cfg_file)])
    assert rc == 0
    assert os.path.exists(os.path.join(str(tmp_path / "keys"), "priv.pem"))
    assert os.path.exists(os.path.join(str(tmp_path / "keys"), "pub.pem"))


def test_version_cli_no_snapshot(tmp_path, capsys):
    cfg_file = tmp_path / "ccfg.json"
    cfg_file.write_text(json.dumps({
        "snapshot_dir": str(tmp_path / "snap"),
        "target_dir": str(tmp_path / "tgt"),
        "backup_dir": str(tmp_path / "bak"),
    }))
    rc = main(["version", "--config", str(cfg_file)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["local_version"] == ""
