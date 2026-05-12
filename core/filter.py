"""Node filtering and sorting based on speed test results.

Supports two modes:
- speed mode: Uses avg_speed for sorting (descending), filters out zero-speed nodes.
- ping-only mode: When no avg_speed data is present, uses ping for sorting
  (ascending, lower is better), filters out zero-ping nodes.
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional

import yaml

from .speedtester import SpeedTester

logger = logging.getLogger(__name__)


class NodeFilter:
    """Filter and sort nodes based on speed test results."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.top_n = settings.get("output", {}).get("top_nodes", 200)
        self.filenames = settings.get("output", {}).get("filenames", {})

    def filter_results(self, speedtest_results: Dict[str, Any],
                       original_proxies: List[Dict[str, Any]],
                       output_dir: str) -> Dict[str, Any]:
        """Process speed test results and filter nodes.

        Matches singtools meta.json entries to original Clash proxies by tag/name.

        Args:
            speedtest_results: Output from SpeedTester.run() with 'meta' key.
            original_proxies: Original proxy list (Clash YAML format).
            output_dir: Directory for output files.

        Returns:
            Dictionary with filtered proxies and statistics.
        """
        meta_entries = speedtest_results.get("meta", [])

        # Build a mapping of tag -> speed info from meta.json
        speed_map = {}
        for entry in meta_entries:
            info = SpeedTester.extract_speed_info(entry)
            tag = info["tag"]
            if tag:
                speed_map[tag] = info

        # Match speed info with original proxies by name
        proxy_speeds = []
        for proxy in original_proxies:
            name = proxy.get("name", "")
            speed_info = speed_map.get(name, {"avg_speed": 0, "max_speed": 0, "ping": 0})

            proxy_speeds.append({
                "proxy": proxy,
                "avg_speed": speed_info.get("avg_speed", 0),
                "max_speed": speed_info.get("max_speed", 0),
                "ping": speed_info.get("ping", 0),
            })

        # Determine mode: ping-only vs speed
        has_speed_data = any(ps["avg_speed"] > 0 for ps in proxy_speeds)

        if has_speed_data:
            # Speed mode: sort by avg_speed descending, filter zero-speed
            proxy_speeds.sort(key=lambda x: x["avg_speed"], reverse=True)
            passed = [ps for ps in proxy_speeds if ps["avg_speed"] > 0]
            logger.info("Speed test: %d total, %d with speed > 0",
                         len(proxy_speeds), len(passed))
            top_proxies = passed[:self.top_n]
            all_filtered = passed
        else:
            # Ping-only mode: sort by ping ascending, filter zero-ping
            proxy_speeds.sort(key=lambda x: x["ping"] if x["ping"] > 0 else 999999)
            passed = [ps for ps in proxy_speeds if ps["ping"] > 0]
            logger.info("Ping test: %d total, %d with ping > 0",
                         len(proxy_speeds), len(passed))
            top_proxies = passed[:self.top_n]
            all_filtered = passed

        # Generate log (top nodes)
        self._write_speed_log(top_proxies, output_dir)

        result = {
            "top_proxies": [ps["proxy"] for ps in top_proxies],
            "all_proxies": [ps["proxy"] for ps in all_filtered],
            "top_proxy_speeds": top_proxies,
            "all_proxy_speeds": all_filtered,
            "stats": {
                "total_tested": len(proxy_speeds),
                "passed": len(passed),
                "top_n": len(top_proxies),
                "mode": "speed" if has_speed_data else "ping",
            },
        }

        logger.info("Filtered: %d top nodes from %d tested (mode=%s)",
                     len(top_proxies), len(proxy_speeds),
                     "speed" if has_speed_data else "ping")

        return result

    def _write_speed_log(self, proxy_speeds: List[Dict[str, Any]],
                         output_dir: str) -> None:
        """Write speed test log file.

        Args:
            proxy_speeds: List of proxy+speed dictionaries.
            output_dir: Output directory.
        """
        log_filename = self.filenames.get("log", "speedtest_log.txt")
        log_path = os.path.join(output_dir, log_filename)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"{'ID':<6}{'Name':<50}{'Type':<12}{'Ping':<10}{'AvgSpeed':<15}{'MaxSpeed':<15}\n")
            f.write("-" * 108 + "\n")
            for idx, ps in enumerate(proxy_speeds, 1):
                proxy = ps["proxy"]
                name = proxy.get("name", "unknown")[:48]
                ptype = proxy.get("type", "unknown")[:10]
                ping = f"{ps['ping']:.0f}ms" if ps["ping"] else "N/A"
                # Convert bytes/sec to MB/s (1 MB = 1024*1024 bytes)
                if ps["avg_speed"]:
                    avg_speed = f"{ps['avg_speed'] / 1048576:.2f} MB/s"
                else:
                    avg_speed = "0"
                if ps["max_speed"]:
                    max_speed = f"{ps['max_speed'] / 1048576:.2f} MB/s"
                else:
                    max_speed = "0"
                f.write(f"{idx:<6}{name:<50}{ptype:<12}{ping:<10}{avg_speed:<15}{max_speed:<15}\n")

        logger.info("Speed log written to %s", log_path)
