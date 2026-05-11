"""Speed testing using singtools."""

import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

from .platform_utils import get_tool_path

logger = logging.getLogger(__name__)

# Default filter: test all common proxy types
DEFAULT_FILTER = "shadowsocks,vmess,trojan,hysteria2,vless,hysteria"


class SpeedTester:
    """Run speed tests using singtools (sing-box based speed tester).

    singtools supports multiple input formats including:
    - Clash YAML (proxies: [...])  ← preferred, auto-detected and converted internally
    - sing-box JSON config
    - sing-box subscription format

    The meta.json output contains per-node results with ping/speed info.
    """

    def __init__(self, settings: dict):
        self.settings = settings
        self.singtools_config = settings.get("singtools", {})

    def run(self, input_file: str, output_dir: str = "") -> Optional[Dict[str, Any]]:
        """Run speed test on nodes file.

        Args:
            input_file: Path to input file. Supports:
                - Clash YAML (proxies: [...])
                - sing-box JSON config
                - sing-box subscription format
            output_dir: Directory for output files. Defaults to same as input.

        Returns:
            Dictionary with 'meta' key containing per-node results, or None.
        """
        from .config_loader import PROJECT_ROOT

        binary = get_tool_path(self.settings, "singtools")
        binary_path = Path(binary)

        if not binary_path.exists():
            logger.error("singtools binary not found: %s", binary)
            return None

        if not output_dir:
            output_dir = str(Path(input_file).parent)

        # Resolve all paths to absolute
        input_file = os.path.abspath(input_file)
        output_dir = os.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        output_file = os.path.join(output_dir, "out.json")
        meta_file = os.path.join(output_dir, "meta.json")

        # Resolve config file path
        config_file = self.singtools_config.get("config", "")
        if config_file and not os.path.isabs(config_file):
            config_file = str(PROJECT_ROOT / config_file)
        if config_file and not os.path.exists(config_file):
            logger.warning("singtools config not found: %s, using defaults", config_file)
            config_file = ""

        # Build command
        cmd = [binary, "test", "-i", input_file, "-o", output_file, "-m", meta_file]

        if config_file:
            cmd.extend(["-c", config_file])

        # Filter: must include all proxy types we want to test
        filter_val = self.singtools_config.get("filter", DEFAULT_FILTER)
        if filter_val and filter_val != "all":
            cmd.extend(["-f", filter_val])

        # Optional flags
        if self.singtools_config.get("detect_country", False):
            cmd.append("--detect")
        if self.singtools_config.get("detect_remote_ip", False):
            cmd.append("--remote")

        # Logging level
        cmd.extend(["-e", "info"])

        logger.info("Running singtools: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                cwd=str(binary_path.parent),
                capture_output=True,
                text=True,
                timeout=self.singtools_config.get("timeout", 600),
            )

            # singtools returns non-zero when all nodes fail, which is normal
            # Only treat as fatal error if no output files were generated
            if result.returncode != 0:
                logger.warning("singtools exited with code %d", result.returncode)
                if result.stderr:
                    logger.debug("singtools stderr: %s", result.stderr[:500])

            # Check for output regardless of exit code
            if not os.path.exists(meta_file) and not os.path.exists(output_file):
                logger.error("singtools produced no output files")
                return None

            logger.info("singtools completed")

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

        singtools meta.json format (per node):
            {
                "tag": "node-name",
                "type": "shadowsocks",
                "ping": 150,           // ms, 0 = failed
                "time": "ISO-timestamp",
                "config": "{...}",      // original outbound JSON
                // On success:
                "avg_speed": 12345.6,   // bytes/sec
                "max_speed": 23456.7,   // bytes/sec
                // With --detect:
                "country": "US",
                // With --remote:
                "remote_ip": "1.2.3.4"
            }

        singtools out.json format:
            Full sing-box config with only working outbounds.

        Args:
            output_file: Path to out.json (successful nodes only).
            meta_file: Path to meta.json (all tested nodes with metrics).

        Returns:
            Dictionary with 'meta' (list of per-node results) and
            'working_outbounds' (list of successful outbound configs).
        """
        result = {"meta": [], "working_outbounds": []}

        # Parse meta.json - primary source of speed info
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                if isinstance(meta_data, list):
                    result["meta"] = meta_data
                elif isinstance(meta_data, dict):
                    # Might be wrapped in a key
                    result["meta"] = meta_data.get("nodes", meta_data.get("results", [meta_data]))
                logger.info("Parsed meta.json: %d node results", len(result["meta"]))
            except Exception as e:
                logger.error("Failed to parse meta.json: %s", e)
        else:
            logger.warning("meta.json not found: %s", meta_file)

        # Parse out.json - working nodes in sing-box format
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    out_data = json.load(f)
                if isinstance(out_data, dict) and "outbounds" in out_data:
                    outbounds = out_data["outbounds"]
                    if outbounds:
                        result["working_outbounds"] = outbounds
                elif isinstance(out_data, list):
                    result["working_outbounds"] = out_data
                logger.info("Parsed out.json: %d working outbounds",
                            len(result["working_outbounds"]))
            except Exception as e:
                logger.warning("Failed to parse out.json: %s", e)

        return result

    @staticmethod
    def extract_speed_info(meta_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Extract speed information from a singtools meta.json entry.

        Args:
            meta_entry: Single entry from meta.json.

        Returns:
            Dictionary with tag, type, avg_speed, max_speed, ping.
        """
        return {
            "tag": meta_entry.get("tag", ""),
            "type": meta_entry.get("type", ""),
            "avg_speed": meta_entry.get("avg_speed", 0) or 0,
            "max_speed": meta_entry.get("max_speed", 0) or 0,
            "ping": meta_entry.get("ping", 0) or 0,
            "country": meta_entry.get("country", ""),
            "remote_ip": meta_entry.get("remote_ip", ""),
        }
