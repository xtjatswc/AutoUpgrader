"""manifest: 构建/序列化/diff."""
from __future__ import annotations

import json
import os

from upgrader.manifest import (
    Manifest,
    FileEntry,
    build_manifest,
    diff_manifest,
    Snapshot,
)


def test_build_manifest_scans_files(tmp_path):
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("2", encoding="utf-8")
    m = build_manifest(str(tmp_path), version="v1", published_at="2026-01-01T00:00:00Z")
    rels = sorted(e.rel for e in m.files)
    assert rels == ["a.txt", "sub/b.txt"]
    assert m.version == "v1"
    for e in m.files:
        assert e.sha256
        assert e.size > 0


def test_build_manifest_exclude(tmp_path):
    (tmp_path / "keep.txt").write_text("k", encoding="utf-8")
    (tmp_path / "skip.tmp").write_text("s", encoding="utf-8")
    m = build_manifest(str(tmp_path), version="v1", published_at="t", exclude_patterns=[".tmp"])
    rels = [e.rel for e in m.files]
    assert rels == ["keep.txt"]


def test_build_manifest_glob_patterns(tmp_path):
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "sub" / "c.log").write_text("c", encoding="utf-8")
    m = build_manifest(str(tmp_path), version="v1",
                       include_patterns=["*.py"], exclude_patterns=["sub/*"])
    rels = [e.rel for e in m.files]
    assert rels == ["a.py"]
    # glob '*' 在 fnmatch 中不跨 '/', sub/c.log 应被 *.log 匹配
    m2 = build_manifest(str(tmp_path), version="v1", exclude_patterns=["*.log"])
    assert [e.rel for e in m2.files] == ["a.py", "sub/b.py"]


def test_manifest_serialization_roundtrip():
    m = Manifest(version="v9", published_at="t",
                files=[FileEntry(rel="x", sha256="h", size=3, mtime=1.0)])
    sig = sign = "BASE64"
    m.sign = sig
    d = m.to_dict(include_sign=True)
    m2 = Manifest.from_dict(d)
    assert m2.version == "v9"
    assert m2.files[0].sha256 == "h"
    assert m2.sign == sig


def test_diff_add_update_delete():
    server = {
        "a.txt": FileEntry(rel="a.txt", sha256="new", size=10, mtime=1),
        "b.txt": FileEntry(rel="b.txt", sha256="same", size=5, mtime=1),
        # c.txt 不在服务端
    }
    client = {
        "a.txt": FileEntry(rel="a.txt", sha256="old", size=9, mtime=0),
        "b.txt": FileEntry(rel="b.txt", sha256="same", size=5, mtime=1),
        "c.txt": FileEntry(rel="c.txt", sha256="x", size=1, mtime=0),
    }
    d = diff_manifest(server, client)
    assert [e.rel for e in d.to_add] == []          # a 在客户端已有(需更新), b 已有(未变)
    assert [e.rel for e in d.to_update] == ["a.txt"]
    assert d.to_delete == ["c.txt"]
    assert d.unchanged == 1


def test_diff_empty():
    s = {"a": FileEntry(rel="a", sha256="h", size=1, mtime=0)}
    c = {"a": FileEntry(rel="a", sha256="h", size=1, mtime=0)}
    assert diff_manifest(s, c).empty()


def test_snapshot_save_load_roundtrip(tmp_path):
    snap = Snapshot(str(tmp_path / "snap.json"))
    entries = {"a": FileEntry(rel="a", sha256="h", size=1, mtime=1.5)}
    snap.save("v3", entries, "t")
    assert snap.current_version() == "v3"
    loaded = snap.load()
    assert loaded["a"].sha256 == "h"


def test_snapshot_empty_when_missing(tmp_path):
    snap = Snapshot(str(tmp_path / "nope" / "s.json"))
    assert snap.load() == {}
    assert snap.current_version() == ""
