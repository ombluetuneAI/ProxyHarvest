"""Proxy node collector from subscription sources."""

import logging
import re
from typing import List, Dict, Any, Optional

import requests
import yaml

from .converter import SubConverter
from .merger import merge_and_validate

logger = logging.getLogger(__name__)

# Country code to emoji mapping
COUNTRY_EMOJI = {
    "US": "\U0001F1FA\U0001F1F8",
    "CN": "\U0001F1E8\U0001F1F3",
    "HK": "\U0001F1ED\U0001F1F0",
    "JP": "\U0001F1EF\U0001F1F5",
    "KR": "\U0001F1F0\U0001F1F7",
    "SG": "\U0001F1F8\U0001F1EC",
    "TW": "\U0001F1F9\U0001F1FC",
    "GB": "\U0001F1EC\U0001F1E7",
    "DE": "\U0001F1E9\U0001F1EA",
    "FR": "\U0001F1EB\U0001F1F7",
    "RU": "\U0001F1F7\U0001F1FA",
    "AU": "\U0001F1E6\U0001F1FA",
    "CA": "\U0001F1E8\U0001F1E6",
    "NL": "\U0001F1F3\U0001F1F1",
    "SE": "\U0001F1F8\U0001F1EA",
    "NO": "\U0001F1F3\U0001F1F4",
    "FI": "\U0001F1EB\U0001F1EE",
    "DK": "\U0001F1E9\U0001F1F0",
    "PL": "\U0001F1F5\U0001F1F1",
    "CZ": "\U0001F1E8\U0001F1FF",
    "CH": "\U0001F1E8\U0001F1ED",
    "IT": "\U0001F1EE\U0001F1F9",
    "ES": "\U0001F1EA\U0001F1F8",
    "PT": "\U0001F1F5\U0001F1F9",
    "BR": "\U0001F1E7\U0001F1F7",
    "IN": "\U0001F1EE\U0001F1F3",
    "ID": "\U0001F1EE\U0001F1E9",
    "TH": "\U0001F1F9\U0001F1ED",
    "VN": "\U0001F1FB\U0001F1F3",
    "MY": "\U0001F1F2\U0001F1FE",
    "PH": "\U0001F1F5\U0001F1ED",
    "TR": "\U0001F1F9\U0001F1F7",
    "AE": "\U0001F1E6\U0001F1EA",
    "SA": "\U0001F1F8\U0001F1E6",
    "IL": "\U0001F1EE\U0001F1F1",
    "EG": "\U0001F1EA\U0001F1EC",
    "ZA": "\U0001F1FF\U0001F1E6",
    "NG": "\U0001F1F3\U0001F1EC",
    "KE": "\U0001F1F0\U0001F1EA",
    "AR": "\U0001F1E6\U0001F1F7",
    "CL": "\U0001F1E8\U0001F1F1",
    "MX": "\U0001F1F2\U0001F1FD",
    "CO": "\U0001F1E8\U0001F1F4",
    "PE": "\U0001F1F5\U0001F1EA",
    "VE": "\U0001F1FB\U0001F1EA",
}


class Collector:
    """Collect proxy nodes from subscription sources."""

    def __init__(self, settings: dict, subconverter: SubConverter):
        self.settings = settings
        self.subconverter = subconverter
        self.timeout = settings.get("collector", {}).get("timeout", 240)
        self.max_retries = settings.get("collector", {}).get("max_retries", 2)
        self.validate_config = settings.get("collector", {}).get("validate", {})

    def collect_all(self, sources: list) -> List[Dict[str, Any]]:
        """Collect nodes from all enabled sources.

        Args:
            sources: List of source dictionaries.

        Returns:
            List of collected proxy dictionaries.
        """
        all_proxies = []

        for source in sources:
            if not source.get("enabled", True):
                logger.info("Skipping disabled source #%d: %s",
                            source.get("id", -1), source.get("remarks", ""))
                continue

            source_id = source.get("id", -1)
            remarks = source.get("remarks", "")
            url = source.get("url", "")

            if not url:
                logger.warning("Source #%d (%s) has no URL", source_id, remarks)
                continue

            logger.info("Collecting from source #%d: %s", source_id, remarks)
            proxies = self._collect_from_source(url, source_id)

            if proxies:
                logger.info("Collected %d proxies from source #%d", len(proxies), source_id)
                all_proxies.extend(proxies)
            else:
                logger.warning("No proxies collected from source #%d", source_id)

        # Deduplicate and validate
        if all_proxies:
            all_proxies = merge_and_validate(all_proxies, self.validate_config)
            logger.info("Total collected: %d proxies (after dedup)", len(all_proxies))

        return all_proxies

    def _collect_from_source(self, url: str, source_id: int) -> List[Dict[str, Any]]:
        """Collect proxies from a single source URL (may contain multiple URLs separated by |).

        Args:
            url: Subscription URL(s), pipe-separated.
            source_id: Source ID for logging.

        Returns:
            List of proxy dictionaries.
        """
        all_proxies = []
        urls = url.split("|")

        for idx, sub_url in enumerate(urls):
            sub_url = sub_url.strip()
            if not sub_url:
                continue

            logger.info("  [%d/%d] Fetching: %s", idx + 1, len(urls), sub_url[:80])
            proxies = self._fetch_and_parse(sub_url, source_id)
            all_proxies.extend(proxies)

        return all_proxies

    def _fetch_and_parse(self, sub_url: str, source_id: int,
                         retries: int = 0) -> List[Dict[str, Any]]:
        """Fetch subscription and parse as Clash YAML.

        Args:
            sub_url: Subscription URL.
            source_id: Source ID.
            retries: Current retry count.

        Returns:
            List of proxy dictionaries.
        """
        try:
            # Use subconverter to convert to clash format
            yaml_content = self.subconverter.convert(
                url=sub_url,
                target="clash",
                emoji=True,
                list_mode=True,
                timeout=self.timeout
            )

            if not yaml_content:
                if retries < self.max_retries:
                    logger.warning("Retry %d/%d for source #%d",
                                   retries + 1, self.max_retries, source_id)
                    return self._fetch_and_parse(sub_url, source_id, retries + 1)
                return []

            proxies = self._parse_clash_proxies(yaml_content)
            return proxies

        except Exception as e:
            logger.error("Failed to fetch source #%d: %s", source_id, e)
            if retries < self.max_retries:
                return self._fetch_and_parse(sub_url, source_id, retries + 1)
            return []

    def _parse_clash_proxies(self, yaml_content: str) -> List[Dict[str, Any]]:
        """Parse Clash YAML content and extract proxies.

        Args:
            yaml_content: Raw YAML content string.

        Returns:
            List of proxy dictionaries.
        """
        proxies = []

        try:
            data = yaml.safe_load(yaml_content)
            if not data:
                return []

            # Extract proxies (can be under 'proxies' or as raw list)
            if "proxies" in data:
                raw_proxies = data["proxies"]
            else:
                # Some subscriptions return just a list
                raw_proxies = data if isinstance(data, list) else []
                if isinstance(raw_proxies, dict):
                    raw_proxies = [raw_proxies]

            for item in raw_proxies:
                if not isinstance(item, dict):
                    continue

                # Skip groups
                if item.get("type") in ("select", "url-test", "fallback", "load-balance"):
                    continue

                # Validate minimum required fields
                if "name" not in item or "server" not in item:
                    continue

                # Apply validation rules
                if self._validate_proxy(item):
                    proxies.append(item)

        except yaml.YAMLError as e:
            logger.error("Failed to parse YAML: %s", e)

        return proxies

    def _validate_proxy(self, proxy: Dict[str, Any]) -> bool:
        """Validate a proxy against configured rules.

        Args:
            proxy: Proxy dictionary.

        Returns:
            True if valid, False to skip.
        """
        ptype = proxy.get("type", "").lower()

        # SS validation
        if ptype == "ss":
            cipher = proxy.get("cipher", "").lower()
            ss_ciphers = self.validate_config.get("ss_ciphers", [])
            if ss_ciphers and cipher not in ss_ciphers:
                logger.debug("Skipping SS with unsupported cipher: %s", cipher)
                return False

            # SS plugin validation
            plugin = proxy.get("plugin", "")
            ss_plugins = self.validate_config.get("ss_plugins", [])
            if plugin and ss_plugins and plugin not in ss_plugins:
                logger.debug("Skipping SS with unsupported plugin: %s", plugin)
                return False

        # VMess validation
        elif ptype == "vmess":
            uuid_val = proxy.get("uuid", "")
            if len(uuid_val) != 36:
                logger.debug("Skipping VMess with invalid UUID length: %s", uuid_val)
                return False

            # VMess h2/grpc must have TLS
            network = proxy.get("network", "")
            tls = proxy.get("tls", "")
            if network in ("h2", "grpc") and not tls:
                logger.debug("Skipping VMess with network=%s but no TLS", network)
                return False

        # Filter IPv6-only nodes (no IPv4)
        server = proxy.get("server", "")
        if ":" in server and not any(c.isdigit() and c != ":" for c in server.split(":")[0][:4]):
            # Looks like IPv6
            if not re.match(r"\d+\.\d+\.\d+\.\d+", server):
                logger.debug("Skipping IPv6-only node: %s", server)
                return False

        return True