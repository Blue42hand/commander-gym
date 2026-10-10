"""Inspect a matched release without starting services, reading keys, or calling a model."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

IMPORTS = "META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports"
CONFIGURATION = "org.commandergym.argentum.CommanderGymAutoConfiguration"
QA_GATE = "BOOT-INF/classes/com/wingedsheep/gameserver/ai/QaCallbackAdmission.class"


def manifest(archive: zipfile.ZipFile) -> dict[str, str]:
    text = archive.read("META-INF/MANIFEST.MF").decode("utf-8").replace("\r\n ", "")
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


def qualify(server: Path, adapter: Path, native_sha: str, gym_sha: str, *, require_qa_callback_gate: bool = False) -> dict:
    if any(re.fullmatch(r"[0-9a-f]{40}", pin) is None for pin in (native_sha, gym_sha)):
        raise ValueError("exact native and Gym commit pins required")
    adapter_bytes = adapter.read_bytes()
    with zipfile.ZipFile(io.BytesIO(adapter_bytes)) as archive:
        metadata = manifest(archive)
        if metadata.get("Argentum-Revision") != native_sha or metadata.get("Commander-Gym-Revision") != gym_sha:
            raise ValueError("adapter source pins do not match the release")
        if CONFIGURATION not in archive.read(IMPORTS).decode().splitlines():
            raise ValueError("adapter Spring provider registration missing")
        if "org/commandergym/argentum/CommanderGymControllerProvider.class" not in archive.namelist():
            raise ValueError("compiled controller provider missing")
    with zipfile.ZipFile(server) as archive:
        if require_qa_callback_gate and QA_GATE not in archive.namelist():
            raise ValueError("compiled QA callback admission gate missing")
        if manifest(archive).get("Argentum-Revision") != native_sha:
            raise ValueError("server source pin does not match the release")
        candidates = [name for name in archive.namelist()
                      if name.startswith("BOOT-INF/lib/") and name.endswith(".jar")
                      and "commander-gym-argentum-adapter" in name]
        if len(candidates) != 1 or archive.read(candidates[0]) != adapter_bytes:
            raise ValueError("server must contain exactly the qualified adapter bytes")
    receipt = {
        "schemaVersion": 1, "nativeSha": native_sha, "gymSha": gym_sha,
        "serverSha256": hashlib.sha256(server.read_bytes()).hexdigest(),
        "adapterSha256": hashlib.sha256(adapter_bytes).hexdigest(),
        "embeddedAdapter": candidates[0],
        "deploymentAuthorized": False, "providerCallsMade": 0,
    }
    if require_qa_callback_gate:
        receipt["qaCallbackGatePackaged"] = True
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-jar", type=Path, required=True)
    parser.add_argument("--adapter-jar", type=Path, required=True)
    parser.add_argument("--native-sha", required=True)
    parser.add_argument("--gym-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-qa-callback-gate", action="store_true")
    args = parser.parse_args()
    receipt = qualify(args.server_jar, args.adapter_jar, args.native_sha, args.gym_sha,
                      require_qa_callback_gate=args.require_qa_callback_gate)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
