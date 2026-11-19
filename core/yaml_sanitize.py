"""Sanitize user-controlled strings before Clash YAML serialization."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Union

# C0 except TAB; all of C1 (U+007F–U+009F)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Literal Python-style escapes sometimes copied into names (not YAML escapes in file)
_LITERAL_ESCAPE_RE = re.compile(
    r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})"
)


def sanitize_yaml_scalar(value: Any) -> Any:
    """Strip control characters and U+FFFD from a string scalar; pass other types through."""
    if not isinstance(value, str):
        return value
    text = _CONTROL_RE.sub("", value)
    text = text.replace("\ufffd", "")
    text = _LITERAL_ESCAPE_RE.sub("", text)
    return text


def _sanitize_proxy_entry(proxy: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(proxy)
    if "name" in updated:
        updated["name"] = sanitize_yaml_scalar(updated["name"])
    return updated


def sanitize_proxies_for_yaml(proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_sanitize_proxy_entry(p) for p in proxies if isinstance(p, dict)]


def sanitize_clash_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-copy config and sanitize proxy/group name strings."""
    out = copy.deepcopy(config)

    proxies = out.get("proxies")
    if isinstance(proxies, list):
        out["proxies"] = sanitize_proxies_for_yaml(proxies)

    groups = out.get("proxy-groups")
    if isinstance(groups, list):
        new_groups: List[Dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                new_groups.append(group)
                continue
            g = dict(group)
            if "name" in g:
                g["name"] = sanitize_yaml_scalar(g["name"])
            plist = g.get("proxies")
            if isinstance(plist, list):
                g["proxies"] = [
                    sanitize_yaml_scalar(p) if isinstance(p, str) else p for p in plist
                ]
            new_groups.append(g)
        out["proxy-groups"] = new_groups

    return out


def clash_config_has_unsafe_scalars(config: Dict[str, Any]) -> bool:
    """Return True if any proxy/group name still contains controls or U+FFFD."""
    for proxy in config.get("proxies") or []:
        if isinstance(proxy, dict) and _scalar_unsafe(proxy.get("name")):
            return True
    for group in config.get("proxy-groups") or []:
        if not isinstance(group, dict):
            continue
        if _scalar_unsafe(group.get("name")):
            return True
        for p in group.get("proxies") or []:
            if isinstance(p, str) and _scalar_unsafe(p):
                return True
    return False


def _scalar_unsafe(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_CONTROL_RE.search(value) or "\ufffd" in value)


def dumped_yaml_has_forbidden_escapes(yaml_text: str) -> bool:
    """Detect Python-style \\x / \\U sequences in dumped YAML (outside comment lines)."""
    for line in yaml_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.search(r'(?<!\\)\\(?:x[0-9a-fA-F]{2}|U[0-9a-fA-F]{8})', line):
            return True
    return False