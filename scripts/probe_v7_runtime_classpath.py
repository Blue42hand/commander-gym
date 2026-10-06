"""Fingerprint the classpath executed by the qualified v7 Gradle server task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


MARKER = "CODEX_V7_RUNTIME_CLASSPATH="
INIT_SCRIPT = Path(__file__).with_name("v7_runtime_classpath.init.gradle")


def _hash_entry(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        raise ValueError(f"runtime classpath entry is linked: {path}")
    if not path.exists():
        # Gradle includes absent Java/resource output directories when this
        # Kotlin-only adapter has no files for that source set.
        return "missing"
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_symlink():
                raise ValueError(f"runtime classpath tree contains a link: {item}")
            if item.is_file():
                digest.update(item.relative_to(path).as_posix().encode())
                digest.update(b"\0")
                digest.update(hashlib.sha256(item.read_bytes()).digest())
    else:
        raise ValueError(f"runtime classpath entry is not a file or directory: {path}")
    return digest.hexdigest()


def fingerprint(engine_dir: Path, gym_dir: Path) -> dict[str, object]:
    engine = engine_dir.resolve()
    gym = gym_dir.resolve()
    if not INIT_SCRIPT.is_file() or INIT_SCRIPT.is_symlink():
        raise ValueError("reviewed runtime classpath probe is missing")
    command = [str(engine / "scripts/gradle-locked"), "--offline", "--quiet",
               "--init-script", str(INIT_SCRIPT), "-p", str(gym / "jvm-adapter"),
               "codexV7RuntimeClasspath"]
    environment = dict(os.environ)
    environment["ARGENTUM_ENGINE_DIR"] = str(engine)
    result = subprocess.run(command, cwd=engine, env=environment, capture_output=True, text=True,
                            check=True, timeout=900)
    matches = [line.removeprefix(MARKER) for line in result.stdout.splitlines()
               if line.startswith(MARKER)]
    if len(matches) != 1:
        raise ValueError("Gradle did not report one effective server runtime classpath")
    names = json.loads(matches[0])
    if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
        raise ValueError("Gradle runtime classpath is invalid")
    if any(not Path(name).is_absolute() or Path(name).is_symlink() for name in names):
        raise ValueError("Gradle runtime classpath contains a relative or linked entry")
    paths = [Path(name).resolve() for name in names]
    if len(paths) != len(set(paths)):
        raise ValueError("Gradle runtime classpath contains relative or duplicate entries")
    relative_gym = [path.relative_to(gym) for path in paths if path.is_relative_to(gym)]
    relative_engine = [path.relative_to(engine) for path in paths if path.is_relative_to(engine)]
    if (not any(str(path).startswith("jvm-adapter/build/classes/kotlin/main") for path in relative_gym)
        or not any(str(path).startswith("jvm-adapter/build/classes/kotlin/test") for path in relative_gym)
        or not any(path.parts and path.parts[0] == "game-server" for path in relative_engine)
        or not any(path.parts and path.parts[0] == "rules-engine" for path in relative_engine)):
        raise ValueError("runtime classpath omits qualified adapter or fixed native engine outputs")
    entries = [[str(path), _hash_entry(path)] for path in paths]
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "entryCount": len(entries),
            "adapterEntries": len(relative_gym), "engineEntries": len(relative_engine)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--gym-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fingerprint(args.engine_dir, args.gym_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
