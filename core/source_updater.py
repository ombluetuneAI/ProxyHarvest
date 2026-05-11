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
        self.aggregator_urls = settings.get("source_updater", {}).get(
            "airport_aggregator_urls", []
        )
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
                    new_url = self.change_date(source_id)
                    if new_url:
                        source["url"] = new_url
                        logger.info("Source #%d updated (change_date): %s", source_id, new_url)
                elif method == "page_release":
                    new_url = self.find_release(source_id, source)
                    if new_url:
                        source["url"] = new_url
                        logger.info("Source #%d updated (page_release): %s", source_id, new_url)
                elif method == "update_airports":
                    new_url = self.update_airports(source_id)
                    if new_url:
                        source["url"] = new_url
                        logger.info("Source #%d updated (update_airports): %d URLs",
                                    source_id, new_url.count("|") + 1)
                elif method == "auto":
                    logger.debug("Source #%d: auto, no update needed", source_id)
                else:
                    logger.warning("Source #%d: unknown update_method '%s'", source_id, method)
            except Exception as e:
                logger.error("Failed to update source #%d (%s): %s", source_id, method, e)

        return sources

    def change_date(self, source_id: int) -> Optional[str]:
        """Generate date-based URL for a source.

        Args:
            source_id: Source ID.

        Returns:
            Generated URL or None.
        """
        now = datetime.now()
        yyyy = now.strftime("%Y")
        mm = now.strftime("%m")
        dd = now.strftime("%d")
        mmdd = now.strftime("%m%d")
        yyyymmdd = now.strftime("%Y%m%d")

        url_map = {
            0: f"https://raw.githubusercontent.com/pojiezhiyuanjun/freev2/master/{mmdd}.txt",
            # free-nodes/v2rayfree: file format is v{YYYYMMDD}1 (try 1 and 2)
            1: f"https://raw.githubusercontent.com/free-nodes/v2rayfree/main/v{yyyymmdd}1",
            3: f"https://nodefree.org/dy/{yyyy}/{mm}/{yyyymmdd}.yaml",
            4: f"https://v2rayshare.com/v2rayshare/v2ray/{yyyy}/{mm}/{yyyymmdd}.txt",
            5: f"https://clashnode.com/data/{yyyy}/{mm}/{yyyymmdd}.txt",
        }

        url = url_map.get(source_id)
        if url:
            # Verify URL is accessible
            try:
                resp = self.session.head(url, timeout=10, allow_redirects=True)
                if resp.status_code == 200:
                    return url
                else:
                    logger.warning("change_date source #%d returned status %d", source_id, resp.status_code)
                    return url  # Return anyway, may work later
            except Exception as e:
                logger.warning("change_date source #%d check failed: %s", source_id, e)
                return url  # Return anyway

        logger.warning("No change_date mapping for source #%d", source_id)
        return None

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

    def update_airports(self, source_id: int) -> Optional[str]:
        """Fetch airport URLs from aggregator sources.

        Args:
            source_id: Source ID.

        Returns:
            Pipe-separated URL string, or None.
        """
        urls = []

        for agg_url in self.aggregator_urls:
            try:
                resp = self.session.get(agg_url, timeout=30)
                resp.raise_for_status()
                content = resp.text.strip()
                # Each line is a URL
                for line in content.splitlines():
                    line = line.strip()
                    if line and line.startswith("http"):
                        urls.append(line)
            except Exception as e:
                logger.warning("Failed to fetch aggregator %s: %s", agg_url, e)

        if urls:
            # Deduplicate while preserving order
            seen = set()
            unique_urls = []
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    unique_urls.append(u)
            return "|".join(unique_urls)

        logger.warning("No airport URLs collected for source #%d", source_id)
        return None
