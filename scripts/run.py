#!/usr/bin/env python3
"""ProxyHarvest - Main CLI entry point.

Usage:
    python scripts/run.py all              # Collect -> Mihomo filter -> nodes_clash.yaml
    python scripts/run.py all --speedtest  # 同上，额外 singtools 测速排序
    python scripts/run.py clash-validate   # Validate nodes via standalone Mihomo
    python scripts/run.py clash-validate --input output/clash_merge.yaml --output output/clash_all.yaml
"""

import sys
import os
import csv
import logging
from pathlib import Path
from datetime import datetime

import yaml

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import (
    load_settings, load_sub_sources, save_sub_sources, get_path, load_clash_template
)
from core.platform_utils import ensure_dir
from core.converter import SubConverter, FormatConverter
from core.source_updater import SourceUpdater
from core.collector import Collector
from core.namer import GeoNamer
from core.speedtester import SpeedTester
from core.geoip import ensure_geoip
from core.clash_validator import (
    ClashValidator, load_proxies_for_validation, write_validated_clash,
)


def _tmp_dir(settings: dict) -> str:
    path = os.path.join(get_path(settings, "output_dir"), "tmp")
    ensure_dir(path)
    return path


def _save_collect_summary(collector: Collector, sources: list, tmp_dir: str) -> None:
    """Save CSV: source_id, remarks, success, total_nodes, deduped_nodes."""
    path = os.path.join(tmp_dir, "collect_summary.csv")
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
        writer = csv.DictWriter(
            f,
            fieldnames=["source_id", "remarks", "success", "total_nodes", "deduped_nodes"],
        )
        writer.writeheader()
        writer.writerows(rows)
    logging.getLogger("run").info("Collection summary: %s (%d sources)", path, len(rows))

    print("\n" + "=" * 70)
    print(" COLLECTION SUMMARY")
    print("=" * 70)
    print(f"| {'source_id':>10} | {'remarks':<20} | {'success':>7} | {'total':>5} | {'deduped':>7} |")
    print("-" * 70)
    for r in rows:
        print(
            f"| {str(r['source_id']):>10} | {r['remarks']:<20} | {r['success']:>7} | "
            f"{r['total_nodes']:>5} | {r['deduped_nodes']:>7} |"
        )
    print("=" * 70)


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
    """Collect nodes: update sources -> GeoIP -> subconverter -> rename."""
    logger = logging.getLogger("run.collect")

    logger.info("=== Phase 1: Update subscription sources ===")
    sources = load_sub_sources(get_path(settings, "sub_sources"))
    updater = SourceUpdater(settings)
    sources = updater.update_all(sources)
    save_sub_sources(sources, get_path(settings, "sub_sources"))

    logger.info("=== Phase 2: Download GeoIP ===")
    mmdb_path = ensure_geoip(settings)

    logger.info("=== Phase 3: Start subconverter ===")
    subconverter = SubConverter(settings)
    if not subconverter.start():
        logger.error("Failed to start subconverter, cannot collect")
        return []

    try:
        logger.info("=== Phase 4: Collect nodes ===")
        collector = Collector(settings, subconverter)
        proxies = collector.collect_all(sources)

        if not proxies:
            logger.warning("No proxies collected!")
            return []

        logger.info("=== Phase 5: GeoIP rename ===")
        namer = GeoNamer(settings, mmdb_path)
        try:
            proxies = namer.rename_proxies(proxies)
        finally:
            namer.close()

        _save_collect_summary(collector, sources, _tmp_dir(settings))
        merge_path = write_nodes_clash_merge(settings, proxies)
        logger.info("Collection complete: %d proxies -> %s", len(proxies), merge_path)
        return proxies

    finally:
        subconverter.stop()


def run_mihomo_filter(settings: dict, proxies: list) -> list:
    """Validate connectivity via Mihomo; return alive proxies only."""
    logger = logging.getLogger("run.mihomo_filter")

    if not proxies:
        return []

    logger.info("=== Mihomo connectivity filter (%d nodes) ===", len(proxies))
    output_dir = os.path.join(_tmp_dir(settings), "mihomo_filter")

    sources = None
    if any(p.get("_source_id") is not None for p in proxies):
        sources = load_sub_sources(get_path(settings, "sub_sources"))

    validator = ClashValidator(settings)
    report = validator.validate_proxies(proxies, output_dir=output_dir, sources=sources)

    alive_names = {
        node["name"]
        for node in report.get("nodes", [])
        if node.get("alive") and node.get("name")
    }
    alive_proxies = [p for p in proxies if p.get("name") in alive_names]

    logger.info("Mihomo filter: %d/%d nodes alive", len(alive_proxies), len(proxies))
    return alive_proxies


def _write_speed_log(proxy_speeds: list, log_path: str) -> None:
    """Write speed test log to tmp (not committed)."""
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"{'ID':<6}{'Name':<50}{'Type':<12}{'Ping':<10}{'AvgSpeed':<15}{'MaxSpeed':<15}\n")
        f.write("-" * 108 + "\n")
        for idx, ps in enumerate(proxy_speeds, 1):
            proxy = ps["proxy"]
            name = proxy.get("name", "unknown")[:48]
            ptype = proxy.get("type", "unknown")[:10]
            ping = f"{ps['ping']:.0f}ms" if ps["ping"] else "N/A"
            avg_speed = f"{ps['avg_speed'] / 1048576:.2f} MB/s" if ps["avg_speed"] else "0"
            max_speed = f"{ps['max_speed'] / 1048576:.2f} MB/s" if ps["max_speed"] else "0"
            f.write(f"{idx:<6}{name:<50}{ptype:<12}{ping:<10}{avg_speed:<15}{max_speed:<15}\n")


def run_speed_rank(settings: dict, proxies: list) -> list:
    """Run singtools on alive proxies; merge speed info, sort only (no filtering)."""
    logger = logging.getLogger("run.speed_rank")

    if not proxies:
        return []

    tmp = _tmp_dir(settings)
    clash_input = os.path.join(tmp, "speedtest_input.yaml")
    with open(clash_input, "w", encoding="utf-8") as f:
        yaml.dump({"proxies": proxies}, f, allow_unicode=True, sort_keys=False)
    logger.info("Speed rank input: %d proxies -> %s", len(proxies), clash_input)

    logger.info("=== Running speed test ===")
    tester = SpeedTester(settings)
    results = tester.run(clash_input, tmp)

    speed_map = {}
    if results:
        for entry in results.get("meta", []):
            info = SpeedTester.extract_speed_info(entry)
            if info["tag"]:
                speed_map[info["tag"]] = info

    proxy_speeds = []
    for proxy in proxies:
        name = proxy.get("name", "")
        info = speed_map.get(name, {"avg_speed": 0, "max_speed": 0, "ping": 0})
        proxy_speeds.append({
            "proxy": proxy,
            "avg_speed": info.get("avg_speed", 0) or 0,
            "max_speed": info.get("max_speed", 0) or 0,
            "ping": info.get("ping", 0) or 0,
        })

    has_speed_data = any(ps["avg_speed"] > 0 for ps in proxy_speeds)
    if has_speed_data:
        proxy_speeds.sort(key=lambda x: x["avg_speed"], reverse=True)
        mode = "speed"
    else:
        proxy_speeds.sort(key=lambda x: x["ping"] if x["ping"] > 0 else 999999)
        mode = "ping"

    _write_speed_log(proxy_speeds, os.path.join(tmp, "speedtest_log.txt"))
    logger.info(
        "Speed rank complete: %d nodes sorted (mode=%s, no nodes removed)",
        len(proxy_speeds), mode,
    )
    return [ps["proxy"] for ps in proxy_speeds]


def write_nodes_clash_merge(settings: dict, proxies: list) -> str:
    """Write merged Clash YAML after multi-source collect (keeps _source_id)."""
    logger = logging.getLogger("run.write_clash_merge")
    output_dir = get_path(settings, "output_dir")
    ensure_dir(output_dir)

    filename = settings.get("output", {}).get("clash_merge_yaml", "nodes_clash_merge.yaml")
    path = os.path.join(output_dir, filename)

    merge_proxies = [dict(p) for p in proxies]
    template = load_clash_template(
        settings.get("paths", {}).get("clash_template")
    )
    config = FormatConverter.build_clash_config(template, merge_proxies)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    logger.info("Written merge Clash YAML: %s (%d proxies)", path, len(merge_proxies))
    return path


def write_nodes_clash(settings: dict, proxies: list) -> str:
    """Write final Clash YAML with template and proxy groups."""
    logger = logging.getLogger("run.write_clash")
    output_dir = get_path(settings, "output_dir")
    ensure_dir(output_dir)

    filename = settings.get("output", {}).get("clash_yaml", "nodes_clash.yaml")
    path = os.path.join(output_dir, filename)

    clean = [dict(p) for p in proxies]
    _strip_source_ids(clean)

    template = load_clash_template(
        settings.get("paths", {}).get("clash_template")
    )
    config = FormatConverter.build_clash_config(template, clean)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    logger.info("Written Clash YAML: %s (%d proxies)", path, len(clean))
    return path


def _speedtest_enabled(settings: dict, cli_flag: bool = False) -> bool:
    """Whether to run singtools speed rank (default off)."""
    if cli_flag:
        return True
    env = os.environ.get("ENABLE_SPEEDTEST", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    return bool(settings.get("singtools", {}).get("enabled", False))


def run_all(settings: dict, *, speedtest: bool = False) -> None:
    """Full pipeline: collect -> Mihomo filter -> [optional speed rank] -> nodes_clash.yaml."""
    logger = logging.getLogger("run.all")
    logger.info("=" * 60)
    logger.info("ProxyHarvest - Full Pipeline")
    logger.info("Time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    proxies = run_collect(settings)
    if not proxies:
        logger.error("Collection failed, aborting pipeline")
        return

    alive = run_mihomo_filter(settings, proxies)
    if not alive:
        logger.error("No alive nodes after Mihomo filter, aborting pipeline")
        return

    if speedtest:
        logger.info("singtools speed test enabled")
        final = run_speed_rank(settings, alive)
    else:
        logger.info("singtools speed test skipped (use --speedtest to enable)")
        final = alive

    output_path = write_nodes_clash(settings, final)

    logger.info("=" * 60)
    logger.info("Pipeline complete! Output: %s", output_path)
    logger.info("=" * 60)


def run_clash_validate(settings: dict, input_path: str = None, output_path: str = None) -> None:
    """Validate proxy nodes via standalone Mihomo."""
    logger = logging.getLogger("run.clash_validate")
    logger.info("=== Node validation via standalone Mihomo ===")

    output_dir = os.path.join(_tmp_dir(settings), "clash_validate")

    proxies = load_proxies_for_validation(settings, input_path)
    clash_input = input_path or settings.get("mihomo", {}).get("input", "output/nodes_clash.yaml")

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
        count = write_validated_clash(
            clash_input, output_path, alive_names, settings=settings
        )
        logger.info("Wrote %d alive proxies to %s", count, output_path)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()
    settings = load_settings()
    setup_logging(settings)

    extra_args = sys.argv[2:]
    speedtest = _speedtest_enabled(settings, "--speedtest" in extra_args)

    clash_input = None
    clash_output = None
    if command == "clash-validate":
        if "--input" in extra_args:
            idx = extra_args.index("--input")
            if idx + 1 < len(extra_args):
                clash_input = extra_args[idx + 1]
        if "--output" in extra_args:
            idx = extra_args.index("--output")
            if idx + 1 < len(extra_args):
                clash_output = extra_args[idx + 1]

    commands = {
        "all": lambda: run_all(settings, speedtest=speedtest),
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
