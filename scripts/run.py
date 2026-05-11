#!/usr/bin/env python3
"""V2RayAggregator - Main CLI entry point.

Usage:
    python scripts/run.py all              # Full pipeline
    python scripts/run.py collect          # Collect nodes only
    python scripts/run.py speedtest        # Speed test only
    python scripts/run.py format           # Format output only
    python scripts/run.py update-sources   # Update subscription source URLs
    python scripts/run.py update-geoip     # Download fresh GeoIP database
"""

import sys
import os
import logging
import json
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import (
    load_settings, load_sub_sources, save_sub_sources, get_path
)
from core.platform_utils import ensure_dir, IS_WINDOWS
from core.converter import SubConverter
from core.source_updater import SourceUpdater
from core.collector import Collector
from core.merger import merge_and_validate
from core.namer import GeoNamer
from core.speedtester import SpeedTester
from core.filter import NodeFilter
from core.formatter import NodeFormatter
from core.geoip import ensure_geoip


def setup_logging(settings: dict) -> None:
    """Configure logging."""
    level = settings.get("app", {}).get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_collect(settings: dict) -> list:
    """Run collection phase: update sources -> collect -> merge -> rename.

    Args:
        settings: Settings dictionary.

    Returns:
        List of collected proxy dictionaries.
    """
    logger = logging.getLogger("run.collect")

    # 1. Update subscription source URLs
    logger.info("=== Phase 1: Update subscription sources ===")
    sources = load_sub_sources(get_path(settings, "sub_sources"))
    updater = SourceUpdater(settings)
    sources = updater.update_all(sources)
    save_sub_sources(sources, get_path(settings, "sub_sources"))

    # 2. Download fresh GeoIP
    logger.info("=== Phase 2: Download GeoIP ===")
    mmdb_path = ensure_geoip(settings)

    # 3. Start subconverter
    logger.info("=== Phase 3: Start subconverter ===")
    subconverter = SubConverter(settings)
    if not subconverter.start():
        logger.error("Failed to start subconverter, cannot collect")
        return []

    try:
        # 4. Collect nodes
        logger.info("=== Phase 4: Collect nodes ===")
        collector = Collector(settings, subconverter)
        proxies = collector.collect_all(sources)

        if not proxies:
            logger.warning("No proxies collected!")
            return []

        # 5. Merge and deduplicate
        logger.info("=== Phase 5: Merge and deduplicate ===")
        proxies = merge_and_validate(proxies, settings.get("collector", {}).get("validate", {}))

        # 6. GeoIP rename
        logger.info("=== Phase 6: GeoIP rename ===")
        namer = GeoNamer(settings, mmdb_path)
        try:
            proxies = namer.rename_proxies(proxies)
        finally:
            namer.close()

        # 7. Save intermediate files
        logger.info("=== Phase 7: Save intermediate files ===")
        output_dir = get_path(settings, "output_dir")
        sub_dir = get_path(settings, "sub_dir")
        ensure_dir(output_dir)
        ensure_dir(sub_dir)

        import yaml

        # Save sub_merge_yaml.yml
        yaml_path = os.path.join(sub_dir, "sub_merge_yaml.yml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump({"proxies": proxies}, f, allow_unicode=True, sort_keys=False)
        logger.info("Saved %d proxies to %s", len(proxies), yaml_path)

        # Save sub_merge.txt (mixed format)
        from core.converter import FormatConverter
        mixed_path = os.path.join(sub_dir, "sub_merge.txt")
        yaml_content = yaml.dump({"proxies": proxies}, allow_unicode=True)
        mixed = FormatConverter.base64_encode(yaml_content)
        with open(mixed_path, "w", encoding="utf-8") as f:
            f.write(mixed)

        # Save sub_merge_base64.txt
        b64_path = os.path.join(sub_dir, "sub_merge_base64.txt")
        with open(b64_path, "w", encoding="utf-8") as f:
            f.write(FormatConverter.base64_encode(yaml_content))

        logger.info("Collection complete: %d proxies", len(proxies))
        return proxies

    finally:
        subconverter.stop()


def run_speedtest(settings: dict, proxies: list = None) -> dict:
    """Run speed test phase.

    singtools directly supports Clash YAML as input — it auto-detects the format
    and converts internally. No subconverter sing-box conversion needed.

    Args:
        settings: Settings dictionary.
        proxies: List of proxy dictionaries. If None, loads from sub_merge_yaml.yml.

    Returns:
        Filtered result dictionary.
    """
    logger = logging.getLogger("run.speedtest")

    import yaml

    # Load proxies if not provided
    if proxies is None:
        yaml_path = os.path.join(get_path(settings, "sub_dir"), "sub_merge_yaml.yml")
        if not os.path.exists(yaml_path):
            logger.error("No proxies file found: %s", yaml_path)
            return {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            proxies = data.get("proxies", [])

    if not proxies:
        logger.error("No proxies to test")
        return {}

    output_dir = get_path(settings, "output_dir")

    # Save proxies as Clash YAML for singtools input
    # singtools auto-detects Clash YAML format and converts internally
    clash_input = os.path.join(output_dir, "speedtest_input.yaml")
    with open(clash_input, "w", encoding="utf-8") as f:
        yaml.dump({"proxies": proxies}, f, allow_unicode=True, sort_keys=False)
    logger.info("Saved Clash YAML input: %d proxies -> %s", len(proxies), clash_input)

    # Run singtools directly with Clash YAML input (no subconverter needed)
    logger.info("=== Running speed test ===")
    tester = SpeedTester(settings)
    results = tester.run(clash_input, output_dir)

    if not results:
        logger.error("Speed test produced no results")
        return {}

    # Filter and sort
    logger.info("=== Filtering results ===")
    node_filter = NodeFilter(settings)
    filtered = node_filter.filter_results(results, proxies, output_dir)

    return filtered


def run_format(settings: dict, filtered_result: dict = None) -> dict:
    """Run format and output phase.

    Args:
        settings: Settings dictionary.
        filtered_result: Filtered result from run_speedtest. If None, loads from files.

    Returns:
        Dictionary of output file paths.
    """
    logger = logging.getLogger("run.format")

    # Start subconverter for format conversion
    subconverter = SubConverter(settings)
    subconverter.start()

    try:
        formatter = NodeFormatter(settings, subconverter=subconverter)
        output_dir = get_path(settings, "output_dir")

        if filtered_result is None:
            # Load from files
            import yaml
            yaml_path = os.path.join(get_path(settings, "sub_dir"), "sub_merge_yaml.yml")
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                proxies = data.get("proxies", [])
            filtered_result = {"top_proxies": proxies, "all_proxies": proxies}

        outputs = formatter.format_and_output(
            filtered_result.get("top_proxies", []),
            filtered_result,
            output_dir
        )

        return outputs
    finally:
        subconverter.stop()


def run_collect_and_format(settings: dict) -> None:
    """Collect nodes and format output (without speed test)."""
    logger = logging.getLogger("run.collect_and_format")
    proxies = run_collect(settings)
    if not proxies:
        logger.error("Collection failed")
        return
    filtered_result = {"top_proxies": proxies, "all_proxies": proxies}
    outputs = run_format(settings, filtered_result)
    logger.info("Output files: %s", json.dumps(outputs, indent=2))


def run_all(settings: dict) -> None:
    """Run the complete pipeline: collect -> speedtest -> format.

    Args:
        settings: Settings dictionary.
    """
    logger = logging.getLogger("run.all")
    logger.info("=" * 60)
    logger.info("V2RayAggregator - Full Pipeline")
    logger.info("Time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # Phase 1: Collect
    proxies = run_collect(settings)
    if not proxies:
        logger.error("Collection failed, aborting pipeline")
        return

    # Phase 2: Speed test
    filtered_result = run_speedtest(settings, proxies)
    if not filtered_result:
        logger.warning("Speed test failed, using unfiltered proxies")
        filtered_result = {"top_proxies": proxies, "all_proxies": proxies}

    # Phase 3: Format and output
    outputs = run_format(settings, filtered_result)

    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info("Output files: %s", json.dumps(outputs, indent=2))
    logger.info("=" * 60)


def run_update_sources(settings: dict) -> None:
    """Update subscription source URLs only."""
    logger = logging.getLogger("run.update_sources")
    logger.info("Updating subscription sources...")

    sources = load_sub_sources(get_path(settings, "sub_sources"))
    updater = SourceUpdater(settings)
    sources = updater.update_all(sources)
    save_sub_sources(sources, get_path(settings, "sub_sources"))

    logger.info("Subscription sources updated")


def run_update_geoip(settings: dict) -> None:
    """Download fresh GeoIP database."""
    logger = logging.getLogger("run.update_geoip")
    logger.info("Downloading GeoIP database...")

    mmdb_path = ensure_geoip(settings)
    logger.info("GeoIP database saved to: %s", mmdb_path)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    # Load settings
    settings = load_settings()
    setup_logging(settings)

    commands = {
        "all": lambda: run_all(settings),
        "collect": lambda: run_collect_and_format(settings),
        "speedtest": lambda: run_speedtest(settings),
        "format": lambda: run_format(settings),
        "update-sources": lambda: run_update_sources(settings),
        "update-geoip": lambda: run_update_geoip(settings),
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print(f"Available commands: {', '.join(commands.keys())}")
        sys.exit(1)

    try:
        commands[command]()
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logging.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
