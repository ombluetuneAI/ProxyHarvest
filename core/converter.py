"""Format conversion using subconverter API."""

import os
import json
import base64
import logging
import urllib.parse
from typing import Optional, Dict, Any, List

import requests
import yaml

from .platform_utils import get_tool_path, start_subconverter, stop_subconverter, make_session

logger = logging.getLogger(__name__)


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
        config["proxies"] = proxies

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
