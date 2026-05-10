"""Platform detection and tool path resolution."""

import platform
import os
import logging
import subprocess
import time
import socket
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"


def get_proxies(settings: dict) -> dict:
    """Get proxy configuration for requests.

    Args:
        settings: Settings dictionary.

    Returns:
        Proxy dict for requests, or empty dict for no proxy.
        When empty dict is returned, callers should also set trust_env=False
        on their Session to avoid picking up broken system proxy settings.
    """
    proxy_url = settings.get("network", {}).get("proxy_url", "")
    if proxy_url:
        return {"http": proxy_url, "https": proxy_url}
    use_system_proxy = settings.get("network", {}).get("use_system_proxy", False)
    if use_system_proxy:
        import urllib.request
        return urllib.request.getproxies()
    # Default: no proxy - caller should set trust_env=False
    return {}


def make_session(settings: dict) -> requests.Session:
    """Create a requests Session with proper proxy configuration.

    When no proxy is configured, disables trust_env to avoid
    picking up broken system proxy settings from Windows registry.

    Args:
        settings: Settings dictionary.

    Returns:
        Configured requests.Session.
    """
    import requests as _requests
    proxies = get_proxies(settings)
    session = _requests.Session()
    session.proxies = proxies
    if not proxies:
        session.trust_env = False
    return session


def get_tool_path(settings: dict, tool_name: str) -> str:
    """Get the platform-specific binary path for a tool.

    Args:
        settings: Settings dictionary.
        tool_name: Tool name (e.g., 'subconverter', 'singtools').

    Returns:
        Absolute path to the binary.
    """
    tool_config = settings.get(tool_name, {})
    binary_paths = tool_config.get("binary_path", {})

    if IS_WINDOWS:
        key = "windows"
    elif IS_LINUX:
        key = "linux"
    elif IS_MACOS:
        # macOS uses linux binary if available, otherwise check for mac key
        key = "linux" if "linux" in binary_paths else "macos"
    else:
        key = "linux"

    path = binary_paths.get(key, "")
    if not path:
        raise FileNotFoundError(
            f"No binary path for {tool_name} on {platform.system()}"
        )
    return path


def ensure_dir(path: str) -> None:
    """Ensure directory exists, create if not."""
    Path(path).mkdir(parents=True, exist_ok=True)


def wait_for_port(host: str, port: int, timeout: int = 15) -> bool:
    """Wait for a TCP port to become available.

    Args:
        host: Host address.
        port: Port number.
        timeout: Maximum wait time in seconds.

    Returns:
        True if port is available, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                logger.info("Port %s:%d is ready", host, port)
                return True
        except (socket.error, OSError):
            pass
        time.sleep(0.5)

    logger.error("Timeout waiting for port %s:%d", host, port)
    return False


def start_subconverter(settings: dict) -> Optional[subprocess.Popen]:
    """Start subconverter process.

    Args:
        settings: Settings dictionary.

    Returns:
        subprocess.Popen instance or None on failure.
    """
    binary = get_tool_path(settings, "subconverter")
    binary_path = Path(binary)

    if not binary_path.exists():
        logger.error("subconverter binary not found: %s", binary)
        return None

    host = settings["subconverter"]["host"]
    port = settings["subconverter"]["port"]
    timeout = settings["subconverter"].get("startup_timeout", 15)

    # Check if already running
    if wait_for_port(host, port, timeout=1):
        logger.info("subconverter already running on %s:%d", host, port)
        return None

    cwd = str(binary_path.parent)

    if IS_WINDOWS:
        process = subprocess.Popen(
            [binary],
            cwd=cwd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process = subprocess.Popen(
            [binary],
            cwd=cwd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    logger.info("Starting subconverter (PID: %d)...", process.pid)

    if wait_for_port(host, port, timeout=timeout):
        logger.info("subconverter started successfully")
        return process
    else:
        logger.error("subconverter failed to start")
        process.kill()
        return None


def stop_subconverter(process: Optional[subprocess.Popen]) -> None:
    """Stop subconverter process.

    Args:
        process: subprocess.Popen instance from start_subconverter.
    """
    if process is None:
        return

    try:
        if IS_WINDOWS:
            process.terminate()
        else:
            process.kill()
        process.wait(timeout=5)
        logger.info("subconverter stopped")
    except subprocess.TimeoutExpired:
        process.kill()
        logger.warning("Force killed subconverter")
    except Exception as e:
        logger.error("Error stopping subconverter: %s", e)


def download_file(url: str, dest_path: str, desc: str = "",
                  session: requests.Session = None) -> bool:
    """Download a file from URL.

    Args:
        url: Download URL.
        dest_path: Destination file path.
        desc: Description for logging.
        session: requests.Session with proper proxy config, or None for default.

    Returns:
        True if download succeeded.
    """
    import requests as _requests

    ensure_dir(os.path.dirname(dest_path))
    logger.info("Downloading %s from %s ...", desc or os.path.basename(dest_path), url)

    try:
        s = session or _requests.Session()
        resp = s.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("Downloaded %s (%d bytes)", desc or os.path.basename(dest_path),
                     os.path.getsize(dest_path))
        return True
    except Exception as e:
        logger.error("Failed to download %s: %s", desc or url, e)
        return False
