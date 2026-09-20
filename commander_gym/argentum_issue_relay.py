"""QUARANTINED legacy GitHub relay for the original Argentum remote proof.

The direct authenticated Commander Gym gateway completed its live non-GitHub
orchestration proof on 2026-09-20. This module remains only as transitional
evidence and an explicit manual fallback while the old relay is retired.

New code and normal operations must use ArgentumGymClient / ArgentumOrchestrator
directly. Do not add new relay operations, lifecycle assumptions, or automatic
workflow triggers here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from commander_gym.argentum_client import ArgentumGymClient


class ArgentumRelayCommandError(ValueError):
    """Raised when a relay command is outside the narrow proof contract."""


@dataclass(frozen=True)
class ArgentumRelay:
    client: ArgentumGymClient

    def execute(self, command: Mapping[str, Any]) -> Any:
        op = command.get("op")
        if not isinstance(op, str):
            raise ArgentumRelayCommandError("command.op must be a string")

        if op == "status":
            self._exact_keys(command, {"op"})
            return self.client.status()

        if op == "health":
            self._exact_keys(command, {"op"})
            return self.client.health()

        if op == "schema":
            self._exact_keys(command, {"op"})
            return self.client.schema_hash()

        if op == "list":
            self._exact_keys(command, {"op"})
            return list(self.client.list_envs())

        if op == "create":
            self._exact_keys(command, {"op", "config"})
            config = command.get("config")
            if not isinstance(config, Mapping):
                raise ArgentumRelayCommandError("create.config must be an object")
            return self.client.create_env(config)

        if op == "observe":
            self._keys_subset(command, {"op", "envId", "revealAll"})
            env_id = self._env_id(command)
            reveal_all = command.get("revealAll")
            if reveal_all is not None and type(reveal_all) is not bool:
                raise ArgentumRelayCommandError("observe.revealAll must be boolean")
            return self.client.observe_env(env_id, reveal_all=reveal_all)

        if op == "step":
            self._keys_subset(command, {"op", "envId", "actionId", "params"})
            env_id = self._env_id(command)
            action_id = command.get("actionId")
            if type(action_id) is not int:
                raise ArgentumRelayCommandError("step.actionId must be an integer")
            params = command.get("params")
            if params is not None and not isinstance(params, Mapping):
                raise ArgentumRelayCommandError("step.params must be an object")
            return self.client.step_env(env_id, action_id, params=params)

        if op == "dispose":
            self._exact_keys(command, {"op", "envIds"})
            env_ids = command.get("envIds")
            if (
                not isinstance(env_ids, Sequence)
                or isinstance(env_ids, (str, bytes))
                or not env_ids
                or any(not isinstance(env_id, str) or not env_id for env_id in env_ids)
            ):
                raise ArgentumRelayCommandError("dispose.envIds must be a non-empty string array")
            self.client.dispose_envs(env_ids)
            return {"disposed": list(env_ids)}

        raise ArgentumRelayCommandError(f"unsupported operation: {op}")

    @staticmethod
    def _exact_keys(command: Mapping[str, Any], expected: set[str]) -> None:
        keys = set(command)
        if keys != expected:
            raise ArgentumRelayCommandError(
                f"command keys must be exactly {sorted(expected)}; got {sorted(keys)}"
            )

    @staticmethod
    def _keys_subset(command: Mapping[str, Any], allowed: set[str]) -> None:
        extra = set(command) - allowed
        if extra:
            raise ArgentumRelayCommandError(f"unsupported command keys: {sorted(extra)}")

    @staticmethod
    def _env_id(command: Mapping[str, Any]) -> str:
        env_id = command.get("envId")
        if not isinstance(env_id, str) or not env_id:
            raise ArgentumRelayCommandError("envId must be a non-empty string")
        return env_id


def parse_comment(body: str) -> Mapping[str, Any]:
    prefix = "/argentum "
    if not body.startswith(prefix):
        raise ArgentumRelayCommandError("comment must start with '/argentum '")
    raw = body[len(prefix):].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArgentumRelayCommandError(f"command is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, Mapping):
        raise ArgentumRelayCommandError("command JSON must be an object")
    return value


def render_result(value: Any) -> str:
    return (
        "<!-- commander-gym-argentum-relay -->\n"
        "Argentum relay result:\n\n"
        "~~~json\n"
        + json.dumps(value, indent=2, sort_keys=True)
        + "\n~~~\n"
    )
