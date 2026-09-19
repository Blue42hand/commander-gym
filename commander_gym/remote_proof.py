"""Direct #9 remote-orchestration proof over the durable Commander Gym contract.

This module intentionally bypasses the temporary GitHub Actions relay.  It can be
run against either the loopback authenticated gateway or an HTTPS tunnel endpoint
using :class:`ArgentumGymClient`, and exercises the exact first-success lifecycle:

    health/compatibility -> create -> observe -> step/decision -> observe -> dispose

The bearer token is read from the environment and is never included in the emitted
proof record.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .argentum_client import ArgentumGymClient
from .orchestration import ArgentumOrchestrator


class DirectOrchestrationProofError(RuntimeError):
    """Raised when a direct orchestration proof cannot be completed safely."""


def _required_env_id(created: Mapping[str, Any]) -> str:
    env_id = created.get("envId")
    if not isinstance(env_id, str) or not env_id:
        raise DirectOrchestrationProofError("create response did not contain a valid envId")
    return env_id


def _state_digest(observation: Mapping[str, Any]) -> str | None:
    digest = observation.get("stateDigest")
    return digest if isinstance(digest, str) and digest else None


def _choose_action(observation: Mapping[str, Any], requested_action_id: int | None) -> int:
    actions = observation.get("legalActions")
    if not isinstance(actions, list):
        raise DirectOrchestrationProofError("observation did not contain a legalActions array")

    legal_ids: list[int] = []
    for action in actions:
        if not isinstance(action, Mapping):
            raise DirectOrchestrationProofError("legalActions contained a non-object entry")
        action_id = action.get("actionId")
        if type(action_id) is not int:
            raise DirectOrchestrationProofError("legal action did not contain an integer actionId")
        legal_ids.append(action_id)

    if requested_action_id is not None:
        if requested_action_id not in legal_ids:
            raise DirectOrchestrationProofError(
                f"requested action {requested_action_id} is not legal in the observed state"
            )
        return requested_action_id

    if len(legal_ids) != 1:
        raise DirectOrchestrationProofError(
            "automatic proof execution requires exactly one legal action; "
            "pass --action-id for an explicitly observed legal action or --decision for a "
            "structured decision"
        )
    return legal_ids[0]


def run_direct_proof(
    orchestrator: ArgentumOrchestrator,
    config: Mapping[str, Any],
    *,
    action_id: int | None = None,
    action_params: Mapping[str, Any] | None = None,
    decision_response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the #9 first-success lifecycle through one orchestration backend.

    Exactly one mutation path is used after the opening observation.  A structured
    decision response takes precedence and cannot be combined with ``action_id`` or
    ``action_params``.  Otherwise an explicitly requested legal action is used, or
    the sole legal action is selected automatically.  The environment is disposed
    in a ``finally`` block and disposal is verified authoritatively.
    """

    if decision_response is not None and (action_id is not None or action_params):
        raise DirectOrchestrationProofError(
            "structured decision proof cannot be combined with action-id/action params"
        )

    inspection = orchestrator.inspect()
    before_envs = orchestrator.list_environments()
    env_id: str | None = None
    opening: Mapping[str, Any] | None = None
    resulting: Mapping[str, Any] | None = None
    mutation: dict[str, Any] | None = None
    disposed = False

    try:
        created = orchestrator.create_environment(config)
        env_id = _required_env_id(created)
        opening = orchestrator.observe_environment(env_id)

        if decision_response is not None:
            orchestrator.submit_decision(env_id, decision_response)
            mutation = {"kind": "decision"}
        else:
            selected_action_id = _choose_action(opening, action_id)
            orchestrator.step_environment(env_id, selected_action_id, params=action_params)
            mutation = {"kind": "action", "actionId": selected_action_id}

        resulting = orchestrator.observe_environment(env_id)
    finally:
        if env_id is not None:
            orchestrator.dispose_environment(env_id)
            disposed = env_id not in orchestrator.list_environments()

    if env_id is None or opening is None or resulting is None or mutation is None:
        raise DirectOrchestrationProofError("proof did not reach the complete lifecycle")
    if not disposed:
        raise DirectOrchestrationProofError(f"environment {env_id} still exists after dispose")

    final_inspection = orchestrator.inspect()
    if final_inspection.identity != inspection.identity:
        raise DirectOrchestrationProofError("Argentum identity changed during direct proof")

    return {
        "proof": "commander-gym-direct-orchestration-v1",
        "service": inspection.identity.service,
        "schemaHash": inspection.identity.schema_hash,
        "buildRevision": inspection.identity.build_revision,
        "health": inspection.health_status,
        "envId": env_id,
        "preexistingEnvironmentCount": len(before_envs),
        "openingStateDigest": _state_digest(opening),
        "mutation": mutation,
        "resultingStateDigest": _state_digest(resulting),
        "disposed": disposed,
        "serviceHealthyAfterDispose": final_inspection.health_status == "ok",
    }


def _load_object(path: str, label: str) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise DirectOrchestrationProofError(f"{label} JSON must be an object")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="EnvConfig JSON file")
    p.add_argument("--base-url", default=os.environ.get("COMMANDER_GYM_ARGENTUM_URL"))
    p.add_argument("--token-env", default="COMMANDER_GYM_ARGENTUM_TOKEN")
    p.add_argument("--action-id", type=int)
    p.add_argument("--params", help="optional ActionParams JSON file")
    p.add_argument("--decision", help="optional DecisionResponse JSON file")
    p.add_argument("--expected-schema-hash")
    p.add_argument("--expected-build-revision")
    p.add_argument("--timeout", type=float, default=10.0)
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.base_url:
        raise SystemExit("--base-url or COMMANDER_GYM_ARGENTUM_URL is required")

    token = os.environ.get(args.token_env)
    client = ArgentumGymClient(args.base_url, bearer_token=token, timeout=args.timeout)
    orchestrator = ArgentumOrchestrator(
        client,
        expected_schema_hash=args.expected_schema_hash,
        expected_build_revision=args.expected_build_revision,
    )

    config = _load_object(args.config, "EnvConfig")
    params = _load_object(args.params, "ActionParams") if args.params else None
    decision = _load_object(args.decision, "DecisionResponse") if args.decision else None

    try:
        proof = run_direct_proof(
            orchestrator,
            config,
            action_id=args.action_id,
            action_params=params,
            decision_response=decision,
        )
    except (DirectOrchestrationProofError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps({"ok": True, **proof}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
