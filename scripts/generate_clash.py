#!/usr/bin/env python3
"""Fetch proxies from anaer/Sub, combine with clash_template.yaml, output to output/clash.yaml.

Usage:
    python scripts/generate_clash.py

Output:
    output/clash.yaml  - Complete Clash config with template + remote proxies
"""
import copy
import sys
import os
from pathlib import Path

import yaml
import requests

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = PROJECT_ROOT / "config" / "clash_template.yaml"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_PATH = OUTPUT_DIR / "clash.yaml"

# Remote source
REMOTE_URL = "https://raw.githubusercontent.com/anaer/Sub/refs/heads/main/proxies.yaml"


def main() -> int:
    # 1. Fetch remote proxies
    print(f"[1/4] Fetching proxies from {REMOTE_URL} ...")
    try:
        resp = requests.get(REMOTE_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[FAIL] Failed to fetch remote file: {e}", file=sys.stderr)
        return 1

    remote_data = yaml.safe_load(resp.text)
    if not isinstance(remote_data, dict) or "proxies" not in remote_data:
        print("[FAIL] Remote file does not contain 'proxies' key", file=sys.stderr)
        return 1

    proxies = remote_data["proxies"]
    if not isinstance(proxies, list):
        print("[FAIL] 'proxies' is not a list", file=sys.stderr)
        return 1

    print(f"  -> {len(proxies)} proxies fetched")

    # 2. Load template
    print(f"[2/4] Loading template from {TEMPLATE_PATH} ...")
    if not TEMPLATE_PATH.exists():
        print(f"[FAIL] Template not found: {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    if not isinstance(template, dict):
        print("[FAIL] Invalid template format", file=sys.stderr)
        return 1

    # 3. Build complete config (same logic as FormatConverter.build_clash_config)
    print("[3/4] Building Clash config ...")
    config = copy.deepcopy(template)
    config["proxies"] = proxies

    proxy_names = [p["name"] for p in proxies if isinstance(p, dict) and "name" in p]

    if "proxy-groups" in config:
        for group in config["proxy-groups"]:
            proxies_field = group.get("proxies", [])

            # Replace AUTO_FILL marker with all proxy names
            if proxies_field == "AUTO_FILL":
                group["proxies"] = list(proxy_names)
            elif isinstance(proxies_field, list) and "AUTO_FILL" in proxies_field:
                new_proxies = [p for p in proxies_field if p != "AUTO_FILL"]
                new_proxies.extend(proxy_names)
                group["proxies"] = new_proxies

    # 4. Write output
    print(f"[4/4] Writing output to {OUTPUT_PATH} ...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    file_size = os.path.getsize(OUTPUT_PATH)
    print(f"\n[OK] Generated {OUTPUT_PATH}")
    print(f"     Proxies: {len(proxies)}")
    print(f"     Size:    {file_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
