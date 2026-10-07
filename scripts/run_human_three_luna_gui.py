"""Prepare or supervise isolated GUI services; never create a game or play a human seat."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander_gym.experimental_prefix import exclusive_runtime_lock
from commander_gym.human_gui_preflight import PROFILES, SESSION_CATALOG, verify_session_catalog
from commander_gym.cache_probe_session import _durable_mkdir
from commander_gym.human_gui_session import GuiProvenanceWatch, GuiNativeTerminalWatch
from commander_gym.two_luna_debug import _FatalActionWatch, _write_private_json, _finalize_provenance
from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts.probe_v7_runtime_classpath import _hash_entry
from scripts.run_sequential_v7_batch import _head, _tracked_clean, _stop_and_verify_group
from scripts.run_two_luna_binding_game import await_ready, checked_existing_budget, read_key, selected_python


GATES = ("paidSessionApproved", "fourthChoiceRiskDispositionApproved", "humanDeckChosen",
         "nativeGuiMockPassed", "protectedRuntimeVerified")


JAVA_LOGGING_ARGUMENTS = (
    "-Dlogging.level.com.wingedsheep.gameserver.handler.ConnectionHandler=WARN",
    "-Dlogging.level.com.wingedsheep.gameserver.websocket.GameWebSocketHandler=INFO",
    "-Dlogging.level.com.wingedsheep.gameserver.ai.AiWebSocketSession=INFO",
)


def runtime_classpath(path: Path, expected_sha: str, gym: Path, engine: Path) -> list[str]:
    names = json.loads(path.read_bytes())
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
        raise ValueError("ordered runtime classpath must be a nonempty array")
    paths = [Path(n) for n in names]
    if len(set(paths)) != len(paths) or any(not p.is_absolute() or p.is_symlink() for p in paths):
        raise ValueError("runtime entries must be distinct absolute unlinked paths")
    # Verify effective built output, not an adapter jar or source-only pin.
    for prefix in (gym / "jvm-adapter/build/classes/kotlin/main",
                   gym / "jvm-adapter/build/classes/kotlin/test",
                   engine / "game-server/build/classes/kotlin/main",
                   engine / "rules-engine/build/classes/kotlin/main"):
        if prefix not in paths or not prefix.is_dir() or not any(prefix.rglob("*.class")):
            raise ValueError("compiled adapter/server/rules classes missing")
    entries = [[str(p), _hash_entry(p)] for p in paths]
    sha = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if sha != expected_sha:
        raise ValueError("effective runtime classpath changed")
    return names


def preflight(plan: dict, gym: Path) -> dict:
    engine, catalog = Path(plan["engineDir"]), Path(plan["catalogRoot"])
    if any(not p.is_absolute() for p in (engine, catalog, Path(plan["budgetLedger"]), Path(plan["runtimeLock"]))):
        raise ValueError("absolute runtime paths required")
    for directory, expected in ((gym, plan["gymHead"]), (engine, plan["engineHead"])):
        if _head(directory) != expected or not _tracked_clean(directory):
            raise ValueError("source head or tracked cleanliness changed")
    if not (gym / "scripts/human_gui_preview.config.mjs").is_file():
        raise ValueError("reviewed native preview config missing")
    verify_session_catalog(catalog, plan["catalogManifestSha256"])
    classpath = runtime_classpath(Path(plan["classpathFile"]), plan["runtimeClasspathSha256"], gym, engine)
    frontend = engine / "web-client"
    if _hash_entry(frontend / "dist") != plan["frontendSha256"] or not (frontend / "dist/index.html").is_file():
        raise ValueError("matching built frontend missing or changed")
    if not os.access(frontend / "node_modules/.bin/vite", os.X_OK):
        raise ValueError("installed frontend preview executable missing")
    cap, maximum = plan["cumulativeCapUsd"], plan["cumulativeMaxRequests"]
    snapshot = checked_existing_budget(Path(plan["budgetLedger"]), cap, cap, maximum,
                                      plan["startRequests"], plan["startUnsettled"], plan["startEstimatedUsd"])
    dollars, attempts, wall = plan["incrementalCapUsd"], plan["attemptLimit"], plan["wallSeconds"]
    if (type(attempts) is not int or attempts <= 0 or not math.isfinite(dollars) or dollars <= 0
        or not math.isfinite(wall) or wall <= 0
        or snapshot["estimatedUsd"] + dollars > cap or snapshot["requests"] + attempts > maximum):
        raise ValueError("explicit session bounds exceed existing cumulative allowance")
    ports = [plan[k] for k in ("serverPort", "sidecarPort", "frontendPort")]
    if len(set(ports)) != 3 or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ValueError("three distinct loopback ports required")
    return {"classpath": classpath, "snapshot": snapshot,
            "sessionCapUsd": snapshot["estimatedUsd"] + dollars,
            "sessionMaxRequests": snapshot["requests"] + attempts}


def service_environments(plan: dict, receipt: dict, run: Path, key: str, deadline: float):
    # Allowlist inherited execution metadata. No ambient provider/prefix flags or
    # credential/token may leak into the frontend or alter the canonical Pilot.
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "JAVA_HOME", "TMPDIR", "LANG", "LC_ALL")}
    token = secrets.token_hex(32)
    backend = {**base, "COMMANDER_GYM_GUI_SERVER_PORT": str(plan["serverPort"]),
               "COMMANDER_GYM_SIDECAR_URL": f'http://127.0.0.1:{plan["sidecarPort"]}',
               "COMMANDER_GYM_SIDECAR_TOKEN": token, "COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS": "120000"}
    backend["COMMANDER_GYM_GUI_TERMINAL_RECEIPT"] = str(run / "native-terminal.json")
    sidecar = {**base, "OPENAI_API_KEY": key, "COMMANDER_GYM_SIDECAR_HOST": "127.0.0.1",
               "COMMANDER_GYM_SIDECAR_PORT": str(plan["sidecarPort"]), "COMMANDER_GYM_SIDECAR_TOKEN": token,
               "COMMANDER_GYM_OPENAI_MODEL": "gpt-6-luna", "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": "2",
               "COMMANDER_GYM_OPENAI_TIMEOUT": "120", "COMMANDER_GYM_CACHE_FRIENDLY_HISTORY": "false",
               "COMMANDER_GYM_BINDING_CATALOG": str(Path(plan["catalogRoot"]) / SESSION_CATALOG),
               "COMMANDER_GYM_INSTANCE_ROOT": plan["catalogRoot"],
               "COMMANDER_GYM_SIDECAR_PROVENANCE": str(run / "policy.jsonl"),
               "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": plan["budgetLedger"],
               "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": str(plan["cumulativeCapUsd"]),
               "COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD": str(plan["cumulativeCapUsd"]),
               "COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS": str(plan["cumulativeMaxRequests"]),
               "COMMANDER_GYM_OPENAI_SESSION_CAP_USD": str(receipt["sessionCapUsd"]),
               "COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS": str(receipt["sessionMaxRequests"]),
               "COMMANDER_GYM_HUMAN_GUI_DEADLINE_UNIX": str(deadline),
               "COMMANDER_GYM_HUMAN_GUI_STOP_RECEIPT": str(run / "stop.json")}
    frontend = {**base, "GAME_SERVER_URL": f'http://127.0.0.1:{plan["serverPort"]}'}
    return sidecar, backend, frontend


def verify_advertised_profiles(url: str, token: str):
    with urlopen(Request(url, headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
        body = json.load(response)
    if tuple(p.get("id") for p in body.get("profiles", [])) != PROFILES:
        raise ValueError("runtime must advertise exactly the three approved AI profiles")


def final_receipt(plan: dict, receipt: dict, run: Path, reason: str, error_type: str | None,
                  terminal: dict | None = None):
    budget = OpenAIRunBudget(Path(plan["budgetLedger"]), plan["cumulativeCapUsd"],
                             authorized_max_usd=plan["cumulativeCapUsd"], max_requests=plan["cumulativeMaxRequests"])
    after = budget.snapshot()
    before = receipt["snapshot"]
    _, recorded, reconciled, error = _finalize_provenance(run / "policy.jsonl", budget,
                                                        before["requests"], timeout_seconds=0)
    new_unsettled = after["unsettledRequests"] - before["unsettledRequests"]
    result = {"reason": reason, "errorType": error_type, "qualification": False,
              "naturalTerminalVerified": reason == "native_game_over" and terminal is not None,
              "nativeTerminalNotification": terminal,
              "automaticReplacement": False, "cleanupVerified": True,
              "startLedger": before, "afterLedger": after,
              "requestsUsed": after["requests"] - before["requests"],
              "estimatedUsdUsed": after["estimatedUsd"] - before["estimatedUsd"],
              "newUnsettledRequests": new_unsettled, "providerAttemptsRecorded": recorded,
              "provenanceReconciled": reconciled, "provenanceError": error,
              "sessionCapUsd": receipt["sessionCapUsd"], "sessionMaxRequests": receipt["sessionMaxRequests"]}
    _write_private_json(run / "result.json", result)
    return 0 if reason in ("operator_stop", "native_game_over") and reconciled and new_unsettled <= 0 else 1


def supervise(plan: dict, gym: Path, run: Path, key_file: Path, python: Path):
    """Caller supplies explicit authorization; lock covers preflight through cleanup."""
    if any(plan.get(gate) is not True for gate in GATES):
        raise ValueError("paid launch holds: missing explicit approval/readiness gates")
    processes, logs = [], []
    reason, error_type, status = "startup_failure", None, 1
    terminal = None
    provenance = fatal = None
    with exclusive_runtime_lock(Path(plan["runtimeLock"])):
        receipt = preflight(plan, gym)
        if run.exists():
            raise ValueError("fresh session directory required; never resume or replay a game")
        _durable_mkdir(run)
        if run.stat().st_mode & 0o777 != 0o700:
            raise ValueError("private mode0700 session directory required")
        _write_private_json(run / "manifest.json", {"plan": plan, "preflight": receipt, "qualification": False})
        deadline = time.time() + plan["wallSeconds"]
        old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        def interrupted(_signal, _frame):
            raise InterruptedError("operator stopped supervised GUI")
        for s in old:
            signal.signal(s, interrupted)
        try:
            sidecar, backend, frontend = service_environments(plan, receipt, run, read_key(key_file), deadline)
            for port in (plan["serverPort"], plan["sidecarPort"], plan["frontendPort"]):
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", port))
            commands = [([str(python), "-m", "commander_gym.game_server_binding_openai_sidecar"], gym, sidecar),
                        (["java", *JAVA_LOGGING_ARGUMENTS, "-cp", os.pathsep.join(receipt["classpath"]), "org.commandergym.argentum.LocalGuiGameServerKt"], gym, backend),
                        ([str(Path(plan["engineDir"]) / "web-client/node_modules/.bin/vite"), "preview", "--configLoader", "native", "--config", str(gym / "scripts/human_gui_preview.config.mjs"), "--host", "127.0.0.1", "--port", str(plan["frontendPort"]), "--strictPort"], Path(plan["engineDir"]) / "web-client", frontend)]
            urls = [f'http://127.0.0.1:{plan["sidecarPort"]}/v1/controller-profiles', f'http://127.0.0.1:{plan["serverPort"]}/', f'http://127.0.0.1:{plan["frontendPort"]}/']
            for index, (command, cwd, env) in enumerate(commands):
                path = run / ("sidecar.log", "server.log", "frontend.log")[index]
                log = os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w")
                logs.append(log)
                process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                processes.append(process)
                await_ready(urls[index], process, min(30, max(.001, deadline - time.time())), sidecar["COMMANDER_GYM_SIDECAR_TOKEN"] if index == 0 else None)
            verify_advertised_profiles(urls[0], sidecar["COMMANDER_GYM_SIDECAR_TOKEN"])
            provenance = GuiProvenanceWatch(run / "policy.jsonl")
            native = GuiNativeTerminalWatch(run / "native-terminal.json")
            fatal = _FatalActionWatch(run / "server.log")
            print(f"GUI ready on loopback port {plan['frontendPort']}; select one human and the three exact profiles before starting one game.", flush=True)
            reason = "wall_limit"
            while time.time() < deadline:
                provenance.scan()
                fatal.scan([])
                native.scan()
                if fatal.failures or fatal.policy_failure or provenance.failed:
                    reason = "callback_or_native_failure"
                    break
                if native.receipt is None and (provenance.native_game_over or (run / "stop.json").exists()):
                    reason = "native_or_session_stop_receipt"
                    break
                snapshot = checked_existing_budget(Path(plan["budgetLedger"]), plan["cumulativeCapUsd"], plan["cumulativeCapUsd"], plan["cumulativeMaxRequests"], None, None)
                # Native cancellation can race an already dispatched sidecar request.
                # Keep services alive until it settles, bounded by the original deadline.
                if native.receipt is not None and snapshot["unsettledRequests"] <= receipt["snapshot"]["unsettledRequests"]:
                    budget = OpenAIRunBudget(Path(plan["budgetLedger"]), plan["cumulativeCapUsd"],
                                             authorized_max_usd=plan["cumulativeCapUsd"],
                                             max_requests=plan["cumulativeMaxRequests"])
                    _, _, drained, _ = _finalize_provenance(run / "policy.jsonl", budget,
                                                            receipt["snapshot"]["requests"], timeout_seconds=0)
                    if drained:
                        # Settlement precedes policy serialization; a late callback
                        # can also report an error after the watches' first scan.
                        provenance.scan()
                        fatal.scan([])
                        if fatal.failures or fatal.policy_failure or provenance.failed:
                            reason = "callback_or_native_failure"
                        else:
                            terminal = native.receipt
                            reason = "native_game_over"
                        break
                if any(p.poll() is not None for p in processes):
                    reason = "service_exited"
                    break
                if (snapshot["requests"] >= receipt["sessionMaxRequests"] or snapshot["estimatedUsd"] >= receipt["sessionCapUsd"]):
                    # Let the last already-reserved call settle; no further reserve
                    # can pass the atomic sidecar ceiling. Wall bound still applies.
                    if snapshot["unsettledRequests"] <= receipt["snapshot"]["unsettledRequests"]:
                        reason = "session_budget_limit"
                        break
                time.sleep(.5)
        except InterruptedError:
            reason = "operator_stop"
        except Exception as error:
            reason, error_type = "supervisor_error", type(error).__name__
        finally:
            for s in old:
                signal.signal(s, signal.SIG_IGN)
            try:
                for process in reversed(processes):
                    _stop_and_verify_group(process)
                for log in logs:
                    log.close()
                if reason == "native_game_over":
                    provenance.scan()
                    fatal.scan([])
                    if fatal.failures or fatal.policy_failure or provenance.failed:
                        reason, terminal = "callback_or_native_failure", None
                status = final_receipt(plan, receipt, run, reason, error_type, terminal)
            finally:
                for s, handler in old.items():
                    signal.signal(s, handler)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--launch", action="store_true", help="explicit operator launch after separate paid approval")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args()
    gym = Path(__file__).resolve().parent.parent
    plan = json.loads(args.plan.read_bytes())
    if not args.launch:
        print(json.dumps({"prepared": True, "qualification": False, "preflight": preflight(plan, gym)}, sort_keys=True))
        return 0
    if not args.run_dir or not args.run_dir.is_absolute() or not args.api_key_file:
        parser.error("launch requires an absolute fresh run directory and existing key file")
    return supervise(plan, gym, args.run_dir, args.api_key_file, selected_python(gym, args.python))


if __name__ == "__main__":
    raise SystemExit(main())
