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
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

from .config_loader import PROJECT_ROOT, get_path
from .converter import dump_clash_yaml
from .mihomo_client import AUTO_SELECT_GROUP, MihomoClient, DEFAULT_TEST_URL
from .mihomo_ipc import ipc_available, resolve_verge_ipc_path
from .mihomo_manager import MihomoManager
from .platform_utils import IS_LINUX, IS_MACOS, IS_WINDOWS, ensure_dir

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


def verge_core_ready(config: Dict[str, Any], app_dir: str, settings: dict) -> bool:
    """Return True if the Verge mihomo external-controller or IPC is responding."""
    host, port, secret, test_url, timeout_ms = parse_controller_from_config(
        config, settings
    )
    ipc_path = resolve_verge_ipc_path(config, app_dir)

    if host and port:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                client = MihomoClient.for_http(
                    f"http://{host}:{port}",
                    secret=secret,
                    test_url=test_url,
                    timeout_ms=timeout_ms,
                )
                client.get_version()
                return True
        except OSError:
            pass
        except Exception:
            pass

    if ipc_path and ipc_available(ipc_path):
        return True
    return False


def find_verge_executable(settings: dict) -> Path:
    """Locate the Clash Verge Rev GUI binary."""
    cfg = settings.get("clash_verge", {})
    explicit = os.environ.get("CLASH_VERGE_EXE") or cfg.get("exe_path")
    if explicit:
        path = Path(str(explicit))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.is_file():
            return path
        raise FileNotFoundError(f"Clash Verge 可执行文件不存在: {path}")

    candidates: List[Path] = []
    if IS_WINDOWS:
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        local_programs = os.environ.get("LOCALAPPDATA", "")
        candidates.extend([
            Path(program_files) / "Clash Verge" / "clash-verge.exe",
            Path(program_files) / "Clash Verge" / "Clash Verge.exe",
        ])
        if local_programs:
            candidates.append(
                Path(local_programs) / "Programs" / "Clash Verge" / "clash-verge.exe"
            )
    elif IS_MACOS:
        candidates.append(
            Path("/Applications/Clash Verge.app/Contents/MacOS/clash-verge")
        )
    elif IS_LINUX:
        candidates.extend([
            Path.home() / ".local/bin/clash-verge",
            Path("/usr/bin/clash-verge"),
            Path("/usr/local/bin/clash-verge"),
        ])

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    hint = (
        "请设置环境变量 CLASH_VERGE_EXE 或在 config/settings.yaml 的 "
        "clash_verge.exe_path 中指定 Clash Verge 可执行文件路径。"
    )
    raise FileNotFoundError(f"未找到 Clash Verge 可执行文件。{hint}")


def wait_for_verge_core(settings: dict, timeout: int) -> bool:
    """Poll until the Verge mihomo API is reachable or timeout expires."""
    manager = VergeManager(settings)
    manager.resolve_app_dir()
    assert manager.app_dir is not None
    app_dir = str(manager.app_dir)
    base = manager._load_base_config()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if verge_core_ready(base, app_dir, settings):
            return True
        time.sleep(0.5)
    return False


def _start_verge_gui(settings: dict) -> None:
    """Launch the Clash Verge Rev GUI (detached)."""
    exe = find_verge_executable(settings)
    logger.info("Clash Verge core not running; launching %s", exe)

    popen_kwargs: dict = {
        "cwd": str(exe.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen([str(exe)], **popen_kwargs)


def ensure_verge_running(settings: dict) -> bool:
    """Start Clash Verge if the mihomo core API is not already up.

    Returns True if this call launched the GUI, False if the core was already up.
    """
    cfg = settings.get("clash_verge", {})
    startup_timeout = int(cfg.get("startup_timeout", 10))
    launch_timeout = int(cfg.get("launch_timeout", 60))

    if wait_for_verge_core(settings, timeout=1):
        logger.info("Clash Verge mihomo core already running")
        return False

    _start_verge_gui(settings)

    if wait_for_verge_core(settings, timeout=max(launch_timeout, startup_timeout)):
        logger.info("Clash Verge mihomo core is ready")
        return True

    raise RuntimeError(
        f"已启动 Clash Verge，但在 {max(launch_timeout, startup_timeout)}s 内 "
        "mihomo 内核 API 仍未就绪。请手动打开 Clash Verge 并确认内核正在运行。"
    )


def read_verge_mixed_port(settings: dict) -> int:
    """Read Clash Verge mixed-port from mihomo config or verge.yaml."""
    manager = VergeManager(settings)
    manager.resolve_app_dir()
    assert manager.app_dir is not None

    base = manager._load_base_config()
    if base.get("mixed-port"):
        return int(base["mixed-port"])

    verge_yaml = manager.app_dir / "verge.yaml"
    if verge_yaml.is_file():
        with open(verge_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if data.get("verge_mixed_port"):
            return int(data["verge_mixed_port"])

    return 7890


def wait_for_proxy_ready(
    proxy_url: str,
    *,
    test_url: str = "https://github.com",
    timeout: int = 60,
) -> bool:
    """Poll until HTTP(S) works through the local mixed proxy."""
    proxies = {"http": proxy_url, "https": proxy_url}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.head(
                test_url,
                proxies=proxies,
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def git_proxy_env(mixed_port: int) -> Dict[str, str]:
    """Environment variables so git uses the local Clash mixed port."""
    url = f"http://127.0.0.1:{mixed_port}"
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "ALL_PROXY": url,
        "GIT_HTTP_PROXY": url,
        "GIT_HTTPS_PROXY": url,
    }


def stop_verge_gui(settings: dict) -> None:
    """Stop Clash Verge GUI and mihomo core processes."""
    exe = find_verge_executable(settings)
    if IS_WINDOWS:
        kill_script = exe.parent / "kill-clash-verge.ps1"
        if kill_script.is_file():
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(kill_script),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            logger.info("Stopped Clash Verge via %s", kill_script.name)
            return
        for image in ("clash-verge.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe"):
            subprocess.run(
                ["taskkill", "/IM", image, "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        logger.info("Stopped Clash Verge processes (taskkill)")
        return

    subprocess.run(["pkill", "-f", "clash-verge"], check=False, capture_output=True)
    logger.info("Stopped Clash Verge (pkill)")


@dataclass
class VergeRuntimeSession:
    """Ensure Verge is up with a working proxy; stop GUI if we launched it."""

    settings: dict
    launched_by_script: bool = field(default=False, init=False)
    mixed_port: int = field(default=0, init=False)
    mode_note: str = field(default="", init=False)
    _previous_mode: Optional[str] = field(default=None, init=False)
    _mode_changed: bool = field(default=False, init=False)
    _selector_backups: Dict[str, str] = field(default_factory=dict, init=False)
    _tried_auto_select: bool = field(default=False, init=False)

    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.mixed_port}"

    def git_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.update(git_proxy_env(self.mixed_port))
        return env

    def _connect_client(self) -> MihomoClient:
        return VergeManager(self.settings).connect()

    def _ensure_git_proxy_mode(self) -> None:
        """Switch Clash from direct to rule (or configured mode) so git can use proxies."""
        cfg = self.settings.get("clash_verge", {})
        target_mode = str(cfg.get("git_proxy_mode", "rule")).lower()

        client = self._connect_client()
        code, data = client.request("GET", "/configs")
        if code != 200 or not isinstance(data, dict):
            logger.warning("Could not read Clash mode (HTTP %s); skipping mode switch", code)
            return

        current = str(data.get("mode") or "").lower()
        if current != "direct":
            return

        self._previous_mode = current
        code, body = client.request("PATCH", "/configs", json_body={"mode": target_mode})
        if code not in (200, 204):
            raise RuntimeError(
                f"无法将 Clash 代理模式从「直连」切换为「{target_mode}」(HTTP {code}): {body}"
            )

        self._mode_changed = True
        label = {"rule": "规则", "global": "全局", "direct": "直连"}.get(
            target_mode, target_mode
        )
        self.mode_note = f"已将代理模式从「直连」切换为「{label}」（供 GitHub 使用）。"
        logger.info("Clash mode switched for git: direct -> %s", target_mode)

    def _restore_clash_mode(self) -> None:
        if not self._mode_changed or not self._previous_mode:
            return
        try:
            client = self._connect_client()
            code, body = client.request(
                "PATCH", "/configs", json_body={"mode": self._previous_mode}
            )
            if code not in (200, 204):
                logger.warning(
                    "Could not restore Clash mode to %s (HTTP %s): %s",
                    self._previous_mode,
                    code,
                    body,
                )
            else:
                logger.info("Restored Clash mode to %s", self._previous_mode)
        except Exception as exc:
            logger.warning("Could not restore Clash mode to %s: %s", self._previous_mode, exc)
        finally:
            self._mode_changed = False
            self._previous_mode = None

    def _auto_select_target(self) -> str:
        cfg = self.settings.get("clash_verge", {})
        mihomo_cfg = self.settings.get("mihomo", {})
        return str(
            cfg.get("auto_select_name")
            or mihomo_cfg.get("test_group")
            or AUTO_SELECT_GROUP
        )

    def _switch_selectors_to_auto(self) -> bool:
        """Point selector groups at the auto-select outbound; backup previous choices."""
        cfg = self.settings.get("clash_verge", {})
        if not cfg.get("auto_switch_on_proxy_fail", True):
            return False

        target = self._auto_select_target()
        only_groups: List[str] = list(cfg.get("git_selector_groups") or [])
        client = self._connect_client()
        try:
            proxies = client.get_proxies()
        except RuntimeError as exc:
            logger.warning("Could not list proxy groups: %s", exc)
            return False

        changed = False
        for group_name, info in proxies.items():
            if not isinstance(info, dict) or info.get("type") != "Selector":
                continue
            if only_groups and group_name not in only_groups:
                continue
            members = info.get("all") or []
            if target not in members:
                continue
            current = str(info.get("now") or "")
            if current == target:
                continue

            self._selector_backups[group_name] = current
            try:
                client.select_proxy_in_group(group_name, target)
                changed = True
                logger.info(
                    "Selector %s: %s -> %s (for GitHub)",
                    group_name,
                    current or "(empty)",
                    target,
                )
            except RuntimeError as exc:
                self._selector_backups.pop(group_name, None)
                logger.warning("%s", exc)

        if changed:
            note = f"已将含「{target}」的策略组临时切换为自动选择（脚本结束后还原）。"
            self.mode_note = f"{self.mode_note}\n       {note}".strip() if self.mode_note else note
        return changed

    def _restore_selector_backups(self) -> None:
        if not self._selector_backups:
            return
        try:
            client = self._connect_client()
            for group_name, previous in list(self._selector_backups.items()):
                try:
                    client.select_proxy_in_group(group_name, previous)
                    logger.info("Restored selector %s -> %s", group_name, previous)
                except RuntimeError as exc:
                    logger.warning(
                        "Could not restore selector %s to %s: %s",
                        group_name,
                        previous,
                        exc,
                    )
        except Exception as exc:
            logger.warning("Could not restore proxy group selections: %s", exc)
        finally:
            self._selector_backups.clear()

    def _cleanup_on_proxy_failure(self) -> None:
        if self._selector_backups and not self.launched_by_script:
            self._restore_selector_backups()
        if self._mode_changed and not self.launched_by_script:
            self._restore_clash_mode()
        if self.launched_by_script:
            stop_verge_gui(self.settings)
            self.launched_by_script = False

    def _wait_github_proxy(self, cfg: dict) -> bool:
        proxy_timeout = int(cfg.get("proxy_ready_timeout", 60))
        retry_timeout = int(cfg.get("proxy_retry_timeout", 45))

        if wait_for_proxy_ready(self.proxy_url(), timeout=proxy_timeout):
            return True

        if self._switch_selectors_to_auto():
            self._tried_auto_select = True
            if wait_for_proxy_ready(self.proxy_url(), timeout=retry_timeout):
                return True

        return False

    def __enter__(self) -> "VergeRuntimeSession":
        cfg = self.settings.get("clash_verge", {})
        proxy_timeout = int(cfg.get("proxy_ready_timeout", 60))

        self.launched_by_script = ensure_verge_running(self.settings)
        self.mixed_port = read_verge_mixed_port(self.settings)
        self._ensure_git_proxy_mode()

        if not self._wait_github_proxy(cfg):
            self._cleanup_on_proxy_failure()
            tried_auto = "（已尝试切换为自动选择仍失败）" if self._tried_auto_select else ""
            raise RuntimeError(
                f"本地代理 {self.proxy_url()} 在 {proxy_timeout}s 内无法访问 GitHub{tried_auto}。\n"
                "脚本已停止（若由本脚本启动的 Clash Verge 已关闭；临时改过的模式/策略组已尽量还原）。\n"
                "请排查后重试：\n"
                "  1. Clash Verge 代理模式为「规则」或「全局」，且策略组已选中可用节点（非 DIRECT/REJECT）\n"
                "  2. 订阅未过期，在 Verge 里对当前配置做一次「测试」或更新订阅\n"
                "  3. mixed-port 与 config.yaml 一致（当前脚本使用 mixed-port "
                f"{self.mixed_port}）\n"
                "  4. 浏览器经系统代理或 127.0.0.1 能否打开 https://github.com\n"
                "  5. 仅需本地验证、暂不拉 GitHub 时：先手动 git pull，或增大 "
                "config/settings.yaml 中 clash_verge.proxy_ready_timeout"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._selector_backups and not self.launched_by_script:
            self._restore_selector_backups()
        if self._mode_changed and not self.launched_by_script:
            self._restore_clash_mode()
        if self.launched_by_script:
            logger.info("Stopping Clash Verge (launched by this script)")
            stop_verge_gui(self.settings)
            self.launched_by_script = False


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
