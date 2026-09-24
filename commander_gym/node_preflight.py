"""Fail-closed installation preflight for a portable Commander Gym node.

This module validates only deployment prerequisites owned by issue #74: pinned
source checkouts, required host commands/files, gateway credentials, optional
local-inference process prerequisites, and physical storage health. It does not
interpret games, pilots, bindings, evidence, datasets, or model policy.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from .node_package import CheckoutSpec, NodePackageConfig, NodePackageError, load_node_package_config
from .storage import StorageError
from .storage_doctor import StorageHealth, check_storage

ACCELERATOR_PROBE_TIMEOUT_SECONDS = 10
_PROBE_OUTPUT_LIMIT = 512


@dataclass(frozen=True)
class CommandHealth:
    command: str
    ok: bool
    resolved_path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class FileHealth:
    name: str
    path: str
    ok: bool
    exists: bool
    readable: bool
    nonempty: bool
    executable: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProbeHealth:
    """Result of one operator-configured, provider-neutral host capability probe."""

    name: str
    command: tuple[str, ...]
    ok: bool
    returncode: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CheckoutHealth:
    name: str
    root: str
    expected_revision: str
    actual_revision: str | None
    clean: bool
    required_files: tuple[FileHealth, ...]
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class NodePreflight:
    commands: tuple[CommandHealth, ...]
    checkouts: tuple[CheckoutHealth, ...]
    gateway_token: FileHealth
    local_inference: tuple[FileHealth | CommandHealth | ProbeHealth, ...]
    storage: StorageHealth

    @property
    def ok(self) -> bool:
        return (
            all(item.ok for item in self.commands)
            and all(item.ok for item in self.checkouts)
            and self.gateway_token.ok
            and all(item.ok for item in self.local_inference)
            and self.storage.ok
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "commands": [asdict(item) for item in self.commands],
            "checkouts": [
                {
                    **asdict(item),
                    "required_files": [asdict(file) for file in item.required_files],
                }
                for item in self.checkouts
            ],
            "gateway_token": asdict(self.gateway_token),
            "local_inference": [asdict(item) for item in self.local_inference],
            "storage": self.storage.to_dict(),
        }


def _check_command(
    command: str,
    *,
    resolver: Callable[[str], str | None] = shutil.which,
) -> CommandHealth:
    resolved = resolver(command)
    if not resolved:
        return CommandHealth(command=command, ok=False, error="command not found on PATH")
    return CommandHealth(command=command, ok=True, resolved_path=resolved)


def _check_file(
    name: str,
    path: Path,
    *,
    require_nonempty: bool = False,
    require_executable: bool = False,
) -> FileHealth:
    try:
        exists = path.is_file()
        if not exists:
            return FileHealth(
                name=name,
                path=str(path),
                ok=False,
                exists=False,
                readable=False,
                nonempty=False,
                executable=None if not require_executable else False,
                error="file does not exist",
            )
        readable = os.access(path, os.R_OK)
        size = path.stat().st_size
        nonempty = size > 0
        executable = os.access(path, os.X_OK) if require_executable else None
        ok = readable and (nonempty or not require_nonempty) and (
            executable if require_executable else True
        )
        error: str | None = None
        if not readable:
            error = "file is not readable"
        elif require_nonempty and not nonempty:
            error = "file is empty"
        elif require_executable and not executable:
            error = "file is not executable"
        return FileHealth(
            name=name,
            path=str(path),
            ok=ok,
            exists=True,
            readable=readable,
            nonempty=nonempty,
            executable=executable,
            error=error,
        )
    except OSError as exc:
        return FileHealth(
            name=name,
            path=str(path),
            ok=False,
            exists=path.exists(),
            readable=False,
            nonempty=False,
            executable=None if not require_executable else False,
            error=str(exc),
        )


def _git(
    root: Path,
    *args: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def check_checkout(
    name: str,
    spec: CheckoutSpec,
    *,
    required_files: Sequence[tuple[str, bool]],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CheckoutHealth:
    """Verify one pinned checkout and its runtime entrypoints.

    ``required_files`` contains repository-relative paths paired with whether the
    file must be executable. Tracked modifications fail closed because the service
    runners promise exact source provenance.
    """

    root = spec.root
    file_health = tuple(
        _check_file(
            f"{name}:{relative}",
            root / relative,
            require_nonempty=True,
            require_executable=executable,
        )
        for relative, executable in required_files
    )
    if not root.is_dir():
        return CheckoutHealth(
            name=name,
            root=str(root),
            expected_revision=spec.revision,
            actual_revision=None,
            clean=False,
            required_files=file_health,
            ok=False,
            error="checkout directory does not exist",
        )

    try:
        revision = _git(root, "rev-parse", "HEAD", runner=runner)
        if revision.returncode != 0:
            return CheckoutHealth(
                name=name,
                root=str(root),
                expected_revision=spec.revision,
                actual_revision=None,
                clean=False,
                required_files=file_health,
                ok=False,
                error=(revision.stderr.strip() or "git rev-parse failed"),
            )
        actual_revision = revision.stdout.strip()

        status = _git(root, "status", "--porcelain", "--untracked-files=no", runner=runner)
        if status.returncode != 0:
            return CheckoutHealth(
                name=name,
                root=str(root),
                expected_revision=spec.revision,
                actual_revision=actual_revision,
                clean=False,
                required_files=file_health,
                ok=False,
                error=(status.stderr.strip() or "git status failed"),
            )
        clean = not status.stdout.strip()
        revision_matches = actual_revision == spec.revision
        ok = revision_matches and clean and all(item.ok for item in file_health)
        error: str | None = None
        if not revision_matches:
            error = f"revision mismatch: expected {spec.revision}, found {actual_revision}"
        elif not clean:
            error = "tracked checkout files are dirty"
        elif not all(item.ok for item in file_health):
            error = "required runtime file check failed"
        return CheckoutHealth(
            name=name,
            root=str(root),
            expected_revision=spec.revision,
            actual_revision=actual_revision,
            clean=clean,
            required_files=file_health,
            ok=ok,
            error=error,
        )
    except OSError as exc:
        return CheckoutHealth(
            name=name,
            root=str(root),
            expected_revision=spec.revision,
            actual_revision=None,
            clean=False,
            required_files=file_health,
            ok=False,
            error=str(exc),
        )


def _trim_probe_output(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) <= _PROBE_OUTPUT_LIMIT:
        return stripped
    return stripped[:_PROBE_OUTPUT_LIMIT] + "..."


def _run_accelerator_probe(
    command: tuple[str, ...],
    *,
    working_directory: Path | None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProbeHealth:
    """Execute one explicitly configured accelerator/capability probe.

    Commander Gym does not infer GPU vendors or accelerator policy. A deployment may
    supply any shell-free argv probe appropriate for that host/provider. Exit status
    zero means the requested capability is available; any other result fails closed.
    """

    try:
        completed = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=ACCELERATOR_PROBE_TIMEOUT_SECONDS,
            cwd=(str(working_directory) if working_directory is not None else None),
        )
    except subprocess.TimeoutExpired as exc:
        return ProbeHealth(
            name="local-inference-accelerator",
            command=command,
            ok=False,
            stdout=_trim_probe_output(exc.stdout if isinstance(exc.stdout, str) else None),
            stderr=_trim_probe_output(exc.stderr if isinstance(exc.stderr, str) else None),
            error=f"accelerator probe timed out after {ACCELERATOR_PROBE_TIMEOUT_SECONDS}s",
        )
    except OSError as exc:
        return ProbeHealth(
            name="local-inference-accelerator",
            command=command,
            ok=False,
            error=str(exc),
        )

    return ProbeHealth(
        name="local-inference-accelerator",
        command=command,
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=_trim_probe_output(completed.stdout),
        stderr=_trim_probe_output(completed.stderr),
        error=(
            None
            if completed.returncode == 0
            else f"accelerator probe exited with status {completed.returncode}"
        ),
    )


def _check_local_inference(
    config: NodePackageConfig,
    *,
    command_resolver: Callable[[str], str | None],
    probe_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[FileHealth | CommandHealth | ProbeHealth, ...]:
    spec = config.local_inference
    if spec is None:
        return ()

    results: list[FileHealth | CommandHealth | ProbeHealth] = []
    command = spec.command[0]
    if "/" in command:
        results.append(
            _check_file(
                "local-inference-command",
                Path(command),
                require_nonempty=True,
                require_executable=True,
            )
        )
    else:
        results.append(_check_command(command, resolver=command_resolver))

    if spec.working_directory is not None:
        path = spec.working_directory
        ok = path.is_dir() and os.access(path, os.R_OK | os.X_OK)
        results.append(
            FileHealth(
                name="local-inference-working-directory",
                path=str(path),
                ok=ok,
                exists=path.is_dir(),
                readable=os.access(path, os.R_OK) if path.exists() else False,
                nonempty=True,
                executable=os.access(path, os.X_OK) if path.exists() else False,
                error=None if ok else "working directory is missing or inaccessible",
            )
        )
    if spec.environment_file is not None:
        results.append(
            _check_file(
                "local-inference-environment-file",
                spec.environment_file,
                require_nonempty=True,
            )
        )
    if spec.accelerator_probe is not None:
        results.append(
            _run_accelerator_probe(
                spec.accelerator_probe,
                working_directory=spec.working_directory,
                runner=probe_runner,
            )
        )
    return tuple(results)


def check_node_preflight(
    config: NodePackageConfig,
    *,
    min_free_bytes: int = 0,
    initialize_storage: bool = False,
    require_systemd: bool = True,
    command_resolver: Callable[[str], str | None] = shutil.which,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    probe_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> NodePreflight:
    """Validate whether a rendered node package has the host prerequisites to start."""

    if not isinstance(config, NodePackageConfig):
        raise NodePackageError("config must be NodePackageConfig")

    required_commands = ["git", "python3", "java"]
    if require_systemd:
        required_commands.append("systemctl")
    commands = tuple(_check_command(command, resolver=command_resolver) for command in required_commands)

    checkouts = (
        check_checkout(
            "commander-gym",
            config.commander_gym,
            required_files=(("scripts/run_argentum_gateway_service.sh", False),),
            runner=git_runner,
        ),
        check_checkout(
            "argentum",
            config.argentum,
            required_files=(
                ("scripts/run-gym-server-service.sh", False),
                ("gradlew", True),
            ),
            runner=git_runner,
        ),
    )
    token = _check_file(
        "gateway-token",
        config.gateway_token_file,
        require_nonempty=True,
    )
    local_inference = _check_local_inference(
        config,
        command_resolver=command_resolver,
        probe_runner=probe_runner,
    )
    storage = check_storage(
        config.storage,
        min_free_bytes=min_free_bytes,
        create=initialize_storage,
    )
    return NodePreflight(
        commands=commands,
        checkouts=checkouts,
        gateway_token=token,
        local_inference=local_inference,
        storage=storage,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed preflight for a portable Commander Gym node."
    )
    parser.add_argument("--config", required=True, help="portable-node JSON config")
    parser.add_argument(
        "--min-free-bytes",
        type=int,
        default=0,
        help="minimum free bytes required on every configured storage route",
    )
    parser.add_argument(
        "--initialize-storage",
        action="store_true",
        help="create missing configured storage route directories before probing them",
    )
    parser.add_argument(
        "--no-systemd",
        action="store_true",
        help="skip systemctl availability check for render/test hosts that will not run the package",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_node_package_config(args.config)
        report = check_node_preflight(
            config,
            min_free_bytes=args.min_free_bytes,
            initialize_storage=args.initialize_storage,
            require_systemd=not args.no_systemd,
        )
    except (NodePackageError, StorageError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
