"""Reproducible, host-configurable service packaging for a Commander Gym node.

This module owns deployment placement and process wiring only. It deliberately does
not define game, pilot, evidence, dataset, or model-selection semantics.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .storage import StorageLayout, StorageError

NODE_PACKAGE_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ACCOUNT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class NodePackageError(ValueError):
    """Raised when portable-node packaging configuration is invalid."""


def _absolute_path(value: object, *, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise NodePackageError(f"{field} must be an absolute filesystem path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise NodePackageError(f"{field} must be absolute")
    return path


def _account(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _ACCOUNT_RE.fullmatch(value):
        raise NodePackageError(f"{field} must be a simple system account name")
    return value


def _revision(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise NodePackageError(f"{field} must be a full lowercase 40-character git SHA")
    return value


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NodePackageError(f"{field} must be a mapping")
    return value


def _http_url(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NodePackageError(f"{field} must be a URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NodePackageError(f"{field} must be an http(s) URL")
    return value.rstrip("/")


def _command_argv(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise NodePackageError(f"{field} must be a non-empty list")
    command: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise NodePackageError(f"{field}[{index}] must be a non-empty string")
        if "\n" in item or "\r" in item:
            raise NodePackageError(f"{field} must not contain newlines")
        command.append(item)
    return tuple(command)


@dataclass(frozen=True)
class CheckoutSpec:
    root: Path
    revision: str

    @classmethod
    def from_mapping(cls, value: object, *, field: str) -> "CheckoutSpec":
        data = _mapping(value, field=field)
        return cls(
            root=_absolute_path(data.get("root"), field=f"{field}.root"),
            revision=_revision(data.get("revision"), field=f"{field}.revision"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"root": str(self.root), "revision": self.revision}


@dataclass(frozen=True)
class LocalInferenceSpec:
    command: tuple[str, ...]
    working_directory: Path | None = None
    environment_file: Path | None = None
    accelerator_probe: tuple[str, ...] | None = None

    @classmethod
    def from_mapping(cls, value: object) -> "LocalInferenceSpec":
        data = _mapping(value, field="local_inference")
        working_directory = data.get("working_directory")
        environment_file = data.get("environment_file")
        accelerator_probe = data.get("accelerator_probe")
        return cls(
            command=_command_argv(data.get("command"), field="local_inference.command"),
            working_directory=(
                _absolute_path(working_directory, field="local_inference.working_directory")
                if working_directory is not None
                else None
            ),
            environment_file=(
                _absolute_path(environment_file, field="local_inference.environment_file")
                if environment_file is not None
                else None
            ),
            accelerator_probe=(
                _command_argv(accelerator_probe, field="local_inference.accelerator_probe")
                if accelerator_probe is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "command": list(self.command),
            "working_directory": (
                str(self.working_directory) if self.working_directory is not None else None
            ),
            "environment_file": (
                str(self.environment_file) if self.environment_file is not None else None
            ),
        }
        if self.accelerator_probe is not None:
            result["accelerator_probe"] = list(self.accelerator_probe)
        return result


@dataclass(frozen=True)
class NodePackageConfig:
    commander_gym: CheckoutSpec
    argentum: CheckoutSpec
    storage: StorageLayout
    gateway_token_file: Path
    service_user: str = "commander"
    service_group: str = "commander"
    gateway_bind: str = "127.0.0.1"
    gateway_port: int = 8082
    gateway_upstream: str = "http://127.0.0.1:8081"
    local_inference: LocalInferenceSpec | None = None
    version: int = NODE_PACKAGE_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NodePackageConfig":
        data = _mapping(value, field="node package config")
        version = data.get("version", NODE_PACKAGE_VERSION)
        if version != NODE_PACKAGE_VERSION:
            raise NodePackageError(
                f"unsupported node package version {version!r}; expected {NODE_PACKAGE_VERSION}"
            )
        try:
            storage = StorageLayout.from_mapping(
                _mapping(data.get("storage"), field="storage")
            )
        except StorageError as exc:
            raise NodePackageError(str(exc)) from exc

        gateway = _mapping(data.get("gateway", {}), field="gateway")
        bind = gateway.get("bind", "127.0.0.1")
        if not isinstance(bind, str) or not bind or "\n" in bind or "\r" in bind:
            raise NodePackageError("gateway.bind must be a non-empty single-line string")
        port = gateway.get("port", 8082)
        if type(port) is not int or not (1 <= port <= 65535):
            raise NodePackageError("gateway.port must be an integer from 1 through 65535")

        local_inference_raw = data.get("local_inference")
        return cls(
            commander_gym=CheckoutSpec.from_mapping(
                data.get("commander_gym"), field="commander_gym"
            ),
            argentum=CheckoutSpec.from_mapping(data.get("argentum"), field="argentum"),
            storage=storage,
            gateway_token_file=_absolute_path(
                data.get("gateway_token_file"), field="gateway_token_file"
            ),
            service_user=_account(data.get("service_user", "commander"), field="service_user"),
            service_group=_account(
                data.get("service_group", "commander"), field="service_group"
            ),
            gateway_bind=bind,
            gateway_port=port,
            gateway_upstream=_http_url(
                gateway.get("upstream", "http://127.0.0.1:8081"),
                field="gateway.upstream",
            ),
            local_inference=(
                LocalInferenceSpec.from_mapping(local_inference_raw)
                if local_inference_raw is not None
                else None
            ),
            version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": self.version,
            "service_user": self.service_user,
            "service_group": self.service_group,
            "commander_gym": self.commander_gym.to_dict(),
            "argentum": self.argentum.to_dict(),
            "gateway_token_file": str(self.gateway_token_file),
            "gateway": {
                "bind": self.gateway_bind,
                "port": self.gateway_port,
                "upstream": self.gateway_upstream,
            },
            "storage": self.storage.to_dict(),
        }
        if self.local_inference is not None:
            result["local_inference"] = self.local_inference.to_dict()
        return result


def _shell_script(lines: Sequence[str]) -> str:
    return "#!/bin/bash\nset -euo pipefail\n\n" + "\n".join(lines) + "\n"


def _systemd_quote(value: Path | str) -> str:
    text = str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _service_unit(
    *,
    description: str,
    user: str,
    group: str,
    runner: Path,
    after: Sequence[str],
    requires: Sequence[str] = (),
    environment_file: Path | None = None,
) -> str:
    lines = [
        "[Unit]",
        f"Description={description}",
        f"After={' '.join(after)}",
        "Wants=network-online.target",
    ]
    if requires:
        lines.append(f"Requires={' '.join(requires)}")
    lines.extend(
        [
            "",
            "[Service]",
            "Type=simple",
            f"User={user}",
            f"Group={group}",
        ]
    )
    if environment_file is not None:
        lines.append(f"EnvironmentFile={_systemd_quote(environment_file)}")
    lines.extend(
        [
            f"ExecStart={_systemd_quote(runner)}",
            "Restart=on-failure",
            "RestartSec=5",
            "TimeoutStopSec=30",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    return "\n".join(lines)


def render_node_package(config: NodePackageConfig, output_dir: str | os.PathLike[str]) -> Path:
    """Render deterministic service scaffolding for one configured node.

    The output contains deployment paths and pinned source revisions by design. Those
    values are installation metadata, never durable Commander Gym artifact identity.
    """

    if not isinstance(config, NodePackageConfig):
        raise NodePackageError("config must be NodePackageConfig")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    systemd_dir = output / "systemd"
    systemd_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output / "node-package.json"
    manifest_path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    storage_path = output / "storage-layout.json"
    storage_path.write_text(
        json.dumps(config.storage.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    argentum_runner = output / "run-argentum-gym.sh"
    argentum_runner.write_text(
        _shell_script(
            [
                "exec "
                + " ".join(
                    shlex.quote(part)
                    for part in (
                        "/bin/bash",
                        str(config.argentum.root / "scripts/run-gym-server-service.sh"),
                        str(config.argentum.root),
                        config.argentum.revision,
                    )
                )
            ]
        ),
        encoding="utf-8",
    )
    argentum_runner.chmod(0o755)

    gateway_runner = output / "run-commander-gym-gateway.sh"
    gateway_runner.write_text(
        _shell_script(
            [
                f"export COMMANDER_GYM_GATEWAY_BIND={shlex.quote(config.gateway_bind)}",
                f"export COMMANDER_GYM_GATEWAY_PORT={config.gateway_port}",
                f"export COMMANDER_GYM_GATEWAY_UPSTREAM={shlex.quote(config.gateway_upstream)}",
                f"export COMMANDER_GYM_STORAGE_CONFIG={shlex.quote(str(storage_path))}",
                "exec "
                + " ".join(
                    shlex.quote(part)
                    for part in (
                        "/bin/bash",
                        str(config.commander_gym.root / "scripts/run_argentum_gateway_service.sh"),
                        str(config.commander_gym.root),
                        config.commander_gym.revision,
                        str(config.gateway_token_file),
                    )
                ),
            ]
        ),
        encoding="utf-8",
    )
    gateway_runner.chmod(0o755)

    (systemd_dir / "argentum-gym.service").write_text(
        _service_unit(
            description="Argentum Gym Server",
            user=config.service_user,
            group=config.service_group,
            runner=argentum_runner,
            after=("network-online.target",),
        ),
        encoding="utf-8",
    )
    (systemd_dir / "commander-gym-gateway.service").write_text(
        _service_unit(
            description="Commander Gym Argentum Gateway",
            user=config.service_user,
            group=config.service_group,
            runner=gateway_runner,
            after=("network-online.target", "argentum-gym.service"),
            requires=("argentum-gym.service",),
        ),
        encoding="utf-8",
    )

    if config.local_inference is not None:
        model_runner = output / "run-local-inference.sh"
        lines: list[str] = []
        if config.local_inference.working_directory is not None:
            lines.append(
                f"cd {shlex.quote(str(config.local_inference.working_directory))}"
            )
        lines.append(
            "exec " + " ".join(shlex.quote(item) for item in config.local_inference.command)
        )
        model_runner.write_text(_shell_script(lines), encoding="utf-8")
        model_runner.chmod(0o755)
        (systemd_dir / "commander-gym-model.service").write_text(
            _service_unit(
                description="Commander Gym Optional Local Inference Service",
                user=config.service_user,
                group=config.service_group,
                runner=model_runner,
                after=("network-online.target",),
                environment_file=config.local_inference.environment_file,
            ),
            encoding="utf-8",
        )

    return output


def load_node_package_config(path: str | os.PathLike[str]) -> NodePackageConfig:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NodePackageError(f"could not load node package config: {exc}") from exc
    return NodePackageConfig.from_mapping(_mapping(payload, field="node package config"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render reproducible service scaffolding for a Commander Gym node."
    )
    parser.add_argument("--config", required=True, help="portable-node JSON config")
    parser.add_argument("--output", required=True, help="directory to render")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_node_package_config(args.config)
        output = render_node_package(config, args.output)
    except NodePackageError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "package_version": config.version,
                "output": str(output),
                "manifest": str(output / "node-package.json"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
