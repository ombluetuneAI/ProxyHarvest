"""Node merger and deduplication."""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def merge_and_validate(proxies: List[Dict[str, Any]],
                       validate_config: dict = None) -> List[Dict[str, Any]]:
    """Merge proxies from multiple sources and deduplicate.

    Deduplication rules:
    - Compare: server, port, type, network, tls, ws-opts, cipher, obfs
    - Ignore: uuid, password, id (allow different accounts on same server)

    Args:
        proxies: List of proxy dictionaries.
        validate_config: Optional validation config.

    Returns:
        Deduplicated list of proxy dictionaries.
    """
    seen = set()
    result = []

    for proxy in proxies:
        key = _dedup_key(proxy)
        if key not in seen:
            seen.add(key)
            result.append(proxy)
        else:
            logger.debug("Dedup: skipping %s", proxy.get("name", "unknown"))

    logger.info("Dedup: %d -> %d proxies", len(proxies), len(result))
    return result


def ensure_unique_names(proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure every proxy has a unique name; append #2, #3, ... on collision."""
    used_names: set[str] = set()
    result: List[Dict[str, Any]] = []
    for proxy in proxies:
        result.append(_with_unique_name(proxy, used_names))
    renamed = sum(1 for orig, new in zip(proxies, result) if orig.get("name") != new.get("name"))
    if renamed:
        logger.info("Unique names: renamed %d/%d proxies", renamed, len(proxies))
    return result


def merge_proxies_priority(
    priority: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge two proxy lists; priority wins on duplicate nodes (keeps priority name).

    Node dedup uses server/port/type key; name collisions get #2, #3, ... suffixes.
    """
    seen: Dict[str, int] = {}
    used_names: set[str] = set()
    result: List[Dict[str, Any]] = []

    for proxy in priority:
        key = _dedup_key(proxy)
        if key in seen:
            idx = seen[key]
            old_name = result[idx].get("name", "")
            if old_name in used_names:
                used_names.discard(old_name)
            result[idx] = _with_unique_name(proxy, used_names)
        else:
            seen[key] = len(result)
            result.append(_with_unique_name(proxy, used_names))

    for proxy in secondary:
        key = _dedup_key(proxy)
        if key not in seen:
            seen[key] = len(result)
            result.append(_with_unique_name(proxy, used_names))

    logger.info(
        "Merge: %d + %d -> %d proxies",
        len(priority),
        len(secondary),
        len(result),
    )
    return result


def _allocate_unique_name(name: str, used_names: set[str]) -> str:
    """Return a unique proxy name, appending #2, #3, ... on collision."""
    if name not in used_names:
        used_names.add(name)
        return name
    n = 2
    while True:
        candidate = f"{name}#{n}"
        if candidate not in used_names:
            used_names.add(candidate)
            logger.info("Rename proxy: %s -> %s", name, candidate)
            return candidate
        n += 1


def _with_unique_name(proxy: Dict[str, Any], used_names: set[str]) -> Dict[str, Any]:
    """Ensure proxy has a unique name; returns a copy when renamed."""
    name = str(proxy.get("name", ""))
    unique = _allocate_unique_name(name, used_names)
    if unique == name:
        return proxy
    renamed = dict(proxy)
    renamed["name"] = unique
    return renamed


def _dedup_key(proxy: Dict[str, Any]) -> str:
    """Generate a deduplication key for a proxy.

    Args:
        proxy: Proxy dictionary.

    Returns:
        String key for comparison.
    """
    ptype = proxy.get("type", "").lower()

    parts = [
        proxy.get("server", ""),
        str(proxy.get("port", "")),
        ptype,
    ]

    if ptype == "vmess":
        parts.extend([
            proxy.get("network", ""),
            str(proxy.get("tls", "")),
            _ws_opts_key(proxy.get("ws-opts")),
            proxy.get("cipher", ""),
        ])
    elif ptype == "ss":
        parts.extend([
            proxy.get("cipher", ""),
            str(proxy.get("plugin", "")),
            proxy.get("obfs", ""),
        ])
    elif ptype == "trojan":
        parts.extend([
            str(proxy.get("tls", "")),
            proxy.get("network", ""),
            _ws_opts_key(proxy.get("ws-opts")),
        ])
    elif ptype == "ssr":
        parts.extend([
            proxy.get("cipher", ""),
            proxy.get("obfs", ""),
            proxy.get("protocol", ""),
        ])
    elif ptype == "vless":
        parts.extend([
            proxy.get("network", ""),
            str(proxy.get("tls", "")),
            _ws_opts_key(proxy.get("ws-opts")),
            proxy.get("flow", ""),
        ])
    elif ptype in ("hysteria2", "hy2"):
        parts.extend([
            proxy.get("obfs", ""),
            proxy.get("sni", ""),
        ])
    else:
        # For unknown types, use server+port+type as key
        pass

    return "|".join(parts)


def _ws_opts_key(ws_opts: Any) -> str:
    """Generate key part for ws-opts.

    Args:
        ws_opts: ws-opts dictionary or None.

    Returns:
        String key part.
    """
    if not ws_opts or not isinstance(ws_opts, dict):
        return ""
    path = ws_opts.get("path", "")
    host = ""
    headers = ws_opts.get("headers", {})
    if isinstance(headers, dict):
        host = headers.get("Host", "")
    return f"{path}@{host}"
