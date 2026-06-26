"""GeoIP-based proxy naming."""

import os
import logging
import socket
from typing import Dict, Any, Optional

from .constants import COUNTRY_EMOJI

logger = logging.getLogger(__name__)

# Regional-indicator pair at name start (e.g. 🇺🇸)
_FLAG_PREFIX_CHARS = tuple(chr(cp) for cp in range(0x1F1E6, 0x1F1FF + 1))


def strip_leading_flag(name: str) -> str:
    """Remove a leading flag emoji (regional indicators) if present."""
    if len(name) >= 2 and name[0] in _FLAG_PREFIX_CHARS and name[1] in _FLAG_PREFIX_CHARS:
        return name[2:].lstrip()
    return name


# Special server name replacements
SERVER_REPLACE = {
    "CLOUDFLARE": "RELAY",
    "CF": "RELAY",
    "PRIVATE": "RELAY",
    "RACKSPACE": "RELAY",
}


class GeoNamer:
    """GeoIP-based proxy naming using Country.mmdb."""

    def __init__(self, settings: dict, mmdb_path: str = ""):
        self.settings = settings
        self.mmdb_path = mmdb_path or settings.get("paths", {}).get("country_mmdb", ".cache/Country.mmdb")
        self.namer_config = settings.get("namer", {})
        self.exclude_countries = settings.get("collector", {}).get("validate", {}).get(
            "exclude_countries", ["IL"]
        )
        self.geo_reader = None
        self._load_geoip()

    def _load_geoip(self) -> None:
        """Load GeoIP database."""
        try:
            import geoip2.database
            if self.mmdb_path and os.path.exists(self.mmdb_path):
                self.geo_reader = geoip2.database.Reader(self.mmdb_path)
                logger.info("GeoIP database loaded from %s", self.mmdb_path)
            else:
                logger.warning("GeoIP database not found at %s", self.mmdb_path)
        except ImportError:
            logger.warning("geoip2 module not installed, GeoIP naming disabled")
        except Exception as e:
            logger.error("Failed to load GeoIP database: %s", e)

    def close(self) -> None:
        """Close GeoIP database reader."""
        if self.geo_reader:
            self.geo_reader.close()
            self.geo_reader = None

    def get_country_code(self, ip: str) -> Optional[str]:
        """Get country code for an IP address.

        Args:
            ip: IP address string.

        Returns:
            2-letter country code or None.
        """
        if not self.geo_reader:
            return None

        try:
            response = self.geo_reader.country(ip)
            return response.country.iso_code
        except Exception:
            return None

    def _resolve_hostname(self, server: str) -> Optional[str]:
        """Resolve hostname to IP address.

        Args:
            server: Server hostname or IP.

        Returns:
            IP address string or None.
        """
        # Already an IP
        try:
            socket.inet_aton(server)
            return server
        except socket.error:
            pass

        # Try resolving hostname
        try:
            ip = socket.gethostbyname(server)
            return ip
        except socket.gaierror:
            logger.debug("Could not resolve hostname: %s", server)
            return None

    def _apply_flag(self, name: str, emoji: str) -> str:
        """Format as {emoji}{name}, replacing any existing leading flag."""
        base = strip_leading_flag(name)
        if not emoji or not self.namer_config.get("emoji_enabled", True):
            return base
        return f"{emoji}{base}"

    def rename_proxies(self, proxies: list) -> list:
        """Prefix proxy names with a GeoIP country flag.

        Format: {emoji}{name} — keeps the original name (minus any leading flag).

        Args:
            proxies: List of proxy dictionaries.

        Returns:
            Renamed proxy list (some may be excluded).
        """
        if not self.geo_reader:
            logger.warning("GeoIP not available, skipping rename")
            return proxies

        result = []
        excluded = 0

        for proxy in proxies:
            server = proxy.get("server", "")
            if not server:
                continue

            # Resolve IP
            ip = self._resolve_hostname(server)
            if not ip:
                logger.debug("Could not resolve %s, skipping", server)
                excluded += 1
                continue

            # Get country
            country_code = self.get_country_code(ip)
            if not country_code:
                logger.debug("No country for %s (%s), skipping", server, ip)
                excluded += 1
                continue

            # Skip excluded countries
            if country_code.upper() in self.exclude_countries:
                logger.debug("Skipping excluded country: %s", country_code)
                excluded += 1
                continue

            emoji = COUNTRY_EMOJI.get(country_code.upper(), "")
            proxy["name"] = self._apply_flag(proxy.get("name", ""), emoji)

            result.append(proxy)

        if excluded > 0:
            logger.info("Excluded %d proxies (GeoIP or country filter)", excluded)
        logger.info("Renamed %d proxies", len(result))

        return result