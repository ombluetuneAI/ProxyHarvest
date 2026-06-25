#!/usr/bin/env python3
"""Fetch proxies from anaer/Sub, merge with existing clash_all.yaml, output clash_merge.yaml.

Usage:
    python scripts/generate_clash.py

Output:
    output/clash_merge.yaml  - Merged Clash config (remote + existing, deduped)
"""
import os
import sys
from pathlib import Path

import yaml
import requests

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.converter import FormatConverter
from core.merger import merge_proxies_priority

# Paths
TEMPLATE_PATH = PROJECT_ROOT / "config" / "clash_template.yaml"
OUTPUT_DIR = PROJECT_ROOT / "output"
EXISTING_PATH = OUTPUT_DIR / "clash_all.yaml"
OUTPUT_PATH = OUTPUT_DIR / "clash_merge.yaml"

# Remote source
REMOTE_URL = "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml"


def _load_existing_proxies() -> list:
    """Load proxies from existing clash_all.yaml if present."""
    if not EXISTING_PATH.exists():
        return []

    with open(EXISTING_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    proxies = data.get("proxies") or []
    if not isinstance(proxies, list):
        print(f"[WARN] Existing file has invalid 'proxies': {EXISTING_PATH}", file=sys.stderr)
        return []

    print(f"  -> {len(proxies)} proxies loaded from {EXISTING_PATH}")
    return [p for p in proxies if isinstance(p, dict)]


def main() -> int:
    # 1. Fetch remote proxies
    print(f"[1/5] Fetching proxies from {REMOTE_URL} ...")
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

    remote_proxies = remote_data["proxies"]
    if not isinstance(remote_proxies, list):
        print("[FAIL] 'proxies' is not a list", file=sys.stderr)
        return 1

    remote_proxies = [p for p in remote_proxies if isinstance(p, dict)]
    print(f"  -> {len(remote_proxies)} proxies fetched")

    # 2. Load existing proxies from clash_all.yaml
    print(f"[2/5] Loading existing proxies from {EXISTING_PATH} ...")
    existing_proxies = _load_existing_proxies()
    if not EXISTING_PATH.exists():
        print("  -> no existing file, using remote only")

    # 3. Merge and deduplicate (remote wins on same node, keeps remote name)
    print("[3/5] Merging and deduplicating ...")
    proxies = merge_proxies_priority(remote_proxies, existing_proxies)
    print(f"  -> {len(proxies)} proxies after merge")

    # 4. Load template
    print(f"[4/5] Loading template from {TEMPLATE_PATH} ...")
    if not TEMPLATE_PATH.exists():
        print(f"[FAIL] Template not found: {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    if not isinstance(template, dict):
        print("[FAIL] Invalid template format", file=sys.stderr)
        return 1

    # 5. Build complete config
    print("[5/5] Building Clash config ...")
    config = FormatConverter.build_clash_config(template, proxies)

    # 6. Write output
    print(f"Writing output to {OUTPUT_PATH} ...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    file_size = os.path.getsize(OUTPUT_PATH)
    print(f"\n[OK] Generated {OUTPUT_PATH}")
    print(f"     Proxies: {len(proxies)} (remote {len(remote_proxies)}, existing {len(existing_proxies)})")
    print(f"     Size:    {file_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
