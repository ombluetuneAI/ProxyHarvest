"""GeoIP database downloader and manager."""

import os
import time
import logging
from pathlib import Path

from .platform_utils import download_file, ensure_dir, make_session

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400


def ensure_geoip(settings: dict) -> str:
    """Ensure GeoIP (Country.mmdb) database is available.

    Uses local cache: skips download if the cached file exists and is newer
    than cache_ttl_days (default 7 days). Downloads only when missing or expired.

    Args:
        settings: Settings dictionary.

    Returns:
        Path to Country.mmdb file.
    """
    geoip_config = settings.get("geoip", {})
    mmdb_url = geoip_config.get(
        "mmdb_url",
        "https://raw.githubusercontent.com/Loyalsoldier/geoip/release/Country.mmdb"
    )
    cache_dir = geoip_config.get("cache_dir", ".cache")
    cache_ttl_days = geoip_config.get("cache_ttl_days", 7)
    mmdb_path = settings.get("paths", {}).get("country_mmdb", "")

    if not mmdb_path:
        mmdb_path = os.path.join(cache_dir, "Country.mmdb")

    mmdb_path = str(Path(mmdb_path).resolve())
    ensure_dir(os.path.dirname(mmdb_path))

    # Check if cached file is still fresh
    if os.path.exists(mmdb_path):
        file_age_days = (time.time() - os.path.getmtime(mmdb_path)) / SECONDS_PER_DAY
        if file_age_days < cache_ttl_days:
            logger.info("Using cached Country.mmdb (%.1f days old, ttl=%d days)",
                        file_age_days, cache_ttl_days)
            return mmdb_path
        logger.info("Cached Country.mmdb expired (%.1f days > %d days), re-downloading",
                    file_age_days, cache_ttl_days)

    # Download if missing or expired
    logger.info("Downloading Country.mmdb...")
    session = make_session(settings)
    success = download_file(mmdb_url, mmdb_path, desc="Country.mmdb",
                           session=session)

    if not success and os.path.exists(mmdb_path):
        logger.warning("Download failed, using cached Country.mmdb: %s", mmdb_path)
    elif not success:
        logger.error("No Country.mmdb available (download failed, no cache)")

    return mmdb_path
