"""Standalone mihomo (Clash.Meta) core lifecycle manager.

Spawns a dedicated mihomo process purely for node validation:

- separate OS process (we start/stop it ourselves)
- separate config with its own external-controller and ports
- never touches system proxy / TUN / routing

Works headless (CI) as long as the mihomo binary is present.
"""

from __future__ import annotations

import logging
import secrets
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .mihomo_client import MihomoClient, DEFAULT_TEST_URL, AUTO_SELECT_GROUP
from .config_loader import load_clash_template, PROJECT_ROOT
from .converter import FormatConverter
from .platform_utils import IS_WINDOWS, get_tool_path, wait_for_port

logger = logging.getLogger(__name__)

VALIDATE_CONFIG_NAME = "proxyharvest_validate.yaml"


def _find_free_port(preferred: int) -> int:
    """Return ``preferred`` if free, otherwise an OS-assigned free port."""
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
        except OSError:
            continue
    return preferred


class MihomoManager:
    """Start/stop a standalone mihomo core and expose its REST client."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.cfg = settings.get("mihomo", {})
        self.host = str(self.cfg.get("controller_host", "127.0.0.1"))
        self.secret = str(self.cfg.get("secret") or secrets.token_hex(8))
        self.test_url = str(self.cfg.get("test_url") or DEFAULT_TEST_URL)
        self.timeout_ms = int(self.cfg.get("timeout_ms") or 10000)
        self.startup_timeout = int(self.cfg.get("startup_timeout", 20))
        self.group_name = self.cfg.get("test_group", AUTO_SELECT_GROUP)

        data_dir = self.cfg.get("data_dir", "output/tmp/mihomo")
        self.data_dir = Path(data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = PROJECT_ROOT / self.data_dir

        self._process: Optional[subprocess.Popen] = None
        self._controller_port: Optional[int] = None
        self.client: Optional[MihomoClient] = None

    # ── binary ──────────────────────────────────────────────────────

    def resolve_binary(self) -> Path:
        binary = get_tool_path(self.settings, "mihomo")
        path = Path(binary)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise FileNotFoundError(
                f"mihomo 二进制未找到: {path}\n"
                "请运行 scripts/setup.ps1 下载，或手动从 "
                "https://github.com/MetaCubeX/mihomo/releases 获取后放到该路径。"
            )
        return path

    # ── config ──────────────────────────────────────────────────────

    def build_validate_config(self, proxies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a minimal Clash config dedicated to delay testing.

        Strips internal ``_`` keys, fills the url-test group with every node,
        and pins the external-controller / ports so the instance is isolated.
        """
        template_rel = self.cfg.get("template") or self.settings.get("paths", {}).get("clash_template")
        template = load_clash_template(template_rel)

        clean_proxies = []
        for proxy in proxies:
            item = {k: v for k, v in proxy.items() if not str(k).startswith("_")}
            clean_proxies.append(item)

        config = FormatConverter.build_clash_config(template, clean_proxies)

        self._controller_port = _find_free_port(int(self.cfg.get("controller_port", 9091)))
        mixed_port = _find_free_port(int(self.cfg.get("mixed_port", 7899)))

        config["external-controller"] = f"{self.host}:{self._controller_port}"
        config["secret"] = self.secret
        config["mixed-port"] = mixed_port
        config["allow-lan"] = False
        config["mode"] = "rule"
        # Validation never routes user traffic; keep TUN off explicitly.
        config["tun"] = {"enable": False}

        # Delay tests dial each proxy directly and never consult the rule engine,
        # so strip everything that would make mihomo fetch GeoIP/GeoSite databases
        # on startup (a GEOIP rule or DNS geoip fallback-filter triggers an MMDB
        # download that can hang/block and delay the controller coming up).
        config["rules"] = ["MATCH,DIRECT"]
        config["rule-providers"] = {}
        config["geodata-mode"] = False
        config["geo-auto-update"] = False
        config["dns"] = {
            "enable": True,
            "nameserver": ["223.5.5.5", "119.29.29.29", "8.8.8.8", "1.1.1.1"],
        }
        return config

    def write_config(self, config: Dict[str, Any]) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.data_dir / VALIDATE_CONFIG_NAME
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)
        return config_path

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self, proxies: List[Dict[str, Any]]) -> MihomoClient:
        """Launch mihomo with a validate config and return a ready REST client."""
        binary = self.resolve_binary()
        config = self.build_validate_config(proxies)
        config_path = self.write_config(config)

        logger.info(
            "Starting standalone mihomo: %s (%d proxies, API %s:%d)",
            binary, len(proxies), self.host, self._controller_port,
        )

        cmd = [str(binary), "-d", str(self.data_dir), "-f", str(config_path)]
        popen_kwargs: Dict[str, Any] = {
            "cwd": str(binary.parent),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        self._process = subprocess.Popen(cmd, **popen_kwargs)
        logger.info("mihomo started (PID: %d)", self._process.pid)

        if not wait_for_port(self.host, self._controller_port, timeout=self.startup_timeout):
            self.stop()
            raise ConnectionError(
                f"独立 mihomo 内核 API 未就绪 ({self.host}:{self._controller_port})。"
                "请检查二进制是否可执行、端口是否被占用。"
            )

        base_url = f"http://{self.host}:{self._controller_port}"
        self.client = MihomoClient.for_http(
            base_url,
            secret=self.secret,
            test_url=self.test_url,
            timeout_ms=self.timeout_ms,
        )
        # Confirm the API actually answers before handing control back.
        version = self.client.get_version()
        logger.info("Standalone mihomo ready: %s", version.get("version", version))
        return self.client

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                logger.warning("Force killed standalone mihomo")
            logger.info("Standalone mihomo stopped")
        except Exception as exc:  # pragma: no cover - best effort teardown
            logger.error("Error stopping mihomo: %s", exc)
        finally:
            self._process = None
            self.client = None

    def __enter__(self) -> "MihomoManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
