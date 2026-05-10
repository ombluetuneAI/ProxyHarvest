"""Output formatting: Clash YAML, Base64, Mixed."""

import os
import base64
import logging
from typing import List, Dict, Any, Optional

import yaml

from .converter import SubConverter, FormatConverter
from .config_loader import load_clash_template

logger = logging.getLogger(__name__)


class NodeFormatter:
    """Format and output proxy nodes in various formats."""

    def __init__(self, settings: dict, subconverter: SubConverter = None):
        self.settings = settings
        self.subconverter = subconverter
        self.filenames = settings.get("output", {}).get("filenames", {})
        self.formats = settings.get("output", {}).get("formats", [])
        self.split_by_protocol = settings.get("output", {}).get(
            "split_by_protocol", True
        )
        self.top_n = settings.get("output", {}).get("top_nodes", 200)

    def format_and_output(self, proxies: List[Dict[str, Any]],
                          filtered_result: Dict[str, Any],
                          output_dir: str) -> Dict[str, str]:
        """Format and output nodes in configured formats.

        Args:
            proxies: Top proxies (for filtered output).
            filtered_result: Result from NodeFilter.filter_results().
            output_dir: Output directory.

        Returns:
            Dictionary of {format_name: output_path}.
        """
        os.makedirs(output_dir, exist_ok=True)
        outputs = {}

        # Output top nodes (filtered)
        top_proxies = filtered_result.get("top_proxies", proxies)

        # Output all nodes (with speed)
        all_proxies = filtered_result.get("all_proxies", [])

        if "clash_yaml" in self.formats:
            path = self._write_clash_yaml(top_proxies, output_dir)
            if path:
                outputs["clash_yaml"] = path

        if "base64" in self.formats:
            path = self._write_base64(top_proxies, output_dir)
            if path:
                outputs["base64"] = path

        if "mixed" in self.formats:
            path = self._write_mixed(top_proxies, output_dir)
            if path:
                outputs["mixed"] = path

        # nodes_all_mixed.txt (all nodes with speed)
        if all_proxies:
            path = self._write_all_mixed(all_proxies, output_dir)
            if path:
                outputs["all_mixed"] = path

        # Split by protocol
        if self.split_by_protocol and top_proxies:
            split_dir = os.path.join(output_dir, "sub", "splitted")
            self._split_by_protocol(top_proxies, split_dir)

        logger.info("Output files: %s", outputs)
        return outputs

    def _write_clash_yaml(self, proxies: List[Dict[str, Any]],
                            output_dir: str) -> Optional[str]:
        """Write Clash YAML configuration file.

        Args:
            proxies: List of proxy dictionaries.
            output_dir: Output directory.

        Returns:
            Path to output file, or None.
        """
        filename = self.filenames.get("clash_yaml", "nodes_clash.yaml")
        path = os.path.join(output_dir, filename)

        try:
            template = load_clash_template(
                self.settings.get('paths', {}).get('clash_template', None)
            )
            config = FormatConverter.build_clash_config(template, proxies)
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, sort_keys=False)
            logger.info("Written Clash YAML: %s (%d proxies)", path, len(proxies))
            return path
        except Exception as e:
            logger.error("Failed to write Clash YAML: %s", e)
            return None

    def _write_base64(self, proxies: List[Dict[str, Any]],
                       output_dir: str) -> Optional[str]:
        """Write Base64 encoded subscription.

        Args:
            proxies: List of proxy dictionaries.
            output_dir: Output directory.

        Returns:
            Path to output file, or None.
        """
        filename = self.filenames.get("base64", "nodes_base64.txt")
        path = os.path.join(output_dir, filename)

        try:
            # Convert to clash YAML first, then to mixed, then base64
            yaml_content = yaml.dump({"proxies": proxies}, allow_unicode=True)
            mixed = self._yaml_to_mixed(yaml_content)
            b64 = FormatConverter.base64_encode(mixed)

            with open(path, "w", encoding="utf-8") as f:
                f.write(b64)
            logger.info("Written Base64: %s (%d bytes)", path, len(b64))
            return path
        except Exception as e:
            logger.error("Failed to write Base64: %s", e)
            return None

    def _write_mixed(self, proxies: List[Dict[str, Any]],
                      output_dir: str) -> Optional[str]:
        """Write Mixed format subscription.

        Args:
            proxies: List of proxy dictionaries.
            output_dir: Output directory.

        Returns:
            Path to output file, or None.
        """
        filename = self.filenames.get("mixed", "nodes_mixed.txt")
        path = os.path.join(output_dir, filename)

        try:
            yaml_content = yaml.dump({"proxies": proxies}, allow_unicode=True)
            mixed = self._yaml_to_mixed(yaml_content)

            with open(path, "w", encoding="utf-8") as f:
                f.write(mixed)
            logger.info("Written Mixed: %s (%d bytes)", path, len(mixed))
            return path
        except Exception as e:
            logger.error("Failed to write Mixed: %s", e)
            return None

    def _write_all_mixed(self, proxies: List[Dict[str, Any]],
                          output_dir: str) -> Optional[str]:
        """Write all nodes (with speed) in Mixed format.

        Args:
            proxies: List of proxy dictionaries.
            output_dir: Output directory.

        Returns:
            Path to output file, or None.
        """
        filename = self.filenames.get("all_mixed", "nodes_all_mixed.txt")
        path = os.path.join(output_dir, filename)

        try:
            yaml_content = yaml.dump({"proxies": proxies}, allow_unicode=True)
            mixed = self._yaml_to_mixed(yaml_content)

            with open(path, "w", encoding="utf-8") as f:
                f.write(mixed)
            logger.info("Written All Mixed: %s (%d bytes)", path, len(mixed))
            return path
        except Exception as e:
            logger.error("Failed to write All Mixed: %s", e)
            return None

    def _yaml_to_mixed(self, yaml_content: str) -> str:
        """Convert Clash YAML proxies to Mixed format.

        Uses subconverter API if available, otherwise does a basic
        local conversion by encoding each proxy to its URI scheme.

        Args:
            yaml_content: YAML string with 'proxies:' key.

        Returns:
            Mixed format string (ss://, vmess://, trojan://, etc.).
        """
        import tempfile

        # Try subconverter first (best quality)
        if self.subconverter and self.subconverter._is_running():
            try:
                # Write temp YAML file for subconverter to read
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False, encoding="utf-8"
                ) as f:
                    f.write(yaml_content)
                    temp_path = f.name

                result = self.subconverter.convert(
                    url=temp_path, target="mixed", list_mode=True, timeout=60
                )
                os.unlink(temp_path)

                if result:
                    return result.strip()
            except Exception as e:
                logger.warning("subconverter mixed conversion failed: %s", e)

        # Fallback: basic local conversion
        return self._local_yaml_to_mixed(yaml_content)

    @staticmethod
    def _local_yaml_to_mixed(yaml_content: str) -> str:
        """Basic local YAML-to-mixed conversion.

        Converts each proxy dict to its standard URI scheme.

        Args:
            yaml_content: YAML string with 'proxies:' key.

        Returns:
            Mixed format string with one URI per line.
        """
        import base64 as _b64
        import json as _json
        import urllib.parse

        try:
            data = yaml.safe_load(yaml_content)
        except yaml.YAMLError:
            return yaml_content

        proxies = data.get("proxies", []) if isinstance(data, dict) else []
        lines = []

        for p in proxies:
            ptype = p.get("type", "")
            name = urllib.parse.quote(p.get("name", ""))

            if ptype == "ss":
                # ss://base64(method:password)@server:port#name
                method = p.get("cipher", "")
                password = p.get("password", "")
                userinfo = _b64.b64encode(
                    f"{method}:{password}".encode()
                ).decode()
                server = p.get("server", "")
                port = p.get("port", "")
                line = f"ss://{userinfo}@{server}:{port}#{name}"
                # Add plugin info if present
                plugin = p.get("plugin", "")
                if plugin:
                    opts = p.get("plugin-opts", {})
                    plugin_str = f"plugin={plugin}"
                    if opts:
                        for k, v in opts.items():
                            plugin_str += f";{k}={v}"
                    line += f"?{urllib.parse.quote(plugin_str)}"
                lines.append(line)

            elif ptype == "vmess":
                # vmess://base64(json)
                vmess_obj = {
                    "v": "2",
                    "ps": p.get("name", ""),
                    "add": p.get("server", ""),
                    "port": str(p.get("port", "")),
                    "id": p.get("uuid", ""),
                    "aid": str(p.get("alterId", 0)),
                    "net": p.get("network", "tcp"),
                    "type": p.get("network", "tcp"),
                    "host": "",
                    "path": "",
                    "tls": "tls" if p.get("tls", "") else "",
                }
                ws_opts = p.get("ws-opts", {})
                if ws_opts:
                    vmess_obj["host"] = ws_opts.get("headers", {}).get("Host", "")
                    vmess_obj["path"] = ws_opts.get("path", "")
                h2_opts = p.get("h2-opts", {})
                if h2_opts:
                    vmess_obj["host"] = ",".join(h2_opts.get("host", []))
                    vmess_obj["path"] = h2_opts.get("path", "")
                grpc_opts = p.get("grpc-opts", {})
                if grpc_opts:
                    vmess_obj["path"] = grpc_opts.get("grpc-service-name", "")
                    vmess_obj["type"] = "gun"
                encoded = _b64.b64encode(
                    _json.dumps(vmess_obj).encode()
                ).decode()
                lines.append(f"vmess://{encoded}")

            elif ptype == "trojan":
                # trojan://password@server:port?params#name
                password = p.get("password", "")
                server = p.get("server", "")
                port = p.get("port", "")
                params = []
                if p.get("sni", ""):
                    params.append(f"sni={p['sni']}")
                if p.get("network", "tcp") != "tcp":
                    params.append(f"type={p['network']}")
                if p.get("tls", ""):
                    params.append("security=tls")
                query = "&".join(params)
                line = f"trojan://{password}@{server}:{port}"
                if query:
                    line += f"?{query}"
                line += f"#{name}"
                lines.append(line)

            elif ptype == "ssr":
                # Basic ssr:// encoding
                server = p.get("server", "")
                port = p.get("port", "")
                protocol = p.get("protocol", "origin")
                method = p.get("cipher", "")
                obfs = p.get("obfs", "plain")
                password = p.get("password", "")
                password_encoded = _b64.b64encode(
                    password.encode()
                ).decode().rstrip("=")
                remarks = _b64.b64encode(
                    p.get("name", "").encode()
                ).decode().rstrip("=")
                sr = f"{server}:{port}:{protocol}:{method}:{obfs}:{password_encoded}/?remarks={remarks}"
                lines.append(
                    f"ssr://{_b64.b64encode(sr.encode()).decode().rstrip('=')}"
                )

            else:
                # Unknown type, skip
                logger.debug("Skipping unknown proxy type: %s", ptype)

        return "\n".join(lines)

    def _split_by_protocol(self, proxies: List[Dict[str, Any]],
                            output_dir: str) -> None:
        """Split proxies by protocol type and write separate files.

        Args:
            proxies: List of proxy dictionaries.
            output_dir: Output directory for split files.
        """
        os.makedirs(output_dir, exist_ok=True)

        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for proxy in proxies:
            ptype = proxy.get("type", "unknown")
            by_type.setdefault(ptype, []).append(proxy)

        for ptype, type_proxies in by_type.items():
            filename = f"nodes_{ptype}.txt"
            path = os.path.join(output_dir, filename)

            try:
                yaml_content = yaml.dump({"proxies": type_proxies}, allow_unicode=True)
                mixed = self._yaml_to_mixed(yaml_content)
                b64 = FormatConverter.base64_encode(mixed)

                with open(path, "w", encoding="utf-8") as f:
                    f.write(b64)
                logger.info("Written %s split: %s (%d nodes)", ptype, path, len(type_proxies))
            except Exception as e:
                logger.error("Failed to write %s split: %s", ptype, e)
