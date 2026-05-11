"""Subscription source URL updater."""

import re
import logging
from datetime import datetime
from typing import List, Optional

import requests

from .platform_utils import make_session

logger = logging.getLogger(__name__)


class SourceUpdater:
    """Update subscription source URLs automatically."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.session = make_session(settings)

    def update_all(self, sources: list) -> list:
        """Update all source URLs based on their update_method.

        Args:
            sources: List of source dictionaries.

        Returns:
            Updated sources list.
        """
        for source in sources:
            if not source.get("enabled", True):
                continue

            method = source.get("update_method", "auto")
            source_id = source.get("id", -1)

            try:
                if method == "change_date":
                    new_url = self.change_date(source)
                    if new_url:
                        source["url"] = new_url
                        logger.info("Source #%d updated (change_date): %s", source_id, new_url)
                elif method == "page_release":
                    new_url = self.find_release(source_id, source)
                    if new_url:
                        source["url"] = new_url
                        logger.info("Source #%d updated (page_release): %s", source_id, new_url)
                elif method == "auto":
                    logger.debug("Source #%d: auto, no update needed", source_id)
                else:
                    logger.warning("Source #%d: unknown update_method '%s'", source_id, method)
            except Exception as e:
                logger.error("Failed to update source #%d (%s): %s", source_id, method, e)

        return sources

    # Supported date placeholder tokens for url_template
    _DATE_TOKENS = {
        "YYYY", "YY", "MM", "DD", "MMDD", "YYYYMMDD", "YYMMDD",
        "HH", "mm", "SS",
    }

    @staticmethod
    def _build_date_map() -> dict:
        """Build a dict of date placeholder → formatted value."""
        now = datetime.now()
        return {
            "YYYY": now.strftime("%Y"),
            "YY": now.strftime("%y"),
            "MM": now.strftime("%m"),
            "DD": now.strftime("%d"),
            "MMDD": now.strftime("%m%d"),
            "YYYYMMDD": now.strftime("%Y%m%d"),
            "YYMMDD": now.strftime("%y%m%d"),
            "HH": now.strftime("%H"),
            "mm": now.strftime("%M"),
            "SS": now.strftime("%S"),
        }

    def change_date(self, source: dict) -> Optional[str]:
        """Generate date-based URL from source's url_template field.

        The url_template uses curly-brace placeholders like {YYYY}, {MMDD},
        {YYYYMMDD} etc. which are replaced with current date values.

        Example url_template:
            https://example.com/{YYYY}/{MM}/{YYYYMMDD}.txt
            → https://example.com/2026/05/20260511.txt

        Args:
            source: Source dictionary with 'url_template' field.

        Returns:
            Generated URL or None.
        """
        template = source.get("url_template")
        if not template:
            logger.warning(
                "Source #%d has change_date method but no url_template",
                source.get("id", -1),
            )
            return None

        date_map = self._build_date_map()

        try:
            url = template.format_map(date_map)
        except KeyError as e:
            logger.error(
                "Source #%d url_template has unknown placeholder: %s",
                source.get("id", -1), e,
            )
            return None

        # Verify URL is accessible
        try:
            resp = self.session.head(url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                logger.warning(
                    "change_date source #%d returned status %d",
                    source.get("id", -1), resp.status_code,
                )
        except Exception as e:
            logger.warning(
                "change_date source #%d check failed: %s",
                source.get("id", -1), e,
            )

        return url  # Return anyway, may work later

    def find_release(self, source_id: int, source: dict) -> Optional[str]:
        """Find latest GitHub Release URL.

        Args:
            source_id: Source ID.
            source: Source dictionary with 'site' field.

        Returns:
            Download URL or None.
        """
        # Extract owner/repo from site URL
        site = source.get("site", "")
        match = re.match(r"https?://github\.com/([^/]+/[^/]+)", site)
        if not match:
            logger.warning("Cannot parse GitHub repo from site: %s", site)
            return None

        repo = match.group(1)
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"

        try:
            resp = self.session.get(api_url, timeout=15)
            resp.raise_for_status()
            release = resp.json()

            # Find the first asset that looks like a data file
            for asset in release.get("assets", []):
                name = asset.get("name", "")
                if name.startswith("data") or name.endswith(".txt"):
                    return asset["browser_download_url"]

            # Fallback: use the first asset
            if release.get("assets"):
                return release["assets"][0]["browser_download_url"]

            logger.warning("No assets found in release for %s", repo)
            return None
        except Exception as e:
            logger.error("Failed to find release for %s: %s", repo, e)
            return None


