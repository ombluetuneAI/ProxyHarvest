"""Configuration loader for V2RayAggregator."""

import os
import yaml
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Project root directory (parent of core/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_settings(settings_path: Optional[str] = None) -> Dict[str, Any]:
    """Load global settings from settings.yaml.

    Args:
        settings_path: Optional explicit path. Defaults to config/settings.yaml under project root.

    Returns:
        Parsed settings dictionary with resolved paths.
    """
    if settings_path is None:
        settings_path = PROJECT_ROOT / "config" / "settings.yaml"

    settings_path = Path(settings_path)
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    # Resolve relative paths to absolute paths based on project root
    if "paths" in settings:
        for key, val in settings["paths"].items():
            p = Path(val)
            if not p.is_absolute():
                settings["paths"][key] = str(PROJECT_ROOT / p)
            else:
                settings["paths"][key] = str(p)

    # Resolve binary paths
    for tool in ("subconverter", "singtools"):
        if tool in settings and "binary_path" in settings[tool]:
            for platform_key, rel_path in settings[tool]["binary_path"].items():
                p = Path(rel_path)
                if not p.is_absolute():
                    settings[tool]["binary_path"][platform_key] = str(PROJECT_ROOT / p)

    # Resolve config paths within tool sections
    for tool in ("subconverter", "singtools"):
        if tool in settings and "config" in settings[tool]:
            p = Path(settings[tool]["config"])
            if not p.is_absolute():
                settings[tool]["config"] = str(PROJECT_ROOT / p)

    logger.info("Settings loaded from %s", settings_path)
    return settings


def load_sub_sources(sources_path: Optional[str] = None) -> list:
    """Load subscription sources from JSON file.

    Args:
        sources_path: Optional explicit path. Defaults to config/sub_sources.json.

    Returns:
        List of source dictionaries.
    """
    if sources_path is None:
        sources_path = PROJECT_ROOT / "config" / "sub_sources.json"

    sources_path = Path(sources_path)
    if not sources_path.exists():
        raise FileNotFoundError(f"Sub sources file not found: {sources_path}")

    with open(sources_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    logger.info("Loaded %d subscription sources from %s", len(sources), sources_path)
    return sources


def save_sub_sources(sources: list, sources_path: Optional[str] = None) -> None:
    """Save subscription sources back to JSON file.

    Args:
        sources: List of source dictionaries.
        sources_path: Optional explicit path.
    """
    if sources_path is None:
        sources_path = PROJECT_ROOT / "config" / "sub_sources.json"

    sources_path = Path(sources_path)
    with open(sources_path, "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)

    logger.info("Saved %d subscription sources to %s", len(sources), sources_path)


def load_clash_template(template_path: Optional[str] = None) -> Dict[str, Any]:
    """Load Clash configuration template.

    Args:
        template_path: Optional explicit path.

    Returns:
        Parsed Clash template dictionary.
    """
    if template_path is None:
        template_path = PROJECT_ROOT / "config" / "clash_template.yaml"

    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Clash template not found: {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    logger.info("Clash template loaded from %s", template_path)
    return template


def get_path(settings: Dict[str, Any], key: str) -> str:
    """Get a resolved path from settings.

    Args:
        settings: Settings dictionary.
        key: Path key (e.g., 'sub_dir', 'output_dir').

    Returns:
        Resolved absolute path string.
    """
    return settings.get("paths", {}).get(key, "")
