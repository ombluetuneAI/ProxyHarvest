#!/usr/bin/env python3
"""Local Clash Verge validation + publish.

1. Force-sync local repo to remote latest (discards local commits/changes)
2. Run clash-validate-verge on nodes_clash.yaml -> output/clash.yaml
3. Commit output/clash.yaml and push to remote

Prerequisites:
    - Clash Verge Rev running (verge-mihomo core up)
    - pywin32 on Windows (pip install -r requirements.txt)
    - git remote configured with push access

Usage (from project root):
    python test/verge_validate_and_push.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NODES_INPUT = PROJECT_ROOT / "output" / "nodes_clash.yaml"
CLASH_OUTPUT = PROJECT_ROOT / "output" / "clash.yaml"
RUN_PY = PROJECT_ROOT / "scripts" / "run.py"


def _run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    kwargs: dict = {"cwd": PROJECT_ROOT, "check": check, "text": True}
    if capture:
        kwargs["capture_output"] = True
    return subprocess.run(cmd, **kwargs)


def _git(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return _run(["git", *args], check=check, capture=capture)


def _git_output(*args: str) -> str:
    result = _git(*args, capture=True)
    return (result.stdout or "").strip()


def _require_cmd(name: str) -> None:
    if subprocess.run(
        [name, "--version"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode != 0:
        print(f"ERROR: {name} not found. Please install it and add to PATH.", file=sys.stderr)
        sys.exit(1)


def _sync_ref() -> str:
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if upstream.returncode == 0 and (upstream.stdout or "").strip():
        return upstream.stdout.strip()

    name = _git_output("rev-parse", "--abbrev-ref", "HEAD")
    if not name or name == "HEAD":
        print("ERROR: Detached HEAD; checkout a branch before running this script.", file=sys.stderr)
        sys.exit(1)
    return f"origin/{name}"


def _current_branch() -> str:
    return _git_output("rev-parse", "--abbrev-ref", "HEAD")


def sync_remote() -> str:
    sync_ref = _sync_ref()
    print(f"[1/3] Syncing to remote latest ({sync_ref})...")
    print("       WARNING: local uncommitted changes and unpushed commits will be discarded.")
    _git("fetch", "origin")
    _git("reset", "--hard", sync_ref)
    _git("clean", "-fd", "--", "output/tmp/", check=False)
    print(f"       Synced to {sync_ref}")
    return sync_ref


def run_validation() -> None:
    print("[2/3] Running Clash Verge validation...")
    print("       Ensure Clash Verge Rev is running before continuing.")
    _run([
        sys.executable,
        str(RUN_PY),
        "clash-validate-verge",
        "--input", str(NODES_INPUT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "--output", str(CLASH_OUTPUT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    ])
    if not CLASH_OUTPUT.is_file():
        print("ERROR: output/clash.yaml was not generated.", file=sys.stderr)
        sys.exit(1)
    print("       Validation complete -> output/clash.yaml")


def commit_and_push(branch: str) -> None:
    print("[3/3] Committing and pushing output/clash.yaml...")
    _git("add", "output/clash.yaml")

    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("       No changes in output/clash.yaml; nothing to push.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"update: clash.yaml from Clash Verge validation {timestamp}"
    _git("commit", "-m", message)
    _git("push", "origin", branch)
    print()
    print("=== Done ===")
    print(f"Committed: {message}")
    print(f"Pushed to origin/{branch}")


def main() -> None:
    if not (PROJECT_ROOT / ".git").is_dir():
        print(f"ERROR: Not a git repository: {PROJECT_ROOT}", file=sys.stderr)
        sys.exit(1)

    _require_cmd("git")

    print("=== ProxyHarvest: Clash Verge validate & push ===")
    print(f"Project: {PROJECT_ROOT}")
    print()

    branch = _current_branch()
    sync_remote()
    print()
    run_validation()
    print()
    commit_and_push(branch)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(exc.cmd) if exc.cmd else "command"
        print(f"ERROR: {cmd} failed (exit {exc.returncode}).", file=sys.stderr)
        sys.exit(exc.returncode or 1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
