"""客户端 HTTP 传输层 (标准库 urllib). 可注入 transport 便于测试."""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


class Transport:
    """传输层抽象. 默认使用 urllib; 测试可替换为 FakeTransport."""

    def get_json(self, url: str, token: str, params: Optional[dict] = None) -> dict:
        raise NotImplementedError

    def get_bytes(self, url: str, token: str, params: Optional[dict] = None) -> tuple[bytes, int]:
        """返回 (data, status_code). 仅 200 视为成功内容."""
        raise NotImplementedError

    def get_file(self, url: str, token: str, params: Optional[dict],
                 dst_path: str) -> int:
        """下载到 dst_path (由调用方保证与目标同卷), 返回状态码. 默认基于 get_bytes."""
        data, status = self.get_bytes(url, token, params)
        if status == 200:
            d = os.path.dirname(dst_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(dst_path, "wb") as f:
                f.write(data)
        return status


class UrllibTransport(Transport):
    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout

    def get_json(self, url: str, token: str, params: Optional[dict] = None) -> dict:
        full = self._build(url, params)
        req = urllib.request.Request(full, headers=self._headers(token))
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode())

    def get_bytes(self, url: str, token: str, params: Optional[dict] = None) -> tuple[bytes, int]:
        full = self._build(url, params)
        req = urllib.request.Request(full, headers=self._headers(token))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), resp.status
        except urllib.error.HTTPError as e:
            data = e.read()
            msg = data.decode(errors="replace")[:200]
            raise RuntimeError(f"HTTP {e.code}: {msg}") from e

    def get_file(self, url: str, token: str, params: Optional[dict],
                 dst_path: str) -> int:
        full = self._build(url, params)
        req = urllib.request.Request(full, headers=self._headers(token))
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: unexpected status for {url}")
            d = os.path.dirname(dst_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(dst_path, "wb") as f:
                shutil.copyfileobj(resp, f, 1024 * 1024)
            return resp.status

    @staticmethod
    def _build(url: str, params: Optional[dict]) -> str:
        if not params:
            return url
        q = "&".join(f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}" for k, v in params.items())
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{q}"

    @staticmethod
    def _headers(token: str) -> dict:
        h = {"Accept": "application/json, application/octet-stream"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h
