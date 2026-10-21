"""文件清单生成 / 差异计算 / 持久化.

- 清单文件为 JSON: {version, published_at, files: [{rel, sha256, size, mtime}]}
- diff: 服务端清单 vs 客户端 snapshot → {to_add, to_update, to_delete, unchanged}
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class FileEntry:
    rel: str      # 相对路径, 正斜杠
    sha256: str
    size: int
    mtime: float

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "FileEntry":
        return FileEntry(rel=d["rel"], sha256=d["sha256"], size=int(d["size"]), mtime=float(d["mtime"]))


@dataclass
class Manifest:
    version: str
    published_at: str  # ISO8601
    files: List[FileEntry] = field(default_factory=list)
    sign: str = ""     # Ed25519 签名 (base64)

    def files_json(self) -> bytes:
        """参与签名的规范化 JSON (稳定排序, 去 sign 字段)."""
        return json.dumps(
            [f.to_dict() for f in self.files],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def to_dict(self, include_sign: bool = True) -> dict:
        d = {
            "version": self.version,
            "published_at": self.published_at,
            "files": [f.to_dict() for f in self.files],
        }
        if include_sign:
            d["sign"] = self.sign
        return d

    def to_json(self, include_sign: bool = True) -> str:
        return json.dumps(self.to_dict(include_sign), ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def from_dict(d: dict) -> "Manifest":
        return Manifest(
            version=d.get("version", ""),
            published_at=d.get("published_at", ""),
            files=[FileEntry.from_dict(x) for x in d.get("files", [])],
            sign=d.get("sign", ""),
        )

    @staticmethod
    def load_json(raw: str | bytes) -> "Manifest":
        if isinstance(raw, bytes):
            raw = raw.decode()
        return Manifest.from_dict(json.loads(raw))


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches(rel: str, pats: List[str]) -> bool:
    """子串或 glob (fnmatch) 匹配: 'sub/' 前缀仍有效, '*.so' 等 glob 同样支持."""
    return any((p in rel) or fnmatch.fnmatch(rel, p) for p in pats)


def build_manifest(
    publish_dir: str,
    version: str,
    published_at: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> Manifest:
    """扫描 publish_dir 生成清单.

    include/exclude 支持子串或 glob 模式 (相对路径, 如 '*.so', 'docs/*.md').
    """
    include = include_patterns or []
    exclude = exclude_patterns or []
    files: List[FileEntry] = []
    for root, _dirs, fnames in os.walk(publish_dir):
        for fn in fnames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, publish_dir).replace(os.sep, "/")
            if include and not _matches(rel, include):
                continue
            if exclude and _matches(rel, exclude):
                continue
            try:
                st = os.stat(full)
                sha = hash_file(full)
            except OSError:
                continue
            files.append(FileEntry(rel=rel, sha256=sha, size=st.st_size, mtime=st.st_mtime))
    files.sort(key=lambda e: e.rel)
    return Manifest(version=version, published_at=published_at or _now_iso(), files=files)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- Snapshot (客户端本地清单) ----------

class Snapshot:
    """客户端本地快照, 结构与 Manifest 相同 (但 sign 不使用)."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Dict[str, FileEntry]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {e["rel"]: FileEntry.from_dict(e) for e in raw.get("files", [])}

    def save(self, version: str, entries: Dict[str, FileEntry], published_at: str = "") -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        data = {
            "version": version,
            "published_at": published_at,
            "files": [e.to_dict() for e in entries.values()],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def current_version(self) -> str:
        if not os.path.exists(self.path):
            return ""
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f).get("version", "")


# ---------- Diff ----------

@dataclass
class Diff:
    to_add: List[FileEntry] = field(default_factory=list)     # 服务端有, 客户端无
    to_update: List[FileEntry] = field(default_factory=list)  # 两端都有但 hash/size 不同
    to_delete: List[str] = field(default_factory=list)        # 客户端有, 服务端无 (rel)
    unchanged: int = 0

    def empty(self) -> bool:
        return not (self.to_add or self.to_update or self.to_delete)

    def files_to_download(self) -> List[FileEntry]:
        return self.to_add + self.to_update

    def to_dict(self) -> dict:
        return {
            "to_add": [e.to_dict() for e in self.to_add],
            "to_update": [e.to_dict() for e in self.to_update],
            "to_delete": list(self.to_delete),
            "unchanged": self.unchanged,
        }


def diff_manifest(server_files: Dict[str, FileEntry], client_snap: Dict[str, FileEntry]) -> Diff:
    diff = Diff()
    for rel, e in server_files.items():
        if rel not in client_snap:
            diff.to_add.append(e)
        else:
            ce = client_snap[rel]
            if ce.sha256 != e.sha256 or ce.size != e.size:
                diff.to_update.append(e)
            else:
                diff.unchanged += 1
    for rel in client_snap:
        if rel not in server_files:
            diff.to_delete.append(rel)
    diff.to_add.sort(key=lambda e: e.rel)
    diff.to_update.sort(key=lambda e: e.rel)
    diff.to_delete.sort()
    return diff
