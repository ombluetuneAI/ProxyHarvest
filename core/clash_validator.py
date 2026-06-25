"""Validate proxy nodes via standalone Mihomo."""

from __future__ import annotations

import copy
import csv
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config_loader import PROJECT_ROOT, load_clash_template
from .converter import FormatConverter
from .mihomo_client import AUTO_SELECT_GROUP
from .mihomo_manager import MihomoManager

logger = logging.getLogger(__name__)


class ClashValidator:
    """Run delay-based validity checks via a standalone Mihomo core."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.cfg = settings.get("mihomo", {})
        self.manager = MihomoManager(settings)
        self.client = None
        self.runtime = None
        self.group_name = self.cfg.get("test_group", AUTO_SELECT_GROUP)

    def validate_proxies(
        self,
        proxies: List[Dict[str, Any]],
        output_dir: str,
        sources: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Test proxy connectivity and optionally summarize per subscription source."""
        if not proxies:
            raise ValueError("No proxies to validate")

        os.makedirs(output_dir, exist_ok=True)
        total = len(proxies)
        print(f"Validating {total} nodes via mihomo ...", flush=True)
        node_results = self._validate_standalone(proxies)

        name_to_source = {p.get("name", ""): p.get("_source_id", -1) for p in proxies}
        alive = []
        dead = []
        for name in [p.get("name", "") for p in proxies]:
            info = node_results.get(name, {})
            delay = int(info.get("delay") or 0)
            entry = {"name": name, "delay": delay, "alive": delay > 0}
            if name_to_source.get(name, -1) != -1:
                entry["source_id"] = name_to_source[name]
            if delay > 0:
                alive.append(entry)
            else:
                dead.append(entry)

        source_summary = self._summarize_sources(alive, dead, sources)

        report = {
            "backend": "mihomo-standalone",
            "transport": "http",
            "test_url": self.runtime.test_url,
            "timeout_ms": self.runtime.timeout_ms,
            "total": len(proxies),
            "alive": len(alive),
            "dead": len(dead),
            "nodes": sorted(alive + dead, key=lambda x: (not x["alive"], x.get("delay", 0))),
            "sources": source_summary,
        }

        report_path = os.path.join(output_dir, "clash_validate_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Validation report: %s", report_path)

        if source_summary:
            self._write_source_csv(source_summary, output_dir)

        self._print_summary(report)
        return report

    def _validate_standalone(self, proxies: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Spin up a dedicated mihomo core, run delay tests, tear it down."""
        self.client = self.manager.start(proxies)
        self.runtime = self.client.runtime
        try:
            return self._run_delay_tests(
                [p["name"] for p in proxies if p.get("name")]
            )
        finally:
            self.manager.stop()

    def _print_progress(self, done: int, total: int, name: str, delay: int) -> None:
        status = f"OK {delay}ms" if delay > 0 else "FAIL"
        encoding = sys.stdout.encoding or "utf-8"
        safe_name = name.encode(encoding, errors="replace").decode(encoding)
        print(f"[{done}/{total}] {safe_name}: {status}", flush=True)

    def _run_delay_tests(self, proxy_names: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return mapping of proxy name -> {delay, alive}."""
        use_group = bool(self.cfg.get("use_group_test", True))
        fallback_parallel = bool(self.cfg.get("parallel_fallback", True))
        max_workers = int(self.cfg.get("max_workers", 8))
        total = len(proxy_names)

        results: Dict[str, Dict[str, Any]] = {}

        if use_group:
            print(
                f"Testing {total} nodes via group \"{self.group_name}\" ...",
                flush=True,
            )
            try:
                delay_map = self.client.test_group_delay(self.group_name)
                results = self._results_from_delay_map(proxy_names, delay_map)
                alive = sum(1 for v in results.values() if v.get("delay", 0) > 0)
                print(
                    f"[{total}/{total}] group test complete: {alive}/{total} alive",
                    flush=True,
                )
                if alive > 0:
                    logger.info("Group delay test completed via %s", self.group_name)
                    return results
                print(
                    "Group delay test returned no alive nodes, trying per-proxy tests",
                    flush=True,
                )
                logger.warning("Group delay test returned no alive nodes, trying per-proxy tests")
            except Exception as exc:
                print(f"Group delay test failed: {exc}", flush=True)
                logger.warning("Group delay test failed: %s", exc)

        if fallback_parallel:
            print(
                f"Testing {total} nodes individually ({max_workers} workers) ...",
                flush=True,
            )
            results = self._parallel_proxy_tests(proxy_names, max_workers)
        return results

    def _results_from_delay_map(
        self, proxy_names: List[str], delay_map: dict
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for name in proxy_names:
            delay = int(delay_map.get(name) or 0)
            results[name] = {"delay": delay, "alive": delay > 0}
        return results

    def _test_proxy_delay_with_retry(self, name: str) -> int:
        """Probe one proxy; on failure retry up to ``max_retries`` times."""
        max_retries = max(0, int(self.cfg.get("max_retries", 0)))
        delay = 0
        for attempt in range(max_retries + 1):
            delay = self.client.test_proxy_delay(name) or 0
            if delay > 0:
                return delay
            if attempt < max_retries:
                logger.debug("Retry %d/%d for proxy: %s", attempt + 1, max_retries, name)
        return delay

    def _parallel_proxy_tests(
        self, proxy_names: List[str], max_workers: int
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        total = len(proxy_names)
        done = 0
        lock = threading.Lock()

        def _test(name: str) -> tuple[str, int]:
            return name, self._test_proxy_delay_with_retry(name)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_test, name): name for name in proxy_names}
            for future in as_completed(futures):
                name, delay = future.result()
                results[name] = {"delay": delay, "alive": delay > 0}
                with lock:
                    done += 1
                    self._print_progress(done, total, name, delay)
        for name in proxy_names:
            results.setdefault(name, {"delay": 0, "alive": False})
        alive = sum(1 for v in results.values() if v.get("delay", 0) > 0)
        print(f"Per-node test complete: {alive}/{total} alive", flush=True)
        return results

    def _summarize_sources(
        self, alive: List[dict], dead: List[dict], sources: Optional[list]
    ) -> List[dict]:
        if not any("source_id" in x for x in alive + dead):
            return []

        id_to_remarks = {}
        if sources:
            id_to_remarks = {s.get("id", -1): s.get("remarks", "") for s in sources}

        totals: Dict[int, int] = {}
        valid: Dict[int, int] = {}
        for entry in alive + dead:
            sid = entry.get("source_id", -1)
            if sid == -1:
                continue
            totals[sid] = totals.get(sid, 0) + 1
            if entry.get("alive"):
                valid[sid] = valid.get(sid, 0) + 1

        rows = []
        for sid in sorted(totals):
            total = totals[sid]
            ok = valid.get(sid, 0)
            rows.append({
                "source_id": sid,
                "remarks": id_to_remarks.get(sid, ""),
                "total_nodes": total,
                "valid_nodes": ok,
                "valid_rate": round(ok / total * 100, 1) if total else 0.0,
            })
        return rows

    def _write_source_csv(self, rows: List[dict], output_dir: str) -> None:
        path = os.path.join(output_dir, "clash_validate_summary.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["source_id", "remarks", "total_nodes", "valid_nodes", "valid_rate"],
            )
            writer.writeheader()
            writer.writerows(rows)
        logger.info("Source summary: %s", path)

    @staticmethod
    def _print_summary(report: dict) -> None:
        print("\n" + "=" * 64)
        print(" MIHOMO VALIDATION")
        print("=" * 64)
        print(f"Backend   : {report.get('backend')}")
        print(f"Test URL  : {report.get('test_url')}")
        print(f"Nodes     : {report.get('alive')}/{report.get('total')} alive")
        sources = report.get("sources") or []
        if sources:
            print("-" * 64)
            print(f"| {'source_id':>10} | {'remarks':<22} | {'valid':>5} | {'total':>5} | {'rate':>6} |")
            print("-" * 64)
            for row in sources:
                print(
                    f"| {str(row['source_id']):>10} | {row['remarks']:<22} | "
                    f"{row['valid_nodes']:>5} | {row['total_nodes']:>5} | {row['valid_rate']:>5.1f}% |"
                )
        print("=" * 64)


def load_proxies_for_validation(settings: dict, input_path: Optional[str] = None) -> List[dict]:
    """Load proxies from clash yaml; default to configured mihomo input."""
    if input_path:
        path = Path(input_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    else:
        default = settings.get("mihomo", {}).get("input", "output/nodes_clash.yaml")
        path = PROJECT_ROOT / default

    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    proxies = data.get("proxies") or []
    if not proxies:
        raise ValueError(f"No proxies in {path}")
    return proxies


def write_validated_clash(
    input_path: str | Path,
    output_path: str | Path,
    alive_names: set[str],
    settings: Optional[dict] = None,
) -> int:
    """Write a Clash config keeping only alive proxies and updating proxy-groups."""
    in_path = Path(input_path)
    out_path = Path(output_path)
    if not in_path.is_absolute():
        in_path = PROJECT_ROOT / in_path
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path

    with open(in_path, "r", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}

    filtered = [
        p for p in base.get("proxies", [])
        if isinstance(p, dict) and p.get("name") in alive_names
    ]

    template_path = None
    if settings:
        template_path = settings.get("mihomo", {}).get("template") or settings.get(
            "paths", {}
        ).get("clash_template")
    template = load_clash_template(template_path)
    config = FormatConverter.build_clash_config(template, filtered)

    # Keep routing/DNS customisations from the source file.
    for key in ("rules", "rule-providers", "dns"):
        if key in base:
            config[key] = copy.deepcopy(base[key])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    logger.info(
        "Validated Clash config: %d/%d proxies -> %s",
        len(filtered),
        len(base.get("proxies", [])),
        out_path,
    )
    return len(filtered)
