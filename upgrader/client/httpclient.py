"""客户端 HTTP 传输层 (标准库 urllib). 可注入 transport 便于测试."""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Optional


class Transport:
    """传输层抽象. 默认使用 urllib; 测试可替换为 FakeTransport."""

    def get_json(self, url: str, token: str, params: Optional[dict] = None) -> dict:
        raise NotImplementedError

    def get_bytes(self, url: str, token: str, params: Optional[dict] = None,
                   if_none_match: Optional[str] = None) -> tuple[bytes, int, Optional[str]]:
        """返回 (data, status_code, etag_or_none). 304 时 data 为空, status=304."""
        raise NotImplementedError


class UrllibTransport(Transport):
    def get_json(self, url: str, token: str, params: Optional[dict] = None) -> dict:
        full = self._build(url, params)
        req = urllib.request.Request(full, headers=self._headers(token))
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
        return json.loads(raw.decode())

    def get_bytes(self, url: str, token: str, params: Optional[dict] = None,
                  if_none_match: Optional[str] = None) -> tuple[bytes, int, Optional[str]]:
        full = self._build(url, params)
        headers = self._headers(token)
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        req = urllib.request.Request(full, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                etag = resp.headers.get("ETag")
                return data, resp.status, etag
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return b"", 304, if_none_match
            data = e.read()
            try:
                raise RuntimeError(f"HTTP {e.code}: {data.decode()[:200]}") from e
            except UnicodeDecodeError:
                raise RuntimeError(f"HTTP {e.code}") from e

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
