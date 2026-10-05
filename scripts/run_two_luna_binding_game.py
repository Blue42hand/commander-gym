"""Start isolated Argentum/Binding services and run one capped headless game."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.qualified_v7_preflight import stage_qualified_v7_catalog


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_key(path):
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("OPENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise RuntimeError("API key file has no OPENAI_API_KEY entry")


def checked_existing_budget(ledger, cap, authorized_max, max_requests,
                            expected_requests, expected_unsettled):
    if not ledger.is_file() or not ledger.read_bytes():
        raise RuntimeError("existing nonempty cumulative budget ledger required")
    snapshot = OpenAIRunBudget(
        ledger, cap, authorized_max_usd=authorized_max, max_requests=max_requests,
    ).snapshot()
    if expected_requests is not None and snapshot["requests"] != expected_requests:
        raise RuntimeError("cumulative ledger request count changed before game launch")
    if expected_unsettled is not None and snapshot["unsettledRequests"] != expected_unsettled:
        raise RuntimeError("cumulative ledger unsettled reservations changed before game launch")
    return snapshot


def selected_python(gym: Path, override: Path | None) -> Path:
    # A venv interpreter is usually a symlink. Resolving it selects the system
    # binary outside the venv and loses its installed OpenAI SDK.
    python = override.absolute() if override else gym / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("selected Python executable is unavailable")
    return python


def await_ready(url, process, seconds, token=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local process exited during startup; inspect run log")
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            with urlopen(Request(url, headers=headers), timeout=0.5):
                return
        except HTTPError as error:
            if error.code == 404 and token is None:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("local service did not become ready; inspect run log")


def main():
    parser = argparse.ArgumentParser()
    for name in ("engine-dir", "instance-root", "catalog", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--python", type=Path,
                        help="Python executable with the existing OpenAI SDK; defaults to worktree .venv")
    parser.add_argument("--budget-ledger", type=Path,
                        help="existing absolute cumulative ledger; defaults to output-dir ledger")
    parser.add_argument("--budget-cap", type=float, default=5.0)
    parser.add_argument("--budget-authorized-max", type=float, default=5.0)
    parser.add_argument("--budget-max-requests", type=int)
    parser.add_argument("--expected-ledger-requests", type=int)
    parser.add_argument("--expected-unsettled-requests", type=int)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--profile-a", required=True)
    parser.add_argument("--profile-b", required=True)
    parser.add_argument("--dry-run", action="store_true", help="reject model dispatch before spend")
    parser.add_argument("--qualified-v7-comparison", action="store_true",
                        help="pin and stage the reviewed v7 Binding/Pilot catalog closure")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--stall-seconds", type=float, default=600)
    args = parser.parse_args()
    if args.timeout <= 0 or args.stall_seconds <= 0 or args.max_attempts <= 0:
        parser.error("timeout and stall-seconds must be positive")
    if args.budget_ledger is not None and not args.budget_ledger.is_absolute():
        parser.error("existing cumulative budget ledger must be absolute")
    if args.budget_cap > 5 and args.budget_max_requests is None:
        parser.error("an elevated cumulative cap requires an absolute request limit")
    if not args.dry_run and args.api_key_file is None:
        parser.error("--api-key-file is required for a paid game")
    if not args.dry_run and not args.qualified_v7_comparison:
        parser.error("paid two-Luna qualification requires --qualified-v7-comparison")
    gym = Path(__file__).resolve().parent.parent
    python = selected_python(gym, args.python)
    engine = args.engine_dir.resolve()
    out = args.output_dir.resolve()
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(out.stat().st_mode) & 0o077:
        raise RuntimeError("output directory must be private to the current user")
    ledger = args.budget_ledger.resolve() if args.budget_ledger else out / "openai-budget.json"
    if args.budget_ledger is not None and not args.dry_run:
        checked_existing_budget(
            ledger, args.budget_cap, args.budget_authorized_max,
            args.budget_max_requests, args.expected_ledger_requests,
            args.expected_unsettled_requests,
        )
    run_dir = out / f"game-{int(time.time())}-{secrets.token_hex(4)}"
    run_dir.mkdir(mode=0o700)
    catalog_root = args.instance_root.resolve()
    catalog_path = args.catalog.resolve()
    if args.qualified_v7_comparison:
        catalog_root, catalog_path, qualification = stage_qualified_v7_catalog(
            catalog_root, catalog_path, run_dir, args.profile_a, args.profile_b,
            args.max_attempts,
        )
        print("TWO_LUNA_QUALIFICATION_PREFLIGHT " + json.dumps(qualification, sort_keys=True))
    sidecar_port, server_port = free_port(), free_port()
    env = dict(os.environ)
    env.update({
        "OPENAI_API_KEY": "sk-test-no-dispatch" if args.dry_run else read_key(args.api_key_file),
        "COMMANDER_GYM_SIDECAR_TOKEN": secrets.token_hex(32),
        "COMMANDER_GYM_SIDECAR_HOST": "127.0.0.1",
        "COMMANDER_GYM_SIDECAR_PORT": str(sidecar_port),
        "COMMANDER_GYM_SIDECAR_URL": f"http://127.0.0.1:{sidecar_port}",
        "COMMANDER_GYM_GUI_SERVER_PORT": str(server_port),
        "COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS": "120000",
        "COMMANDER_GYM_OPENAI_MODEL": "gpt-6-luna",
        "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": str(args.max_attempts),
        "COMMANDER_GYM_OPENAI_TIMEOUT": "120",
        "COMMANDER_GYM_SIDECAR_PROVENANCE": str(run_dir / "policy.jsonl"),
        "COMMANDER_GYM_BINDING_CATALOG": str(catalog_path),
        "COMMANDER_GYM_INSTANCE_ROOT": str(catalog_root),
        "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": str(ledger),
        "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": "0.000000001" if args.dry_run else str(args.budget_cap),
        "COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD": str(args.budget_authorized_max),
        "ARGENTUM_ENGINE_DIR": str(engine),
    })
    if args.budget_max_requests is not None:
        env["COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS"] = str(args.budget_max_requests)
    else:
        env.pop("COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS", None)
    processes, logs = [], []
    try:
        sidecar_log = (run_dir / "sidecar.log").open("x")
        logs.append(sidecar_log)
        sidecar = subprocess.Popen([str(python), "-m",
            "commander_gym.game_server_binding_openai_sidecar"], cwd=gym, env=env,
            stdout=sidecar_log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(sidecar)
        await_ready(
            f"http://127.0.0.1:{sidecar_port}/v1/controller-profiles",
            sidecar, 30, env["COMMANDER_GYM_SIDECAR_TOKEN"],
        )
        server_log = (run_dir / "server.log").open("x")
        logs.append(server_log)
        server = subprocess.Popen([str(engine / "scripts/gradle-locked"), "-p",
            str(gym / "jvm-adapter"), "runLocalGuiServer"], cwd=engine, env=env,
            stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(server)
        await_ready(f"http://127.0.0.1:{server_port}/", server, 240)
        runner_env = dict(env)
        runner_env.pop("OPENAI_API_KEY", None)
        result = subprocess.run([str(python), "-m",
            "commander_gym.two_luna_debug", "--server-url", f"http://127.0.0.1:{server_port}",
            "--sidecar-url", f"http://127.0.0.1:{sidecar_port}",
            "--profile-a", args.profile_a, "--profile-b", args.profile_b,
            "--budget-ledger", str(ledger),
            "--budget-cap", "0.000000001" if args.dry_run else str(args.budget_cap),
            "--budget-authorized-max", str(args.budget_authorized_max),
            *(["--budget-max-requests", str(args.budget_max_requests)] if args.budget_max_requests is not None else []),
            "--provenance", str(run_dir / "policy.jsonl"),
            "--server-log", str(run_dir / "server.log"),
            "--terminal-evidence-dir", str(run_dir),
            "--timeout", str(min(args.timeout, 30) if args.dry_run else args.timeout),
            "--stall-seconds", str(min(args.stall_seconds, 30) if args.dry_run else args.stall_seconds)],
            cwd=gym, env=runner_env, check=False)
        print(f"RUN_ARTIFACTS={run_dir}", flush=True)
        print(f"CUMULATIVE_BUDGET_LEDGER={ledger}", flush=True)
        return result.returncode
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        for log in logs:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
