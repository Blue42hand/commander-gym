#!/usr/bin/env python3
"""Install/manage the persistent local Commander Gym Argentum gateway on macOS."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LABEL = "io.commander-gym.argentum-gateway"
DEFAULT_STATUS_URL = "http://127.0.0.1:8082/status"


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def resolve_repo_root(candidate: str | None) -> Path:
    cwd = Path(candidate).expanduser().resolve() if candidate else Path.cwd()
    result = run("git", "rev-parse", "--show-toplevel", cwd=cwd)
    return Path(result.stdout.strip()).resolve()


def checkout_revision(repo_root: Path) -> str:
    return run("git", "rev-parse", "HEAD", cwd=repo_root).stdout.strip()


def assert_clean_tracked_checkout(repo_root: Path) -> None:
    dirty = run(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
        cwd=repo_root,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            "tracked Commander Gym files are dirty; commit/stash them before installing the gateway"
        )


def default_paths(home: Path) -> dict[str, Path]:
    return {
        "plist": home / "Library" / "LaunchAgents" / f"{LABEL}.plist",
        "state_dir": home / "Library" / "Application Support" / "Commander Gym" / "argentum-gateway",
        "log_dir": home / "Library" / "Logs" / "Commander Gym" / "argentum-gateway",
    }


def ensure_token(token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.exists():
        mode = stat.S_IMODE(token_path.stat().st_mode)
        if mode != 0o600:
            token_path.chmod(0o600)
        if not token_path.read_text(encoding="utf-8").strip():
            raise RuntimeError(f"gateway token file is blank: {token_path}")
        return

    token_path.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    token_path.chmod(0o600)


def render_plist(
    *,
    repo_root: Path,
    revision: str,
    stdout_path: Path,
    stderr_path: Path,
    token_path: Path,
) -> bytes:
    runner = repo_root / "scripts" / "run_argentum_gateway_service.sh"
    payload: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/bash",
            str(runner),
            str(repo_root),
            revision,
            str(token_path),
        ],
        "WorkingDirectory": str(repo_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "COMMANDER_GYM_GATEWAY_BIND": "127.0.0.1",
            "COMMANDER_GYM_GATEWAY_PORT": "8082",
            "COMMANDER_GYM_GATEWAY_UPSTREAM": "http://127.0.0.1:8081",
        },
    }
    return plistlib.dumps(payload, sort_keys=True)


def service_metadata(
    *,
    repo_root: Path,
    revision: str,
    plist_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    token_path: Path,
) -> dict[str, Any]:
    return {
        "schema": "commander-gym-argentum-gateway-launchd-v1",
        "label": LABEL,
        "repository": str(repo_root),
        "buildRevision": revision,
        "plist": str(plist_path),
        "stdoutLog": str(stdout_path),
        "stderrLog": str(stderr_path),
        "tokenFile": str(token_path),
        "statusUrl": DEFAULT_STATUS_URL,
        "installedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def launchd_target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def install(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise RuntimeError("install is supported only on macOS; use render for CI/inspection")

    repo_root = resolve_repo_root(args.repo)
    assert_clean_tracked_checkout(repo_root)
    revision = checkout_revision(repo_root)
    paths = default_paths(Path.home())
    plist_path = paths["plist"]
    state_dir = paths["state_dir"]
    log_dir = paths["log_dir"]
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    token_path = state_dir / "bearer-token"

    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    ensure_token(token_path)

    plist = render_plist(
        repo_root=repo_root,
        revision=revision,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        token_path=token_path,
    )
    write_atomic(plist_path, plist)

    metadata = service_metadata(
        repo_root=repo_root,
        revision=revision,
        plist_path=plist_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        token_path=token_path,
    )
    write_atomic(
        state_dir / "service.json",
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    run("launchctl", "bootout", launchd_target(), check=False)
    run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path))
    run("launchctl", "enable", launchd_target())
    run("launchctl", "kickstart", "-k", launchd_target())

    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


def render(args: argparse.Namespace) -> int:
    sys.stdout.buffer.write(
        render_plist(
            repo_root=Path(args.repo).expanduser().resolve(),
            revision=args.revision,
            stdout_path=Path(args.stdout).expanduser().resolve(),
            stderr_path=Path(args.stderr).expanduser().resolve(),
            token_path=Path(args.token_file).expanduser().resolve(),
        )
    )
    return 0


def fetch_status(token_path: Path) -> tuple[int, str]:
    try:
        token = token_path.read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            DEFAULT_STATUS_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read().decode("utf-8")
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def status(_args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise RuntimeError("status is supported only on macOS")

    paths = default_paths(Path.home())
    token_path = paths["state_dir"] / "bearer-token"
    launch = run("launchctl", "print", launchd_target(), check=False)
    http_status, body = fetch_status(token_path)
    payload = {
        "launchdLoaded": launch.returncode == 0,
        "launchd": launch.stdout if launch.returncode == 0 else launch.stderr,
        "httpStatus": http_status,
        "serviceStatus": body,
        "tokenFile": str(token_path),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if launch.returncode == 0 and http_status == 200 else 1


def print_token(_args: argparse.Namespace) -> int:
    """Print the bearer token for local clipboard transfer.

    The token is intentionally absent from plists, service metadata, logs, and git.
    """
    token_path = default_paths(Path.home())["state_dir"] / "bearer-token"
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("gateway token file is blank")
    print(token)
    return 0


def uninstall(_args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise RuntimeError("uninstall is supported only on macOS")

    paths = default_paths(Path.home())
    run("launchctl", "bootout", launchd_target(), check=False)
    paths["plist"].unlink(missing_ok=True)
    print("Uninstalled gateway launch agent. Logs, metadata, and bearer token were retained.")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    install_p = sub.add_parser("install", help="install/reload the persistent gateway")
    install_p.add_argument("--repo", help="Commander Gym checkout; defaults to current git checkout")
    install_p.set_defaults(func=install)

    render_p = sub.add_parser("render", help="render a plist without touching launchd")
    render_p.add_argument("--repo", required=True)
    render_p.add_argument("--revision", required=True)
    render_p.add_argument("--stdout", required=True)
    render_p.add_argument("--stderr", required=True)
    render_p.add_argument("--token-file", required=True)
    render_p.set_defaults(func=render)

    status_p = sub.add_parser("status", help="show launchd and authenticated gateway status")
    status_p.set_defaults(func=status)

    token_p = sub.add_parser("print-token", help="print the local bearer token")
    token_p.set_defaults(func=print_token)

    uninstall_p = sub.add_parser("uninstall", help="unload and remove the launch agent")
    uninstall_p.set_defaults(func=uninstall)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
