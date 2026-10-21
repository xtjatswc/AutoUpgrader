"""服务端版本历史 + 清单持久化.

- 每次 publish 生成一个版本: {version, published_at, sign, files}
- 持久化到 data_dir/manifests/<version>.json
- current_version 指向 data_dir/current (一行版本号)
"""
from __future__ import annotations

import json
import os
from typing import List, Optional


class VersionStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.manifest_dir = os.path.join(data_dir, "manifests")
        self.current_file = os.path.join(data_dir, "current")
        os.makedirs(self.manifest_dir, exist_ok=True)

    def save(self, manifest, sign: str) -> None:
        manifest.sign = sign
        path = os.path.join(self.manifest_dir, f"{_safe_version(manifest.version)}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(include_sign=True), f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def load(self, version: str) -> Optional[dict]:
        path = os.path.join(self.manifest_dir, f"{_safe_version(version)}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def current(self) -> Optional[str]:
        if not os.path.exists(self.current_file):
            return None
        with open(self.current_file, "r", encoding="utf-8") as f:
            v = f.read().strip()
        return v or None

    def set_current(self, version: str) -> None:
        tmp = self.current_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(version)
        os.replace(tmp, self.current_file)

    def history(self, limit: int = 50) -> List[str]:
        names = sorted(
            [n[:-5] for n in os.listdir(self.manifest_dir) if n.endswith(".json")],
            reverse=True,
        )
        return names[:limit]


def _safe_version(v: str) -> str:
    return "".join(c for c in v if c.isalnum() or c in "-._") or "v0"
