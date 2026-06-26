"""Format conversion using subconverter API."""

import io
import os
import re
import json
import base64
import logging
import urllib.parse
from typing import Optional, Dict, Any, List, Union

import requests
import yaml

from .platform_utils import get_tool_path, start_subconverter, stop_subconverter, make_session

logger = logging.getLogger(__name__)

_SHORT_ID_HEX_RE = re.compile(r"^[0-9a-fA-F]{2,16}$")
_SHORT_ID_LINE_RE = re.compile(r"^(\s+short-id:\s*)(\S+)\s*$", re.MULTILINE)


def normalize_reality_short_id(value: Any) -> Optional[str]:
    """Normalize REALITY short-id to a lowercase hex string mihomo accepts."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        logger.warning("Dropping REALITY short-id parsed as float: %r", value)
        return None
    if isinstance(value, int):
        text = str(value)
    else:
        text = str(value).strip()
        if text.lower() in ("", "null", "none"):
            return None

    text = text.lower()
    if not _SHORT_ID_HEX_RE.fullmatch(text):
        logger.warning("Dropping invalid REALITY short-id: %r", value)
        return None
    return text


def sanitize_proxy(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """Fix REALITY short-id types/values that break Go YAML parsers."""
    opts = proxy.get("reality-opts")
    if not isinstance(opts, dict) or "short-id" not in opts:
        return proxy

    normalized = normalize_reality_short_id(opts.get("short-id"))
    updated = dict(proxy)
    new_opts = dict(opts)
    if normalized is None:
        new_opts.pop("short-id", None)
    else:
        new_opts["short-id"] = normalized
    updated["reality-opts"] = new_opts
    return updated


def sanitize_proxies(proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize REALITY short-id fields for every proxy."""
    return [sanitize_proxy(p) for p in proxies]


def _quote_reality_short_ids(yaml_text: str) -> str:
    """Quote short-id scalars so YAML loaders do not coerce them to numbers."""

    def _quote(match: re.Match) -> str:
        prefix, raw = match.group(1), match.group(2)
        if raw[0] in "\"'":
            return match.group(0)
        return f'{prefix}"{raw}"'

    return _SHORT_ID_LINE_RE.sub(_quote, yaml_text)


def dump_clash_yaml(
    data: Dict[str, Any],
    stream: Optional[Union[io.TextIOBase, str, os.PathLike]] = None,
    *,
    allow_unicode: bool = True,
    sort_keys: bool = False,
) -> Optional[str]:
    """Dump Clash YAML with REALITY short-id values safely quoted."""
    text = yaml.dump(
        data,
        allow_unicode=allow_unicode,
        sort_keys=sort_keys,
    )
    text = _quote_reality_short_ids(text)
    if stream is None:
        return text
    if isinstance(stream, (str, os.PathLike)):
        with open(stream, "w", encoding="utf-8") as handle:
            handle.write(text)
        return None
    stream.write(text)
    return None


class SubConverter:
    """Subconverter API client."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.host = settings["subconverter"]["host"]
        self.port = settings["subconverter"]["port"]
        self.base_url = f"http://{self.host}:{self.port}/sub"
        self.process = None
        self.session = make_session(settings)

    def start(self) -> bool:
        """Start subconverter server.

        Returns:
            True if started successfully or already running.
        """
        self.process = start_subconverter(self.settings)
        return self.process is not None or self._is_running()

    def stop(self) -> None:
        """Stop subconverter server."""
        stop_subconverter(self.process)
        self.process = None

    def _is_running(self) -> bool:
        """Check if subconverter is running."""
        try:
            resp = self.session.get(f"http://{self.host}:{self.port}/version", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def convert(self, url: str, target: str = "clash",
                emoji: bool = True, list_mode: bool = True,
                timeout: int = 240) -> Optional[str]:
        """Convert subscription via subconverter API.

        Args:
            url: Subscription URL or local file path.
            target: Target format (clash, mixed, v2ray, singbox, etc.).
            emoji: Add emoji to node names.
            list_mode: Output as proxy list only (no groups/rules).
            timeout: Request timeout in seconds.

        Returns:
            Converted content string, or None on failure.
        """
        params = {
            "target": target,
            "url": url,
            "emoji": str(emoji).lower(),
            "list": str(list_mode).lower(),
        }

        try:
            resp = self.session.get(self.base_url, params=params, timeout=timeout)
            if resp.status_code == 200:
                logger.info("Converted subscription to %s (%d bytes)",
                            target, len(resp.content))
                return resp.text
            else:
                logger.error("subconverter returned status %d: %s",
                             resp.status_code, resp.text[:200])
                return None
        except requests.exceptions.Timeout:
            logger.error("subconverter request timed out after %ds", timeout)
            return None
        except Exception as e:
            logger.error("subconverter request failed: %s", e)
            return None

    def convert_local(self, file_path: str, target: str = "clash",
                      **kwargs) -> Optional[str]:
        """Convert a local file via subconverter API.

        Args:
            file_path: Local file path.
            target: Target format.
            **kwargs: Additional params passed to convert().

        Returns:
            Converted content string, or None on failure.
        """
        # subconverter accepts local file paths
        return self.convert(url=file_path, target=target, **kwargs)


class FormatConverter:
    """Pure Python format conversion utilities (no external tools)."""

    @staticmethod
    def base64_encode(content: str) -> str:
        """Encode content to base64."""
        return base64.b64encode(content.encode("utf-8")).decode("utf-8")

    @staticmethod
    def base64_decode(content: str) -> str:
        """Decode base64 content.

        Handles padding and common encoding issues.
        """
        # Fix padding
        missing_padding = len(content) % 4
        if missing_padding:
            content += "=" * (4 - missing_padding)
        try:
            return base64.b64decode(content).decode("utf-8")
        except (UnicodeDecodeError, Exception):
            # Try different encodings
            try:
                return base64.b64decode(content).decode("latin-1")
            except Exception:
                logger.error("Failed to decode base64 content")
                return ""

    @staticmethod
    def parse_clash_yaml(yaml_content: str) -> Optional[Dict[str, Any]]:
        """Parse Clash YAML content safely.

        Returns:
            Parsed dictionary or None on failure.
        """
        try:
            return yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            logger.error("Failed to parse Clash YAML: %s", e)
            return None

    @staticmethod
    def extract_proxies(clash_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract proxies list from Clash YAML data.

        Args:
            clash_data: Parsed Clash YAML dictionary.

        Returns:
            List of proxy dictionaries.
        """
        return clash_data.get("proxies", [])

    @staticmethod
    def build_clash_config(template: Dict[str, Any],
                           proxies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build complete Clash configuration from template and proxies.

        Args:
            template: Clash template dictionary.
            proxies: List of proxy dictionaries.

        Returns:
            Complete Clash configuration dictionary.
        """
        import copy
        config = copy.deepcopy(template)
        config["proxies"] = sanitize_proxies(proxies)

        proxy_names = [p["name"] for p in proxies]

        if "proxy-groups" in config:
            for group in config["proxy-groups"]:
                proxies_field = group.get("proxies", [])

                # Replace AUTO_FILL marker with all proxy names
                if proxies_field == "AUTO_FILL":
                    group["proxies"] = list(proxy_names)
                elif isinstance(proxies_field, list) and "AUTO_FILL" in proxies_field:
                    # Keep non-AUTO_FILL entries (e.g., "♻️ 自动选择", "DIRECT") and append all proxies
                    new_proxies = [p for p in proxies_field if p != "AUTO_FILL"]
                    new_proxies.extend(proxy_names)
                    group["proxies"] = new_proxies

        return config
