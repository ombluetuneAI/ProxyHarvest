"""Node filtering and sorting based on speed test results."""

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

        Args:
            speedtest_results: Output from SpeedTester.run().
            original_proxies: Original proxy list (Clash YAML format).
            output_dir: Directory for output files.

        Returns:
            Dictionary with filtered proxies and statistics.
        """
        outbounds = speedtest_results.get("outbounds", [])

        # Build a mapping of tag -> speed info
        speed_map = {}
        for outbound in outbounds:
            info = SpeedTester.extract_speed_info(outbound)
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

        # Sort by average speed (descending)
        proxy_speeds.sort(key=lambda x: x["avg_speed"], reverse=True)

        # Remove zero-speed nodes
        nonzero = [ps for ps in proxy_speeds if ps["avg_speed"] > 0]
        logger.info("Speed test: %d total, %d with speed > 0",
                     len(proxy_speeds), len(nonzero))

        # Take top N
        top_proxies = nonzero[:self.top_n]
        all_with_speed = nonzero  # All non-zero for "all" output

        # Generate speed log
        self._write_speed_log(top_proxies, output_dir)

        result = {
            "top_proxies": [ps["proxy"] for ps in top_proxies],
            "all_proxies": [ps["proxy"] for ps in all_with_speed],
            "top_proxy_speeds": top_proxies,
            "all_proxy_speeds": all_with_speed,
            "stats": {
                "total_tested": len(proxy_speeds),
                "nonzero_speed": len(nonzero),
                "top_n": len(top_proxies),
            },
        }

        logger.info("Filtered: %d top nodes from %d tested",
                     len(top_proxies), len(proxy_speeds))

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
                ping = f"{ps['ping']:.0f}" if ps["ping"] else "N/A"
                avg_speed = f"{ps['avg_speed']:.2f}" if ps["avg_speed"] else "0"
                max_speed = f"{ps['max_speed']:.2f}" if ps["max_speed"] else "0"
                f.write(f"{idx:<6}{name:<50}{ptype:<12}{ping:<10}{avg_speed:<15}{max_speed:<15}\n")

        logger.info("Speed log written to %s", log_path)
