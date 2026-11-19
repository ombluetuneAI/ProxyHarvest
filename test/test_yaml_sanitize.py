#!/usr/bin/env python3
"""Regression tests for Clash YAML name sanitization."""

import re
import sys
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.converter import dump_clash_yaml  # noqa: E402
from core.yaml_sanitize import (  # noqa: E402
    clash_config_has_unsafe_scalars,
    dumped_yaml_has_forbidden_escapes,
    sanitize_yaml_scalar,
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Reproduces a name that caused Go/Clash parsers to reject output/clash.yaml.
_BAD_NAME = "🇳🇱%\ufffd\x11\ufffd%\ufffdP\ufffd\ufffd\ufffdNL-109.104.153.43-0213"
_CLASH_YAML_PATH = PROJECT_ROOT / "output" / "clash.yaml"
_NODES_CLASH_PATH = PROJECT_ROOT / "output" / "nodes_clash.yaml"


class TestYamlSanitize(unittest.TestCase):
    def test_sanitize_strips_control_and_replacement(self):
        cleaned = sanitize_yaml_scalar(_BAD_NAME)
        self.assertNotIn("\x11", cleaned)
        self.assertNotIn("\ufffd", cleaned)
        self.assertTrue(cleaned.startswith("🇳🇱"))
        self.assertIn("NL-109.104.153.43-0213", cleaned)

    def test_dump_avoids_x_and_u_escapes_in_names(self):
        config = {
            "proxies": [
                {
                    "name": _BAD_NAME,
                    "server": "109.104.153.43",
                    "port": 33336,
                    "type": "ss",
                }
            ],
            "proxy-groups": [
                {
                    "name": "🚀 节点选择",
                    "type": "select",
                    "proxies": [_BAD_NAME, "DIRECT"],
                }
            ],
        }
        text = dump_clash_yaml(config)
        self.assertIsNotNone(text)
        self.assertFalse(dumped_yaml_has_forbidden_escapes(text))
        loaded = yaml.safe_load(text)
        self.assertFalse(clash_config_has_unsafe_scalars(loaded))
        for proxy in loaded["proxies"]:
            name = proxy["name"]
            self.assertIsNone(_CONTROL_RE.search(name))

    def test_committed_clash_yaml_has_no_forbidden_escapes(self):
        if not _CLASH_YAML_PATH.exists():
            self.skipTest("output/clash.yaml not present")
        raw = _CLASH_YAML_PATH.read_text(encoding="utf-8")
        self.assertFalse(
            dumped_yaml_has_forbidden_escapes(raw),
            "output/clash.yaml still contains \\x or \\U name escapes",
        )
        data = yaml.safe_load(raw)
        self.assertFalse(clash_config_has_unsafe_scalars(data))

    def test_committed_nodes_clash_yaml_has_no_forbidden_escapes(self):
        if not _NODES_CLASH_PATH.exists():
            self.skipTest("output/nodes_clash.yaml not present")
        raw = _NODES_CLASH_PATH.read_text(encoding="utf-8")
        self.assertFalse(
            dumped_yaml_has_forbidden_escapes(raw),
            "output/nodes_clash.yaml still contains \\x or \\U name escapes",
        )
        data = yaml.safe_load(raw)
        self.assertFalse(clash_config_has_unsafe_scalars(data))


if __name__ == "__main__":
    unittest.main()