"""Speed testing using singtools."""

import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from .platform_utils import get_tool_path, IS_WINDOWS

logger = logging.getLogger(__name__)


class SpeedTester:
    """Run speed tests using singtools (sing-box based speed tester)."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.singtools_config = settings.get("singtools", {})

    def run(self, input_file: str, output_dir: str = "") -> Optional[Dict[str, Any]]:
        """Run speed test on nodes file.

        Args:
            input_file: Path to input file (Clash YAML or sing-box JSON).
            output_dir: Directory for output files. Defaults to same as input.

        Returns:
            Dictionary with results, or None on failure.
        """
        binary = get_tool_path(self.settings, "singtools")
        binary_path = Path(binary)

        if not binary_path.exists():
            logger.error("singtools binary not found: %s", binary)
            return None

        if not output_dir:
            output_dir = str(Path(input_file).parent)

        output_file = os.path.join(output_dir, "out.json")
        meta_file = os.path.join(output_dir, "meta.json")
        config_file = self.singtools_config.get("config", "")

        # Build command
        cmd = [
            binary, "test",
            "-i", input_file,
            "-o", output_file,
            "-c", config_file,
        ]

        # Add optional flags
        if self.singtools_config.get("detect_country", False):
            cmd.append("--detect")
        if self.singtools_config.get("detect_remote_ip", False):
            cmd.append("--remote")

        filter_val = self.singtools_config.get("filter", "")
        if filter_val:
            cmd.extend(["-f", filter_val])

        logger.info("Running singtools: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                cwd=str(binary_path.parent),
                capture_output=True,
                text=True,
                timeout=self.singtools_config.get("timeout", 600),
            )

            if result.returncode != 0:
                logger.error("singtools failed with code %d: %s",
                             result.returncode, result.stderr[:500])
                return None

            logger.info("singtools completed successfully")
            logger.debug("singtools stdout: %s", result.stdout[:500] if result.stdout else "")

        except subprocess.TimeoutExpired:
            logger.error("singtools timed out after %ds",
                         self.singtools_config.get("timeout", 600))
            return None
        except FileNotFoundError:
            logger.error("singtools binary not found or not executable: %s", binary)
            return None
        except Exception as e:
            logger.error("singtools execution failed: %s", e)
            return None

        # Parse results
        return self._parse_results(output_file, meta_file)

    def _parse_results(self, output_file: str, meta_file: str) -> Optional[Dict[str, Any]]:
        """Parse singtools output files.

        Args:
            output_file: Path to out.json (sing-box JSON format).
            meta_file: Path to meta.json (metadata).

        Returns:
            Dictionary with 'outbounds' and 'meta' keys, or None.
        """
        result = {"outbounds": [], "meta": {}}

        # Parse out.json (sing-box JSON format)
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "outbounds" in data:
                        result["outbounds"] = data["outbounds"]
                    elif isinstance(data, list):
                        result["outbounds"] = data
                    else:
                        result["outbounds"] = [data]
                logger.info("Parsed out.json: %d outbounds", len(result["outbounds"]))
            except Exception as e:
                logger.error("Failed to parse out.json: %s", e)
                return None
        else:
            logger.error("out.json not found: %s", output_file)
            return None

        # Parse meta.json (optional)
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    result["meta"] = json.load(f)
                logger.info("Parsed meta.json")
            except Exception as e:
                logger.warning("Failed to parse meta.json: %s", e)

        return result

    @staticmethod
    def extract_speed_info(outbound: Dict[str, Any]) -> Dict[str, Any]:
        """Extract speed information from a singtools outbound entry.

        Args:
            outbound: Single outbound entry from singtools output.

        Returns:
            Dictionary with speed info (avg_speed, max_speed, ping, etc.).
        """
        info = {
            "tag": outbound.get("tag", ""),
            "type": outbound.get("type", ""),
            "avg_speed": 0,
            "max_speed": 0,
            "ping": 0,
        }

        # Speed info may be in different locations depending on singtools version
        # Check for speed data in common locations
        if "speed" in outbound:
            speed = outbound["speed"]
            if isinstance(speed, dict):
                info["avg_speed"] = speed.get("avg", 0)
                info["max_speed"] = speed.get("max", 0)
            elif isinstance(speed, (int, float)):
                info["avg_speed"] = speed

        if "ping" in outbound:
            ping = outbound["ping"]
            if isinstance(ping, dict):
                info["ping"] = ping.get("avg", 0)
            elif isinstance(ping, (int, float)):
                info["ping"] = ping

        # Also check metadata fields
        if "avg_speed" in outbound:
            info["avg_speed"] = outbound["avg_speed"]
        if "max_speed" in outbound:
            info["max_speed"] = outbound["max_speed"]

        return info
