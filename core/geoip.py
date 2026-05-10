"""GeoIP database downloader and manager."""

import os
import logging
from pathlib import Path

from .platform_utils import download_file, ensure_dir, make_session

logger = logging.getLogger(__name__)


def ensure_geoip(settings: dict) -> str:
    """Ensure GeoIP (Country.mmdb) database is available.

    Downloads a fresh copy every run. Falls back to cached version on failure.

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
    mmdb_path = settings.get("paths", {}).get("country_mmdb", "")

    if not mmdb_path:
        mmdb_path = os.path.join(cache_dir, "Country.mmdb")

    mmdb_path = str(Path(mmdb_path).resolve())
    ensure_dir(os.path.dirname(mmdb_path))

    # Always try to download fresh copy
    logger.info("Downloading fresh Country.mmdb...")
    session = make_session(settings)
    success = download_file(mmdb_url, mmdb_path, desc="Country.mmdb",
                           session=session)

    if not success and os.path.exists(mmdb_path):
        logger.warning("Download failed, using cached Country.mmdb: %s", mmdb_path)
    elif not success:
        logger.error("No Country.mmdb available (download failed, no cache)")

    return mmdb_path
