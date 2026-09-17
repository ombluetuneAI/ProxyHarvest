#!/usr/bin/env python3
"""Local Clash Verge validation + publish.

1. Ensure Clash Verge / proxy (start GUI if needed; stop it on exit if we started it)
2. Force-sync local repo to remote latest (discards local commits/changes)
3. Run clash-validate-verge on nodes_clash.yaml -> output/clash.yaml
4. Commit output/clash.yaml and push to remote

Prerequisites:
    - Clash Verge Rev installed (script starts it if the core is not running)
    - pywin32 on Windows (pip install -r requirements.txt)
    - git remote configured with push access

Usage (from project root):
    python test/verge_validate_and_push.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_settings
from core.verge_manager import VergeRuntimeSession

NODES_INPUT = PROJECT_ROOT / "output" / "nodes_clash.yaml"
CLASH_OUTPUT = PROJECT_ROOT / "output" / "clash.yaml"
RUN_PY = PROJECT_ROOT / "scripts" / "run.py"


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict = {"cwd": PROJECT_ROOT, "check": check, "text": True}
    if capture:
        kwargs["capture_output"] = True
    if env is not None:
        merged = os.environ.copy()
        merged.update(env)
        kwargs["env"] = merged
    return subprocess.run(cmd, **kwargs)


def _git(
    *args: str,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return _run(["git", *args], check=check, capture=capture, env=env)


def _git_output(*args: str, env: dict[str, str] | None = None) -> str:
    result = _git(*args, capture=True, env=env)
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


def _sync_ref(env: dict[str, str] | None = None) -> str:
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=merged_env,
    )
    if upstream.returncode == 0 and (upstream.stdout or "").strip():
        return upstream.stdout.strip()

    name = _git_output("rev-parse", "--abbrev-ref", "HEAD", env=env)
    if not name or name == "HEAD":
        print("ERROR: Detached HEAD; checkout a branch before running this script.", file=sys.stderr)
        sys.exit(1)
    return f"origin/{name}"


def _current_branch(env: dict[str, str] | None = None) -> str:
    return _git_output("rev-parse", "--abbrev-ref", "HEAD", env=env)


def sync_remote(env: dict[str, str]) -> str:
    sync_ref = _sync_ref(env)
    print(f"[2/4] Syncing to remote latest ({sync_ref})...")
    print("       WARNING: local uncommitted changes and unpushed commits will be discarded.")
    _git("fetch", "origin", env=env)
    _git("reset", "--hard", sync_ref, env=env)
    _git("clean", "-fd", "--", "output/tmp/", check=False, env=env)
    print(f"       Synced to {sync_ref}")
    return sync_ref


def run_validation() -> None:
    print("[3/4] Running Clash Verge validation...")
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


def commit_and_push(branch: str, env: dict[str, str]) -> None:
    print("[4/4] Committing and pushing output/clash.yaml...")
    _git("add", "output/clash.yaml", env=env)

    if _git("diff", "--cached", "--quiet", check=False, env=env).returncode == 0:
        print("       No changes in output/clash.yaml; nothing to push.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"update: clash.yaml from Clash Verge validation {timestamp}"
    _git("commit", "-m", message, env=env)
    _git("push", "origin", branch, env=env)
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

    settings = load_settings()
    branch = _current_branch()

    print("[1/4] Clash Verge and GitHub proxy...")
    try:
        with VergeRuntimeSession(settings) as verge:
            if verge.launched_by_script:
                print("       Started Clash Verge (will stop when this script exits).")
            else:
                print("       Clash Verge already running.")
            if verge.mode_note:
                for line in verge.mode_note.splitlines():
                    print(f"       {line}")
            print(f"       Proxy ready (mixed-port {verge.mixed_port}).")
            print()

            git_env = verge.git_env()
            sync_remote(git_env)
            print()
            run_validation()
            print()
            commit_and_push(branch, git_env)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


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
