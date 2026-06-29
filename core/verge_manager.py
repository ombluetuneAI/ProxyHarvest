"""Local Clash Verge Rev core for node validation.

Discovers the Verge application data directory, temporarily swaps the
runtime ``config.yaml`` for a minimal delay-test config, reloads the running
mihomo core via external-controller, then restores the original config.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .config_loader import PROJECT_ROOT, get_path
from .converter import dump_clash_yaml
from .mihomo_client import MihomoClient, DEFAULT_TEST_URL
from .mihomo_ipc import ipc_available, resolve_verge_ipc_path
from .mihomo_manager import MihomoManager
from .platform_utils import IS_WINDOWS, ensure_dir

logger = logging.getLogger(__name__)

VERGE_RUNTIME_CONFIG_NAME = "clash-verge.yaml"
VERGE_VALIDATE_CONFIG_NAME = "proxyharvest_validate.yaml"
VERGE_RUNTIME_BACKUP_NAME = "proxyharvest_runtime_backup.yaml"
# Base template only (ports/tun); NOT the merged runtime config with proxies.
VERGE_BASE_CONFIG_NAME = "config.yaml"

# Keys copied from the live Verge config so reload keeps the same API endpoint
# and listener ports (avoids port conflicts with the running core).
_PRESERVE_CONFIG_KEYS = (
    "external-controller",
    "secret",
    "mixed-port",
    "socks-port",
    "port",
    "redir-port",
    "tproxy-port",
    "external-controller-pipe",
    "external-controller-cors",
    "allow-lan",
    "ipv6",
    "log-level",
)


def _verge_candidates() -> List[Path]:
    """Well-known Clash Verge / Clash Verge Rev data directories."""
    home = Path.home()
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
    xdg_data = os.environ.get("XDG_DATA_HOME", "")

    config_home = Path(xdg_config) if xdg_config else home / ".config"
    data_home = Path(xdg_data) if xdg_data else home / ".local" / "share"

    candidates: List[Path] = []
    if os.environ.get("CLASH_VERGE_APP_DIR"):
        candidates.append(Path(os.environ["CLASH_VERGE_APP_DIR"]))

    if appdata:
        candidates.extend([
            Path(appdata) / "io.github.clash-verge-rev.clash-verge-rev",
            Path(appdata) / "clash-verge",
        ])
    if localappdata:
        candidates.append(
            Path(localappdata) / "io.github.clash-verge-rev.clash-verge-rev"
        )

    candidates.extend([
        home / "Library" / "Application Support" / "io.github.clash-verge-rev.clash-verge-rev",
        home / "Library" / "Application Support" / "clash-verge",
        config_home / "io.github.clash-verge-rev.clash-verge-rev",
        config_home / "clash-verge",
        data_home / "io.github.clash-verge-rev.clash-verge-rev",
    ])
    return candidates


def find_verge_app_dir(explicit: Optional[str] = None) -> Path:
    """Locate a Clash Verge data directory that contains runtime configs."""
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.is_dir() and (path / VERGE_BASE_CONFIG_NAME).is_file():
            return path
        raise FileNotFoundError(
            f"指定的 Clash Verge 目录无效或缺少 {VERGE_BASE_CONFIG_NAME}: {path}"
        )

    seen: set[str] = set()
    for candidate in _verge_candidates():
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir() and (candidate / VERGE_BASE_CONFIG_NAME).is_file():
            logger.info("Found Clash Verge app dir: %s", candidate)
            return candidate

    searched = "\n  ".join(str(p) for p in _verge_candidates())
    raise FileNotFoundError(
        "未找到本地 Clash Verge 配置目录（需要存在 config.yaml）。\n"
        f"已检查:\n  {searched}\n"
        "请确认已安装 Clash Verge Rev 并至少运行过一次，"
        "或设置环境变量 CLASH_VERGE_APP_DIR 指向应用数据目录。"
    )


def parse_controller_from_config(
    config: Dict[str, Any],
    settings: dict,
) -> Tuple[str, int, str, str, int]:
    """Return host, port, secret, test_url, timeout_ms from a clash config."""
    cfg = settings.get("mihomo", {})
    ec = str(config.get("external-controller") or "127.0.0.1:9090")
    if "://" in ec:
        ec = ec.split("://", 1)[1]
    if ec.startswith("["):
        host, _, port_str = ec.rpartition("]:")
        host = host.lstrip("[")
    else:
        host, _, port_str = ec.rpartition(":")
    if not port_str:
        host, port_str = "127.0.0.1", ec
    secret = str(config.get("secret") or "")
    test_url = str(cfg.get("test_url") or DEFAULT_TEST_URL)
    timeout_ms = int(cfg.get("timeout_ms") or 10000)
    return host, int(port_str), secret, test_url, timeout_ms


class VergeManager:
    """Swap Clash Verge runtime config for validation, then restore."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.cfg = settings.get("clash_verge", {})
        self.mihomo_helper = MihomoManager(settings)
        self.app_dir: Optional[Path] = None
        self.runtime_config_path: Optional[Path] = None
        self.base_config_path: Optional[Path] = None
        self.validate_path: Optional[Path] = None
        self.runtime_backup_path: Optional[Path] = None
        self.project_backup_path: Optional[Path] = None
        self._client: Optional[MihomoClient] = None
        self._restored = False

    def resolve_app_dir(self) -> Path:
        explicit = self.cfg.get("app_dir") or os.environ.get("CLASH_VERGE_APP_DIR")
        self.app_dir = find_verge_app_dir(str(explicit) if explicit else None)
        self.base_config_path = self.app_dir / VERGE_BASE_CONFIG_NAME
        self.runtime_config_path = self.app_dir / VERGE_RUNTIME_CONFIG_NAME
        self.validate_path = self.app_dir / VERGE_VALIDATE_CONFIG_NAME
        self.runtime_backup_path = self.app_dir / VERGE_RUNTIME_BACKUP_NAME
        return self.app_dir

    def _backup_dir(self) -> Path:
        rel = self.cfg.get("backup_dir", "output/tmp/verge_validate")
        path = Path(rel)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        ensure_dir(str(path))
        return path

    def _load_runtime_config(self) -> Dict[str, Any]:
        """Load the merged Clash Verge runtime config (includes proxies)."""
        assert self.runtime_config_path is not None
        if not self.runtime_config_path.is_file():
            raise FileNotFoundError(
                f"Clash Verge 运行时配置不存在: {self.runtime_config_path}\n"
                "请在 Clash Verge 中激活一个订阅配置后再试。"
            )
        with open(self.runtime_config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_base_config(self) -> Dict[str, Any]:
        """Load the base mihomo template (ports/tun only, no proxies)."""
        assert self.base_config_path is not None
        with open(self.base_config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _make_client(self, config: Dict[str, Any]) -> MihomoClient:
        """Connect via HTTP external-controller or Clash Verge IPC pipe/socket."""
        host, port, secret, test_url, timeout_ms = parse_controller_from_config(
            config, self.settings
        )
        startup_timeout = int(self.cfg.get("startup_timeout", 10))
        assert self.app_dir is not None

        ipc_path = resolve_verge_ipc_path(config, str(self.app_dir))
        use_http = False
        if host and port:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    use_http = True
            except OSError:
                use_http = False

        if use_http:
            logger.info("Clash Verge API via HTTP %s:%d", host, port)
            client = MihomoClient.for_http(
                f"http://{host}:{port}",
                secret=secret,
                test_url=test_url,
                timeout_ms=timeout_ms,
            )
        elif ipc_path and ipc_available(ipc_path):
            logger.info("Clash Verge API via IPC %s", ipc_path)
            # Pipe IPC does not validate secret; keep empty to match mihomo behaviour.
            client = MihomoClient.for_ipc(
                ipc_path,
                secret="",
                test_url=test_url,
                timeout_ms=timeout_ms,
            )
        elif ipc_path and IS_WINDOWS:
            raise ConnectionError(
                f"Clash Verge 内核 API 未就绪。\n"
                f"HTTP {host}:{port} 未监听（外部控制器可能已关闭），"
                f"命名管道 {ipc_path} 也无法连接。\n"
                "请启动 Clash Verge 并确保 verge-mihomo 内核正在运行；"
                "或在设置中开启「外部控制器」。"
            )
        else:
            raise ConnectionError(
                f"Clash Verge 内核 API 未就绪 ({host}:{port})。\n"
                f"已等待 {startup_timeout}s。请启动 Clash Verge 并确保内核正在运行，"
                "或在设置中开启「外部控制器」。"
            )

        client.get_version()
        return client

    def connect(self) -> MihomoClient:
        """Connect to the running Verge mihomo external-controller."""
        if self.app_dir is None:
            self.resolve_app_dir()
        assert self.base_config_path is not None

        runtime = self._load_base_config()
        self._client = self._make_client(runtime)
        return self._client

    def _build_validate_config(
        self, proxies: List[Dict[str, Any]], original: Dict[str, Any]
    ) -> Dict[str, Any]:
        validate = self.mihomo_helper.build_validate_config(proxies)
        for key in _PRESERVE_CONFIG_KEYS:
            if key in original:
                validate[key] = original[key]
        # Validation only uses delay-test API; keep TUN off to reduce side effects.
        validate["tun"] = {"enable": False}
        return validate

    def _validate_config_path(self) -> Path:
        assert self.validate_path is not None
        return self.validate_path

    def _reload_core(self, client: MihomoClient, config_path: Path) -> None:
        path_str = str(config_path.resolve())
        code, data = client.request(
            "PUT",
            "/configs",
            params={"force": "true"},
            json_body={"path": path_str, "payload": ""},
        )
        if code not in (200, 204):
            raise RuntimeError(f"重载 Clash Verge 配置失败 (HTTP {code}): {data}")
        logger.info("Clash Verge config reloaded: %s", path_str)

    def start_validation(self, proxies: List[Dict[str, Any]]) -> MihomoClient:
        """Backup merged runtime config, load validate config, reload core."""
        self.resolve_app_dir()
        assert self.runtime_config_path is not None

        backed_up = False
        try:
            client = self.connect()
            base = self._load_base_config()

            backup_dir = self._backup_dir()
            self.project_backup_path = backup_dir / "verge_runtime_backup.yaml"
            assert self.runtime_config_path is not None
            assert self.runtime_backup_path is not None
            shutil.copy2(self.runtime_config_path, self.runtime_backup_path)
            shutil.copy2(self.runtime_config_path, self.project_backup_path)
            backed_up = True
            logger.info(
                "Backed up Verge runtime config -> %s (also %s)",
                self.runtime_backup_path,
                self.project_backup_path,
            )

            validate_config = self._build_validate_config(proxies, base)
            validate_path = self._validate_config_path()
            with open(validate_path, "w", encoding="utf-8") as f:
                dump_clash_yaml(validate_config, f)
            logger.info(
                "Wrote validate config (%d proxies) -> %s",
                len(proxies),
                validate_path,
            )

            self._reload_core(client, validate_path)

            client = self._make_client(validate_config)
            version = client.get_version()
            logger.info(
                "Clash Verge ready for validation: %s",
                version.get("version", version),
            )
            self._client = client
            self._restored = False
            return client
        except Exception:
            if backed_up:
                self.restore()
            raise

    def restore(self) -> None:
        """Reload the backed-up merged runtime config (with all proxies)."""
        if self._restored:
            return
        if self.app_dir is None:
            self.resolve_app_dir()

        restore_path = self.runtime_backup_path
        if restore_path is None or not restore_path.is_file():
            restore_path = self.runtime_config_path
        if restore_path is None or not restore_path.is_file():
            logger.warning("No Verge runtime config to restore")
            self._restored = True
            return

        try:
            logger.info("Restoring Verge runtime config from %s", restore_path)

            if self._client is not None:
                try:
                    self._reload_core(self._client, restore_path)
                except Exception as exc:
                    logger.error(
                        "重载还原配置失败，请手动在 Clash Verge 中重新激活当前订阅: %s",
                        exc,
                    )
        finally:
            self._restored = True
            for path in (self.validate_path, self.runtime_backup_path):
                if path and path.exists():
                    try:
                        path.unlink()
                    except OSError as exc:
                        logger.debug("Could not remove temp Verge file %s: %s", path, exc)

    def __enter__(self) -> "VergeManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.restore()
