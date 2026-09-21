#!/usr/bin/env python3
"""Run Argentum's live CardRegistry coverage probe against a public Commander Gym roster."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def validate_manifest_files(manifest_path: Path, manifest: dict) -> list[tuple[dict, Path, dict]]:
    root = manifest_path.parent
    rows: list[tuple[dict, Path, dict]] = []
    errors: list[str] = []
    for entry in manifest["decks"]:
        deck_path = root / entry["file"]
        if not deck_path.is_file():
            errors.append(f"{entry['id']}: missing deck file {entry['file']}")
            continue
        if sha256(deck_path) != entry.get("sha256"):
            errors.append(f"{entry['id']}: deck digest drift")
        deck = load(deck_path)
        if sum(deck["cards"].values()) != 100:
            errors.append(f"{entry['id']}: expected exactly 100 cards")
        commander = deck["commander"]
        if deck["cards"].get(commander) != 1:
            errors.append(f"{entry['id']}: commander must occur exactly once")
        rows.append((entry, deck_path, deck))
    if errors:
        raise SystemExit("\n".join(f"ERROR: {error}" for error in errors))
    return rows


def write_probe_deck(path: Path, deck: dict) -> None:
    commander = deck["commander"]
    lines = ["Commander", f"1 {commander}", "", "Deck"]
    for name, count in deck["cards"].items():
        if name == commander:
            continue
        lines.append(f"{count} {name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_probe(argentum: Path, output: Path, rows: list[tuple[dict, Path, dict]]) -> dict[str, str]:
    gradle_runner = argentum / "scripts" / "gradle-locked"
    if not gradle_runner.is_file():
        raise SystemExit(f"Argentum locked Gradle runner not found: {gradle_runner}")

    with tempfile.TemporaryDirectory(prefix="commander-gym-active-roster-") as temp_dir:
        temp_root = Path(temp_dir)
        probes: list[Path] = []
        source_map: dict[str, str] = {}
        for index, (_, deck_path, deck) in enumerate(rows):
            probe = temp_root / f"{index:02d}-{deck['deck_id']}.txt"
            write_probe_deck(probe, deck)
            probes.append(probe)
            source_map[str(probe.resolve())] = deck_path.relative_to(ROOT).as_posix()

        deck_arg = ";".join(str(path.resolve()) for path in probes)
        subprocess.run(
            [
                str(gradle_runner),
                "--no-configuration-cache",
                "-q",
                ":gym-server:commanderGymDeckCoverage",
                f"-PdeckFiles={deck_arg}",
                f"-PcoverageOutput={output.resolve()}",
            ],
            cwd=argentum,
            check=True,
        )
        return source_map


def enrich_report(
    output: Path,
    manifest_path: Path,
    manifest: dict,
    argentum: Path,
    source_map: dict[str, str],
) -> dict:
    report = load(output)
    for deck in report["decks"]:
        probe = str(Path(deck["file"]).resolve())
        source = source_map.get(probe)
        if source is None:
            raise SystemExit(f"Coverage report referenced unknown probe deck: {deck['file']}")
        deck["file"] = source

    missing_by_name: dict[str, dict] = {}
    for deck in report["decks"]:
        for gap in deck["missing"]:
            entry = missing_by_name.setdefault(
                gap["name"],
                {"name": gap["name"], "deckCount": 0, "slotCount": 0, "decks": []},
            )
            entry["deckCount"] += 1
            entry["slotCount"] += gap["copies"]
            entry["decks"].append(deck["file"])

    report["union"] = {
        "missing": sorted(
            missing_by_name.values(),
            key=lambda item: (-item["deckCount"], -item["slotCount"], item["name"]),
        )
    }
    report["commanderGym"] = {
        "repository": "Blue42hand/commander-gym",
        "commit": git(ROOT, "rev-parse", "HEAD"),
        "roster": manifest["roster_id"],
        "manifest": manifest_path.relative_to(ROOT).as_posix(),
        "manifestArgentumRevision": manifest["argentum"]["revision"],
    }
    report["argentum"] = {
        "repository": "Blue42hand/argentum-engine",
        "commit": git(argentum, "rev-parse", "HEAD"),
    }
    report["allDecksCompleteAfterFrontFaceNormalization"] = all(
        deck["normalizedImplementedSlots"] == deck["totalSlotsIncludingCommander"]
        for deck in report["decks"]
    )
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argentum-source", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "rosters" / "argentum-native-v1" / "manifest.json",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "argentum-roster-coverage.json")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit nonzero when any roster slot is missing from CardRegistry",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest = load(manifest_path)
    rows = validate_manifest_files(manifest_path, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_map = run_probe(args.argentum_source.resolve(), args.output, rows)
    report = enrich_report(
        args.output.resolve(),
        manifest_path,
        manifest,
        args.argentum_source.resolve(),
        source_map,
    )

    print(
        f"CardRegistry active-roster coverage: {len(report['decks'])} decks, "
        f"{report['registryCardNames']} registry names, "
        f"Argentum {report['argentum']['commit']}"
    )
    for deck in report["decks"]:
        print(
            f"{deck['normalizedImplementedSlots']:3d}/"
            f"{deck['totalSlotsIncludingCommander']:3d} "
            f"{deck.get('commander') or '(none)'}"
        )

    if args.require_complete and not report["allDecksCompleteAfterFrontFaceNormalization"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
