"""Mihomo REST API over OS IPC (Windows named pipe / Unix domain socket).

Clash Verge Rev keeps external-controller HTTP disabled by default and
exposes the same REST API via ``external-controller-pipe`` instead.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

def normalize_ipc_path(ipc_path: str) -> str:
    """Normalize Mihomo IPC paths from YAML / OS-specific forms."""
    if not ipc_path:
        return ""
    path = str(ipc_path).strip()
    if not path:
        return ""
    normalized = path.replace("/", "\\")
    lower = normalized.lower()
    if lower.startswith(r"\\.\pipe") or lower.startswith(r"\.\pipe"):
        if not normalized.startswith(r"\\.\pipe"):
            normalized = "\\" + normalized
        return normalized
    return path


def is_windows_named_pipe(ipc_path: str) -> bool:
    lower = normalize_ipc_path(ipc_path).lower()
    return lower.startswith(r"\\.\pipe")


def _build_http_request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    secret: str = "",
) -> bytes:
    if params:
        path = f"{path}?{urlencode(params)}"
    payload = json.dumps(json_body) if json_body is not None else None
    lines = [
        f"{method.upper()} {path} HTTP/1.1",
        "Host: localhost",
        "Connection: close",
    ]
    if secret:
        lines.append(f"Authorization: Bearer {secret}")
    if payload is not None:
        lines.append("Content-Type: application/json")
        lines.append(f"Content-Length: {len(payload)}")
    lines.append("")
    body = payload if payload is not None else ""
    return ("\r\n".join(lines) + "\r\n" + body).encode("utf-8")


def _parse_http_response(raw: bytes) -> Tuple[int, Any]:
    if not raw:
        return 0, ""
    header_end = raw.find(b"\r\n\r\n")
    if header_end < 0:
        return 0, raw.decode("utf-8", errors="replace")
    header_blob, body = raw[:header_end], raw[header_end + 4 :]
    status_line = header_blob.split(b"\r\n", 1)[0]
    try:
        status = int(status_line.split(b" ", 2)[1])
    except (IndexError, ValueError):
        status = 0
    if not body:
        return status, {}
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, body.decode("utf-8", errors="replace")


def _windows_pipe_exchange(pipe_path: str, request: bytes, timeout: float) -> bytes:
    try:
        import pywintypes
        import win32file
        import win32pipe
    except ImportError as exc:
        raise ImportError(
            "Windows 命名管道访问需要 pywin32：pip install pywin32\n"
            "或在 Clash Verge 设置中开启「外部控制器」以使用 HTTP API。"
        ) from exc

    import time

    deadline = time.monotonic() + (timeout if timeout > 0 else 30.0)
    handle = None
    try:
        while time.monotonic() < deadline:
            try:
                handle = win32file.CreateFile(
                    pipe_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                break
            except pywintypes.error as exc:
                if exc.winerror == 231:
                    remaining_ms = int(max(1, (deadline - time.monotonic()) * 1000))
                    win32pipe.WaitNamedPipe(pipe_path, remaining_ms)
                    continue
                raise ConnectionError(
                    f"无法连接 Clash Verge 命名管道 {pipe_path}: {exc}"
                ) from exc
        else:
            raise TimeoutError(f"命名管道连接超时 ({pipe_path})")

        win32file.WriteFile(handle, request)
        _, data = win32file.ReadFile(handle, 65536)
        return data or b""
    finally:
        if handle is not None:
            win32file.CloseHandle(handle)


def _unix_socket_exchange(socket_path: str, request: bytes, timeout: float) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout if timeout > 0 else 30.0)
        sock.connect(socket_path)
        sock.sendall(request)
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


def ipc_request(
    ipc_path: str,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    secret: str = "",
    timeout: float = 30.0,
) -> Tuple[int, Any]:
    """Send one HTTP request over a Mihomo IPC endpoint."""
    request = _build_http_request(
        method, path, params=params, json_body=json_body, secret=secret
    )
    ipc_path = normalize_ipc_path(ipc_path)
    if is_windows_named_pipe(ipc_path):
        raw = _windows_pipe_exchange(ipc_path, request, timeout)
    else:
        raw = _unix_socket_exchange(ipc_path, request, timeout)
    return _parse_http_response(raw)


def ipc_available(ipc_path: str) -> bool:
    """Return True if the Mihomo IPC endpoint appears reachable."""
    if not ipc_path:
        return False
    try:
        code, _ = ipc_request(ipc_path, "GET", "/version", timeout=3.0)
        return code == 200
    except Exception:
        return False


def resolve_verge_ipc_path(config: Dict[str, Any], app_dir: Optional[str] = None) -> str:
    """Pick the Mihomo IPC path from runtime or Clash Verge template config."""
    pipe = config.get("external-controller-pipe")
    if pipe:
        return normalize_ipc_path(str(pipe))
    if app_dir:
        from pathlib import Path

        import yaml

        for name in ("clash-verge.yaml", "clash-verge-check.yaml"):
            path = Path(app_dir) / name
            if not path.is_file():
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            pipe = data.get("external-controller-pipe")
            if pipe:
                return normalize_ipc_path(str(pipe))
        return str(Path(app_dir) / DEFAULT_UNIX_SOCKET)
    return ""
