"""Start isolated Argentum/Binding services and run one capped headless game."""
from __future__ import annotations
import argparse
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
    parser.add_argument("--profile-a", required=True)
    parser.add_argument("--profile-b", required=True)
    parser.add_argument("--dry-run", action="store_true", help="reject model dispatch before spend")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    if not args.dry_run and args.api_key_file is None:
        parser.error("--api-key-file is required for a paid game")
    gym = Path(__file__).resolve().parent.parent
    engine = args.engine_dir.resolve()
    out = args.output_dir.resolve()
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(out.stat().st_mode) & 0o077:
        raise RuntimeError("output directory must be private to the current user")
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
        "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": "2",
        "COMMANDER_GYM_OPENAI_TIMEOUT": "120",
        "COMMANDER_GYM_SIDECAR_PROVENANCE": str(out / "policy.jsonl"),
        "COMMANDER_GYM_BINDING_CATALOG": str(args.catalog.resolve()),
        "COMMANDER_GYM_INSTANCE_ROOT": str(args.instance_root.resolve()),
        "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": str(out / "openai-budget.json"),
        "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": "0.000000001" if args.dry_run else "5",
        "ARGENTUM_ENGINE_DIR": str(engine),
    })
    processes, logs = [], []
    try:
        sidecar_log = (out / "sidecar.log").open("a")
        logs.append(sidecar_log)
        sidecar = subprocess.Popen([str(gym / ".venv/bin/python"), "-m",
            "commander_gym.game_server_binding_openai_sidecar"], cwd=gym, env=env,
            stdout=sidecar_log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(sidecar)
        await_ready(
            f"http://127.0.0.1:{sidecar_port}/v1/controller-profiles",
            sidecar, 30, env["COMMANDER_GYM_SIDECAR_TOKEN"],
        )
        server_log = (out / "server.log").open("a")
        logs.append(server_log)
        server = subprocess.Popen([str(engine / "scripts/gradle-locked"), "-p",
            str(gym / "jvm-adapter"), "runLocalGuiServer"], cwd=engine, env=env,
            stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(server)
        await_ready(f"http://127.0.0.1:{server_port}/", server, 240)
        runner_env = dict(env)
        runner_env.pop("OPENAI_API_KEY", None)
        result = subprocess.run([str(gym / ".venv/bin/python"), "-m",
            "commander_gym.two_luna_debug", "--server-url", f"http://127.0.0.1:{server_port}",
            "--sidecar-url", f"http://127.0.0.1:{sidecar_port}",
            "--profile-a", args.profile_a, "--profile-b", args.profile_b,
            "--budget-ledger", str(out / "openai-budget.json"),
            "--budget-cap", "0.000000001" if args.dry_run else "5",
            "--provenance", str(out / "policy.jsonl"),
            "--server-log", str(out / "server.log"),
            "--timeout", str(min(args.timeout, 30) if args.dry_run else args.timeout)],
            cwd=gym, env=runner_env, check=False)
        print(f"RUN_ARTIFACTS={out}", flush=True)
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
