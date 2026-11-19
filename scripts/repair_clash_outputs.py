#!/usr/bin/env python3
"""Repair tracked Clash YAML outputs: sanitize bad proxy/group name scalars in place."""

import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.converter import dump_clash_yaml
from core.yaml_sanitize import (
    clash_config_has_unsafe_scalars,
    dumped_yaml_has_forbidden_escapes,
    sanitize_clash_config,
    sanitize_yaml_scalar,
)

DEFAULT_FILES = (
    PROJECT_ROOT / "output" / "clash.yaml",
    PROJECT_ROOT / "output" / "nodes_clash.yaml",
)

_QUOTED_NAME_RE = re.compile(
    r'^(\s*- name:\s*)"([^"\\]*(?:\\.[^"\\]*)*)"\s*$'
)
_QUOTED_LIST_RE = re.compile(
    r'^(\s*-\s*)"([^"\\]*(?:\\.[^"\\]*)*)"\s*$'
)


def _collect_name_fixes(config: dict) -> dict[str, str]:
    """Map raw unsafe name strings to their sanitized form (only when different)."""
    fixes: dict[str, str] = {}
    for proxy in config.get("proxies") or []:
        if not isinstance(proxy, dict):
            continue
        raw = proxy.get("name")
        if not isinstance(raw, str):
            continue
        clean = sanitize_yaml_scalar(raw)
        if clean != raw:
            fixes[raw] = clean
    for group in config.get("proxy-groups") or []:
        if not isinstance(group, dict):
            continue
        for field in ("name",):
            raw = group.get(field)
            if isinstance(raw, str):
                clean = sanitize_yaml_scalar(raw)
                if clean != raw:
                    fixes[raw] = clean
        for raw in group.get("proxies") or []:
            if isinstance(raw, str):
                clean = sanitize_yaml_scalar(raw)
                if clean != raw:
                    fixes[raw] = clean
    return fixes


def _patch_lines(text: str, fixes: dict[str, str]) -> str:
    if not fixes:
        return text

    out_lines: list[str] = []
    for line in text.splitlines():
        updated = line
        for raw, clean in fixes.items():
            if raw not in updated:
                continue
            escaped = (
                raw.replace("\\", "\\\\")
                .replace('"', '\\"')
                .encode("unicode_escape")
                .decode("ascii")
            )
            patterns = [f'"{raw}"', f'"{escaped}"']
            for needle in patterns:
                if needle in updated:
                    updated = updated.replace(needle, clean)
                    break
            else:
                if f"name: {raw}" in updated:
                    updated = updated.replace(f"name: {raw}", f"name: {clean}")
                elif f"- {raw}" in updated:
                    updated = updated.replace(f"- {raw}", f"- {clean}")
        out_lines.append(updated)
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")


def repair_file(path: Path) -> bool:
    if not path.exists():
        print(f"[skip] missing {path}")
        return False

    raw = path.read_text(encoding="utf-8")
    if not dumped_yaml_has_forbidden_escapes(raw):
        data = yaml.safe_load(raw)
        if isinstance(data, dict) and not clash_config_has_unsafe_scalars(data):
            print(f"[ok] already clean {path}")
            return True

    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        print(f"[fail] not a mapping: {path}", file=sys.stderr)
        return False

    fixes = _collect_name_fixes(data)
    if fixes:
        patched = _patch_lines(raw, fixes)
        if dumped_yaml_has_forbidden_escapes(patched):
            print(f"[warn] patch incomplete, falling back to full dump: {path}")
            patched = dump_clash_yaml(sanitize_clash_config(data))
    else:
        patched = dump_clash_yaml(sanitize_clash_config(data))

    if patched is None or dumped_yaml_has_forbidden_escapes(patched):
        print(f"[fail] still has forbidden escapes: {path}", file=sys.stderr)
        return False

    path.write_text(patched, encoding="utf-8")
    print(f"[ok] repaired {path}")
    return True


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else list(DEFAULT_FILES)
    ok = all(repair_file(p) for p in paths)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())