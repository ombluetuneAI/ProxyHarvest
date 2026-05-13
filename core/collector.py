"""Proxy node collector from subscription sources."""

import ipaddress
import logging
from typing import List, Dict, Any, Optional

import yaml

from .constants import COUNTRY_EMOJI
from .converter import SubConverter
from .merger import merge_and_validate

logger = logging.getLogger(__name__)


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
            List of collected proxy dictionaries (each tagged with _source_id).
        """
        all_proxies = []
        # Track per-source stats: {source_id: {"total": int, "success": bool}}
        self.source_stats = {}

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

            source_ok = bool(proxies)
            source_total = len(proxies)
            self.source_stats[source_id] = {"total": source_total, "success": source_ok}

            if proxies:
                # Tag each proxy with its source_id
                for p in proxies:
                    p["_source_id"] = source_id
                logger.info("Collected %d proxies from source #%d", source_total, source_id)
                all_proxies.extend(proxies)
            else:
                logger.warning("No proxies collected from source #%d", source_id)

        # Deduplicate and validate
        if all_proxies:
            all_proxies = merge_and_validate(all_proxies, self.validate_config)
            logger.info("Total collected: %d proxies (after dedup)", len(all_proxies))

            # Count deduped nodes per source
            dedup_counts = {}
            for p in all_proxies:
                sid = p.get("_source_id", -1)
                dedup_counts[sid] = dedup_counts.get(sid, 0) + 1
            for sid in self.source_stats:
                self.source_stats[sid]["deduped"] = dedup_counts.get(sid, 0)

        return all_proxies

    def _collect_from_source(self, url: str, source_id: int) -> List[Dict[str, Any]]:
        """Collect proxies from a single source URL (may contain multiple URLs separated by |).

        Subconverter natively supports pipe-separated URLs, so we pass them
        through directly instead of splitting and fetching individually.

        Args:
            url: Subscription URL(s), pipe-separated.
            source_id: Source ID for logging.

        Returns:
            List of proxy dictionaries.
        """
        url_count = len([u for u in url.split("|") if u.strip()])
        if url_count > 1:
            logger.info("  Passing %d pipe-separated URLs to subconverter", url_count)

        return self._fetch_and_parse(url, source_id)

    def _fetch_and_parse(self, sub_url: str, source_id: int,
                         retries: int = 0) -> List[Dict[str, Any]]:
        """Fetch subscription and parse as Clash YAML.

        Strategy:
        1. Try subconverter with original URL directly
        2. If that fails, download raw content and pass to subconverter
           via local file (handles sources that need custom HTTP headers)

        Args:
            sub_url: Subscription URL.
            source_id: Source ID.
            retries: Current retry count.

        Returns:
            List of proxy dictionaries.
        """
        try:
            # Strategy 1: Direct URL conversion via subconverter
            yaml_content = self.subconverter.convert(
                url=sub_url,
                target="clash",
                emoji=True,
                list_mode=True,
                timeout=self.timeout
            )

            if yaml_content:
                proxies = self._parse_clash_proxies(yaml_content)
                return proxies

            # Strategy 2: Download raw content, base64-encode, re-try via local file
            logger.info("Direct conversion failed for source #%d, trying raw download + base64 encode",
                        source_id)
            yaml_content = self._fetch_raw_and_convert(sub_url)
            if yaml_content:
                proxies = self._parse_clash_proxies(yaml_content)
                return proxies

            if retries < self.max_retries:
                logger.warning("Retry %d/%d for source #%d",
                               retries + 1, self.max_retries, source_id)
                return self._fetch_and_parse(sub_url, source_id, retries + 1)
            return []

        except Exception as e:
            logger.error("Failed to fetch source #%d: %s", source_id, e)
            if retries < self.max_retries:
                return self._fetch_and_parse(sub_url, source_id, retries + 1)
            return []

    def _fetch_raw_and_convert(self, sub_url: str) -> Optional[str]:
        """Download raw subscription content and convert via subconverter
        using a local file.

        This handles sources that require custom download headers
        (User-Agent, cookies, auth) that subconverter's own HTTP client
        doesn't provide.

        Args:
            sub_url: Subscription URL to download.

        Returns:
            Converted Clash YAML content, or None.
        """
        import os
        from .platform_utils import make_session

        try:
            session = make_session(self.settings)
            resp = session.get(sub_url, timeout=10)
            resp.raise_for_status()
            raw_content = resp.text.strip()
        except Exception as e:
            logger.warning("Failed to download raw content from %s: %s", sub_url, e)
            return None

        # subconverter auto-detects format (plain URI, base64, YAML, etc.)
        logger.info("Raw content fetched (%d bytes), saving to temp file for subconverter",
                    len(raw_content))

        # Write to temp file and convert via subconverter
        try:
            tmp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_file = os.path.join(tmp_dir, "_raw_sub.txt")
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(raw_content)

            result = self.subconverter.convert(
                url=tmp_file,
                target="clash",
                emoji=True,
                list_mode=True,
                timeout=self.timeout
            )
            return result
        except Exception as e:
            logger.warning("Raw download + convert failed: %s", e)
            return None

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

        # Filter IPv6-only nodes (keep hostnames and IPv4)
        server = proxy.get("server", "")
        if server:
            try:
                addr = ipaddress.ip_address(server)
                if isinstance(addr, ipaddress.IPv6Address):
                    logger.debug("Skipping IPv6-only node: %s", server)
                    return False
            except ValueError:
                # Not a bare IP address — could be a hostname, allow through
                pass

        return True