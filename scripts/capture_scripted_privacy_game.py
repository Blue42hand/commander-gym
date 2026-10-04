"""Capture bounded, offline native GameServer callbacks for a privacy audit.

Uses canonical Binding decks and Knowledge for the roster, but deliberately replaces their
Pilot with a small scripted legal-action selector. No OpenAI client or credential is loaded.
By default the script stops on a structured decision after capturing its exact HTTP body.
The pass-only mode drives a bounded native deck-out to check terminal status and replay capture.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from commander_gym.binding_catalog import load_binding_catalog
from commander_gym.game_server_bindings import GameServerBindingRegistry
from commander_gym.game_server_sidecar import GameServerSidecarConfig, GameServerSidecarServer
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice, PilotContractError
from commander_gym.two_luna_debug import _natural_terminal_game, _terminal_artifact_result
from game_server_acceptance_sidecar import _CaptureHandler


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(url: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("native endpoint returned a non-object")
    return result


def _wait_ready(url: str, process: subprocess.Popen[Any], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("native server exited before readiness")
        try:
            _request(url)
            return
        except HTTPError as exc:
            if exc.code == 404:
                return
            time.sleep(0.25)
        except Exception:
            time.sleep(0.25)
    raise TimeoutError("native server did not become ready")


class ScriptedPrivacyPilot:
    """Select only exact current native offers; stop on unsupported decisions."""
    name = "offline-scripted-privacy-fixture"
    version = "1"

    def __init__(self, *, continue_decisions: bool = False, pass_only: bool = False) -> None:
        self.continue_decisions = continue_decisions
        self.pass_only = pass_only

    def choose(self, observation: Mapping[str, Any]):
        state = observation.get("state")
        if isinstance(state, Mapping) and "mulligan" in state:
            return ArgentumActionChoice(0)
        pending = observation.get("pendingDecision")
        if isinstance(pending, Mapping) and pending.get("kind") == "BottomCards":
            raise PilotContractError("scripted privacy fixture stops at bottom-card decision")
        if isinstance(pending, Mapping) and pending.get("requiresStructuredResponse") is True:
            if not (self.continue_decisions or self.pass_only):
                raise PilotContractError("scripted privacy fixture stops at structured decision")
            spec = pending.get("responseSpec")
            response_type = spec.get("responseType") if isinstance(spec, Mapping) else None
            response = {"type": response_type, "decisionId": pending.get("decisionId")}
            if response_type == "OrderedResponse":
                cards = pending.get("cards")
                if not isinstance(cards, list):
                    raise PilotContractError("ordered decision lacks native cards")
                response["orderedObjects"] = list(cards)
            elif response_type == "CardsSelectedResponse":
                options = pending.get("options")
                minimum = pending.get("minSelections")
                if not isinstance(options, list) or type(minimum) is not int or minimum < 0:
                    raise PilotContractError("selection decision lacks native options/minimum")
                response["selectedCards"] = options[:minimum]
            else:
                raise PilotContractError("unsupported native structured response type")
            return ArgentumDecisionChoice(response, metadata={"provider": "local", "policy": self.name})
        legal = observation.get("legalActions")
        if not isinstance(legal, list) or not legal:
            raise PilotContractError("scripted privacy fixture requires native legal actions")

        def choose(action: Mapping[str, Any], params: Mapping[str, Any] | None = None):
            return ArgentumActionChoice(
                action_id=action["actionId"], params=dict(params or {}),
                metadata={"provider": "local", "policy": self.name},
            )

        for action in legal:
            if action.get("actionType") == "DeclareAttackers":
                attackers = action.get("validAttackers")
                targets = action.get("validAttackTargets")
                if not isinstance(attackers, list) or not isinstance(targets, list):
                    raise PilotContractError("native attack menu lacks attackers/targets")
                if attackers and not targets:
                    raise PilotContractError("native attack menu has no target")
                return choose(action, {"attackers": {a: targets[0] for a in attackers}} if attackers else {})
        for action in legal:
            if action.get("actionType") == "DeclareBlockers":
                if action.get("mandatoryBlockerAssignments"):
                    raise PilotContractError("mandatory blocker choices need a dedicated fixture")
                return choose(action)
        if self.pass_only:
            for action in legal:
                if action.get("actionType") == "PassPriority":
                    return choose(action)
            raise PilotContractError("pass-only fixture found no native priority pass")
        for wanted in ("PlayLand", "CastSpell", "CastSpellMode"):
            for action in legal:
                if action.get("actionType") != wanted or action.get("affordable") is False:
                    continue
                spec = action.get("parameterSpec")
                if not isinstance(spec, Mapping) or not isinstance(spec.get("allowedFields"), Mapping):
                    continue
                if action.get("requiresTargets") or action.get("requiresDamageDistribution") or action.get("hasXCost"):
                    continue
                # The native spec lists optional target/X fields even for spells that
                # require neither. Empty params leave those choices to Argentum.
                return choose(action)
        for action in legal:
            if action.get("actionType") == "PassPriority":
                return choose(action)
        raise PilotContractError("scripted privacy fixture found no supported native offer")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--profile-a", required=True)
    parser.add_argument("--profile-b", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-callbacks", type=int, default=180)
    parser.add_argument("--continue-decisions", action="store_true")
    parser.add_argument("--pass-only", action="store_true",
                        help="offline native deck-out fixture; checks terminal HTTP artifacts, not pilot quality")
    args = parser.parse_args()
    if args.profile_a == args.profile_b or args.timeout <= 0 or args.max_callbacks <= 0:
        parser.error("distinct profiles and positive bounds are required")
    engine = args.engine_dir.resolve(strict=True)
    root = args.instance_root.resolve(strict=True)
    loaded = load_binding_catalog(args.catalog, instance_root=root,
                                  pilot_factory=lambda _pilot, _binding: ScriptedPrivacyPilot(
                                      continue_decisions=args.continue_decisions,
                                      pass_only=args.pass_only))
    registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    selected = []
    profiles = {p.binding_id: p for p in registry.profiles}
    for profile_id in (args.profile_a, args.profile_b):
        selected.append(dict(profiles[profile_id].deck_list))

    os.umask(0o077)
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.stat().st_mode & 0o077:
        raise RuntimeError("output directory must be private")
    capture = output / "http"
    capture.mkdir(mode=0o700, exist_ok=True)
    provenance = output / "policy.jsonl"
    sidecar_port, server_port = _port(), _port()
    token = secrets.token_hex(32)
    os.environ["COMMANDER_GYM_HTTP_CAPTURE_DIR"] = str(capture)
    lock = threading.Lock()
    terminal_artifact = None

    def sink(player_id: str, record):
        with lock:
            with provenance.open("a", encoding="utf-8") as writer:
                writer.write(json.dumps({"playerId": player_id, **asdict(record)}, separators=(",", ":")) + "\n")

    def seat_factory(player_id: str, profile_id: str):
        return registry.create_seat(player_id, profile_id,
                                    provenance_sink=lambda record: sink(player_id, record))

    class _BoundedCaptureHandler(_CaptureHandler):
        request_count = 0
        request_lock = threading.Lock()

        def do_POST(self):  # noqa: N802
            with type(self).request_lock:
                if type(self).request_count >= args.max_callbacks:
                    self._write(429, {"error": "fixture_callback_limit"})
                    return
                type(self).request_count += 1
            super().do_POST()

    sidecar = GameServerSidecarServer(
        ("127.0.0.1", sidecar_port), GameServerSidecarConfig(token=token, port=sidecar_port),
        profiles=registry.profile_payloads(), profile_seat_factory=seat_factory,
    )
    sidecar.RequestHandlerClass = _BoundedCaptureHandler
    sidecar_thread = threading.Thread(target=sidecar.serve_forever, daemon=True)
    sidecar_thread.start()
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env.update({"ARGENTUM_ENGINE_DIR": str(engine), "COMMANDER_GYM_SIDECAR_TOKEN": token,
                "COMMANDER_GYM_SIDECAR_URL": f"http://127.0.0.1:{sidecar_port}",
                "COMMANDER_GYM_GUI_SERVER_PORT": str(server_port),
                "COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS": "10000"})
    log = (output / "server.log").open("x")
    process = None
    try:
        process = subprocess.Popen(
            [str(engine / "scripts/gradle-locked"), "-p", str(Path(__file__).resolve().parents[1] / "jvm-adapter"),
             "runLocalGuiServer"], cwd=engine, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        base = f"http://127.0.0.1:{server_port}"
        _wait_ready(base + "/", process, 240)
        created = _request(base + "/api/dev/ai-tournament", {
            "decks": selected, "gamesPerMatch": 1,
            "controllerSpecs": [
                {"mode": "commander-gym", "profileId": args.profile_a},
                {"mode": "commander-gym", "profileId": args.profile_b},
            ], "rules": "COMMANDER",
        })
        lobby_id = created["lobbyId"]
        deadline = time.monotonic() + args.timeout
        stop_reason = "timeout"
        last_status = {}
        last_count = -1
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            last_status = _request(base + "/api/dev/ai-tournament/" + lobby_id)
            files = list(capture.glob("*.json"))
            if len(files) != last_count:
                last_count = len(files)
                last_change = time.monotonic()
            if len(files) >= args.max_callbacks:
                stop_reason = "max_callbacks"
                break
            if not args.pass_only and not args.continue_decisions:
                bodies = [json.loads(p.read_text()) for p in files]
                if any(
                    "state" in body and isinstance(body.get("pendingDecision"), dict)
                    for body in bodies
                ):
                    stop_reason = "first_structured_decision"
                    break
            if last_status.get("complete") is True:
                stop_reason = "native_complete"
                if args.pass_only:
                    terminal_artifact = _terminal_artifact_result(
                        base, last_status.get("completedGames"), output,
                    )
                break
            if time.monotonic() - last_change > 12:
                stop_reason = "no_callback_progress"
                break
            time.sleep(0.2)
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        log.close()
        sidecar.shutdown()
        sidecar.server_close()
    summary = {"engineCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=engine, text=True).strip(),
               "gymCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip(),
               "profiles": [args.profile_a, args.profile_b], "stopReason": stop_reason,
               "httpCallbacks": len(list(capture.glob("*.json"))), "status": last_status,
               "providerCalls": 0}
    if args.pass_only:
        summary["terminalEvidence"] = _natural_terminal_game(last_status.get("completedGames"))
        summary["terminalArtifact"] = terminal_artifact
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("engineCommit", "gymCommit", "stopReason", "httpCallbacks", "providerCalls")}))
    return 0 if not args.pass_only or (
        summary["terminalEvidence"] is not None
        and isinstance(terminal_artifact, dict)
        and "error" not in terminal_artifact
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
