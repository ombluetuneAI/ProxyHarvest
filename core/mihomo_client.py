"""Mihomo (Clash.Meta) external-controller HTTP client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)

AUTO_SELECT_GROUP = "♻️ 自动选择"
DEFAULT_TEST_URL = "http://www.gstatic.com/generate_204"


@dataclass
class MihomoRuntimeConfig:
    """Connection settings for a Mihomo REST API endpoint."""

    secret: str
    test_url: str
    timeout_ms: int
    http_base: str


class MihomoClient:
    """Minimal Mihomo REST client for delay tests."""

    def __init__(self, runtime: MihomoRuntimeConfig):
        self.runtime = runtime

    @classmethod
    def for_http(
        cls,
        base_url: str,
        secret: str = "",
        test_url: str = DEFAULT_TEST_URL,
        timeout_ms: int = 10000,
    ) -> "MihomoClient":
        runtime = MihomoRuntimeConfig(
            secret=secret or "",
            test_url=test_url or DEFAULT_TEST_URL,
            timeout_ms=int(timeout_ms or 10000),
            http_base=base_url.rstrip("/"),
        )
        return cls(runtime)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Tuple[int, Any]:
        import requests

        headers = {}
        if self.runtime.secret:
            headers["Authorization"] = f"Bearer {self.runtime.secret}"
        url = f"{self.runtime.http_base}{path}"
        timeout = max(30, self.runtime.timeout_ms // 1000 + 30)
        resp = requests.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
        if not resp.content:
            return resp.status_code, {}
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text

    def get_version(self) -> dict:
        code, data = self.request("GET", "/version")
        if code != 200:
            raise ConnectionError(f"Mihomo API unreachable (HTTP {code}): {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def test_group_delay(
        self,
        group_name: str,
        url: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict:
        name = quote(group_name, safe="")
        params = {
            "url": url or self.runtime.test_url,
            "timeout": str(timeout_ms or self.runtime.timeout_ms),
        }
        code, data = self.request("GET", f"/group/{name}/delay", params=params)
        if code != 200:
            raise RuntimeError(f"Group delay test failed ({code}): {data}")
        if isinstance(data, dict):
            return data
        return {"raw": data}

    def test_proxy_delay(
        self,
        proxy_name: str,
        url: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> Optional[int]:
        name = quote(proxy_name, safe="")
        params = {
            "url": url or self.runtime.test_url,
            "timeout": str(timeout_ms or self.runtime.timeout_ms),
        }
        code, data = self.request("GET", f"/proxies/{name}/delay", params=params)
        if code == 200 and isinstance(data, dict):
            return int(data.get("delay") or 0)
        return None
