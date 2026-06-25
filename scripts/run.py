#!/usr/bin/env python3
"""ProxyHarvest - Main CLI entry point.

Usage:
    python scripts/run.py all              # Full pipeline
    python scripts/run.py collect          # Collect nodes only
    python scripts/run.py speedtest        # Speed test only
    python scripts/run.py format           # Format output only
    python scripts/run.py update-sources   # Update subscription source URLs
    python scripts/run.py update-geoip     # Download fresh GeoIP database
    python scripts/run.py clash-validate   # Validate nodes via standalone Mihomo
    python scripts/run.py clash-validate --input output/clash_merge.yaml --output output/clash.yaml
"""

import sys
import os
import csv
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
from core.namer import GeoNamer
from core.speedtester import SpeedTester
from core.filter import NodeFilter
from core.formatter import NodeFormatter
from core.geoip import ensure_geoip
from core.clash_validator import (
    ClashValidator, load_proxies_for_validation, authoritative_alive_via_mihomo,
    write_validated_clash,
)

# ── Helper: per-source collection summary CSV ──────────────────────
def _save_collect_summary(collector: Collector, sources: list, tmp_dir: str) -> None:
    """Save CSV 1: source_id, remarks, success, total_nodes, deduped_nodes."""
    path = os.path.join(tmp_dir, "collect_summary.csv")
    # Build source_id -> remarks mapping
    id_to_remarks = {s.get("id", -1): s.get("remarks", "") for s in sources}
    rows = []
    for sid, stats in (collector.source_stats or {}).items():
        rows.append({
            "source_id": sid,
            "remarks": id_to_remarks.get(sid, ""),
            "success": "yes" if stats.get("success") else "no",
            "total_nodes": stats.get("total", 0),
            "deduped_nodes": stats.get("deduped", 0),
        })
    rows.sort(key=lambda r: r["source_id"])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "remarks", "success", "total_nodes", "deduped_nodes"])
        writer.writeheader()
        writer.writerows(rows)
    logging.getLogger("run").info("Collection summary: %s (%d sources)", path, len(rows))

    # ── Print formatted table ──
    print("\n" + "=" * 70)
    print(" 📦  COLLECTION SUMMARY")
    print("=" * 70)
    print(f"| {'source_id':>10} | {'remarks':<20} | {'success':>7} | {'total':>5} | {'deduped':>7} |")
    print("-" * 70)
    for r in rows:
        print(f"| {str(r['source_id']):>10} | {r['remarks']:<20} | {r['success']:>7} | {r['total_nodes']:>5} | {r['deduped_nodes']:>7} |")
    print("=" * 70)


# ── Helper: per-source speedtest summary CSV ───────────────────────
def _save_speedtest_summary(pre_speed_data: dict, filtered_result: dict, sources: list,
                            tmp_dir: str) -> None:
    """Save CSV 2: source_id, remarks, deduped_nodes, valid_nodes."""
    path = os.path.join(tmp_dir, "speedtest_summary.csv")
    id_to_remarks = {s.get("id", -1): s.get("remarks", "") for s in sources}

    # Count valid (passed) proxies per source from the filtered all_proxies
    valid_counts = {}
    for p in filtered_result.get("all_proxies", []):
        sid = p.get("_source_id", -1)
        valid_counts[sid] = valid_counts.get(sid, 0) + 1

    rows = []
    for sid, stats in (pre_speed_data or {}).items():
        sid_int = int(sid) if isinstance(sid, str) else sid
        rows.append({
            "source_id": sid,
            "remarks": id_to_remarks.get(sid_int, ""),
            "deduped_nodes": stats.get("deduped", 0) if isinstance(stats, dict) else stats,
            "valid_nodes": valid_counts.get(sid_int, 0),
        })
    rows.sort(key=lambda r: r["source_id"])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "remarks", "deduped_nodes", "valid_nodes"])
        writer.writeheader()
        writer.writerows(rows)
    logging.getLogger("run").info("Speedtest summary: %s (%d sources)", path, len(rows))

    # ── Print formatted table ──
    print("\n" + "=" * 60)
    print(" ⚡  SPEEDTEST SUMMARY")
    print("=" * 60)
    print(f"| {'source_id':>10} | {'remarks':<20} | {'deduped':>7} | {'valid':>5} |")
    print("-" * 60)
    for r in rows:
        print(f"| {str(r['source_id']):>10} | {r['remarks']:<20} | {r['deduped_nodes']:>7} | {r['valid_nodes']:>5} |")
    print("=" * 60)


# ── Helper: strip internal _source_id marker ───────────────────────
def _strip_source_ids(proxies: list) -> list:
    """Remove internal _source_id field from all proxy dicts."""
    for p in proxies:
        p.pop("_source_id", None)
    return proxies


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

        # 5. GeoIP rename
        logger.info("=== Phase 5: GeoIP rename ===")
        namer = GeoNamer(settings, mmdb_path)
        try:
            proxies = namer.rename_proxies(proxies)
        finally:
            namer.close()

        # Save per-source stats for downstream speedtest CSV
        tmp_dir = os.path.join(get_path(settings, "output_dir"), "tmp")
        ensure_dir(tmp_dir)
        _save_collect_summary(collector, sources, tmp_dir)

        # Save pre-speedtest deduped counts as sidecar JSON
        source_deduped_path = os.path.join(tmp_dir, "source_deduped.json")
        with open(source_deduped_path, "w", encoding="utf-8") as f:
            json.dump(collector.source_stats, f, ensure_ascii=False)
        logger.info("Saved pre-speedtest source stats to %s", source_deduped_path)

        # 6. Save intermediate files
        logger.info("=== Phase 6: Save intermediate files ===")
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

        # Save sub_merge.txt (mixed format — plain URI list)
        from core.formatter import NodeFormatter
        from core.converter import FormatConverter
        mixed_path = os.path.join(sub_dir, "sub_merge.txt")
        yaml_content = yaml.dump({"proxies": proxies}, allow_unicode=True)
        mixed = NodeFormatter._local_yaml_to_mixed(yaml_content)
        with open(mixed_path, "w", encoding="utf-8") as f:
            f.write(mixed)

        # Save sub_merge_base64.txt (base64-encoded mixed format)
        b64_path = os.path.join(sub_dir, "sub_merge_base64.txt")
        with open(b64_path, "w", encoding="utf-8") as f:
            f.write(FormatConverter.base64_encode(mixed))

        logger.info("Collection complete: %d proxies", len(proxies))
        return proxies

    finally:
        subconverter.stop()


def _apply_mihomo_authority(filtered: dict, speedtest_results: dict,
                            proxies: list, mihomo_alive: dict,
                            top_n: int = 200) -> dict:
    """Rebuild the filtered result using Mihomo's alive set as the authority.

    Keeps singtools bandwidth/ping for ranking where available; nodes that only
    Mihomo could validate (singtools couldn't test them) carry zero bandwidth and
    sort last. Drops singtools false positives that Mihomo deems dead.

    Args:
        filtered: Original singtools filter result (for top_n via stats).
        speedtest_results: Raw singtools results (with ``meta``).
        proxies: Full original proxy list.
        mihomo_alive: Mapping of alive ``name -> delay_ms`` from Mihomo.

    Returns:
        New filtered-result dict aligned with the authoritative alive set.
    """
    speed_map = {}
    for entry in speedtest_results.get("meta", []):
        info = SpeedTester.extract_speed_info(entry)
        if info["tag"]:
            speed_map[info["tag"]] = info

    proxy_speeds = []
    for proxy in proxies:
        name = proxy.get("name", "")
        if name not in mihomo_alive:
            continue
        info = speed_map.get(name, {})
        proxy_speeds.append({
            "proxy": proxy,
            "avg_speed": info.get("avg_speed", 0) or 0,
            "max_speed": info.get("max_speed", 0) or 0,
            # Prefer singtools ping; fall back to Mihomo delay for recovered nodes
            "ping": (info.get("ping", 0) or 0) or mihomo_alive.get(name, 0),
        })

    has_speed_data = any(ps["avg_speed"] > 0 for ps in proxy_speeds)
    if has_speed_data:
        proxy_speeds.sort(key=lambda x: x["avg_speed"], reverse=True)
        mode = "speed"
    else:
        proxy_speeds.sort(key=lambda x: x["ping"] if x["ping"] > 0 else 999999)
        mode = "ping"

    top = proxy_speeds[:top_n] if top_n else proxy_speeds

    return {
        "top_proxies": [ps["proxy"] for ps in top],
        "all_proxies": [ps["proxy"] for ps in proxy_speeds],
        "top_proxy_speeds": top,
        "all_proxy_speeds": proxy_speeds,
        "stats": {
            "total_tested": len(proxies),
            "passed": len(proxy_speeds),
            "top_n": len(top),
            "mode": mode,
            "authority": "mihomo",
        },
    }


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

    tmp_dir = os.path.join(get_path(settings, "output_dir"), "tmp")

    # Reconcile with Mihomo. singtools (sing-box) and Mihomo support different
    # protocols, so singtools both drops untestable nodes and occasionally
    # mis-judges. Mihomo is authoritative on connectivity when available.
    if settings.get("singtools", {}).get("reconcile_with_mihomo", True):
        logger.info("=== Reconciling node validity via authoritative Mihomo ===")
        mihomo_alive = authoritative_alive_via_mihomo(
            settings, proxies, os.path.join(tmp_dir, "reconcile")
        )
        if mihomo_alive is not None:
            top_n = settings.get("output", {}).get("top_nodes", 200)
            filtered = _apply_mihomo_authority(
                filtered, results, proxies, mihomo_alive, top_n
            )
            logger.info("Reconciled to %d alive nodes (Mihomo authoritative)",
                        len(filtered.get("all_proxies", [])))

    # Save speedtest summary CSV if pre-speedtest data exists
    source_deduped_path = os.path.join(tmp_dir, "source_deduped.json")
    if os.path.exists(source_deduped_path):
        with open(source_deduped_path, "r", encoding="utf-8") as f:
            pre_speed_data = json.load(f)
        # Load sources for remarks
        sources = load_sub_sources(get_path(settings, "sub_sources"))
        _save_speedtest_summary(pre_speed_data, filtered, sources, tmp_dir)

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

        # Strip internal _source_id before final output
        _strip_source_ids(filtered_result.get("top_proxies", []))
        _strip_source_ids(filtered_result.get("all_proxies", []))

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
    logger.info("ProxyHarvest - Full Pipeline")
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


def run_clash_validate(settings: dict, input_path: str = None, output_path: str = None) -> None:
    """Validate proxy nodes via standalone Mihomo."""
    logger = logging.getLogger("run.clash_validate")
    logger.info("=== Node validation via standalone Mihomo ===")

    output_dir = os.path.join(get_path(settings, "output_dir"), "tmp")
    ensure_dir(output_dir)

    # Prefer merged collect output (keeps _source_id for per-source stats)
    sub_merge = os.path.join(get_path(settings, "sub_dir"), "sub_merge_yaml.yml")
    if input_path:
        proxies = load_proxies_for_validation(settings, input_path)
        clash_input = input_path
    elif os.path.exists(sub_merge):
        proxies = load_proxies_for_validation(settings, sub_merge)
        clash_input = sub_merge
        logger.info("Using merged collect file with source tags: %s", sub_merge)
    else:
        proxies = load_proxies_for_validation(settings, None)
        clash_input = settings.get("mihomo", {}).get("input", "output/clash.yaml")

    sources = None
    if any(p.get("_source_id") is not None for p in proxies):
        sources = load_sub_sources(get_path(settings, "sub_sources"))

    validator = ClashValidator(settings)
    report = validator.validate_proxies(proxies, output_dir=output_dir, sources=sources)

    if output_path:
        alive_names = {
            node["name"]
            for node in report.get("nodes", [])
            if node.get("alive") and node.get("name")
        }
        count = write_validated_clash(clash_input, output_path, alive_names)
        logger.info("Wrote %d alive proxies to %s", count, output_path)


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

    clash_input = None
    clash_output = None
    if command == "clash-validate":
        args = sys.argv[2:]
        if "--input" in args:
            idx = args.index("--input")
            if idx + 1 < len(args):
                clash_input = args[idx + 1]
        if "--output" in args:
            idx = args.index("--output")
            if idx + 1 < len(args):
                clash_output = args[idx + 1]

    commands = {
        "all": lambda: run_all(settings),
        "collect": lambda: run_collect_and_format(settings),
        "speedtest": lambda: run_speedtest(settings),
        "format": lambda: run_format(settings),
        "update-sources": lambda: run_update_sources(settings),
        "update-geoip": lambda: run_update_geoip(settings),
        "clash-validate": lambda: run_clash_validate(
            settings, input_path=clash_input, output_path=clash_output
        ),
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
