#!/usr/bin/env python3
"""Run Argentum's live CardRegistry coverage probe against an exact public roster."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "rosters" / "argentum-native-v1" / "manifest.json"
DEFAULT_SOURCE_PIN = ROOT / "argentum-coverage-source.json"


def load_json(path: Path) -> dict:
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


def normalize_remote(url: str) -> str:
    value = url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    return value


def verify_argentum_checkout(source: Path, pin: dict) -> dict:
    required = ("repository", "upstream_repository", "integration_branch", "commit")
    missing = [key for key in required if not pin.get(key)]
    if missing:
        raise SystemExit("argentum coverage source pin missing: " + ", ".join(missing))

    commit = pin["commit"]
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit.lower()):
        raise SystemExit(f"Invalid Argentum commit SHA: {commit}")

    source = source.resolve()
    if git(source, "rev-parse", "--is-inside-work-tree") != "true":
        raise SystemExit(f"Not a Git checkout: {source}")
    head = git(source, "rev-parse", "HEAD")
    origin = git(source, "remote", "get-url", "origin")
    if head != commit:
        raise SystemExit(f"Argentum HEAD {head} != pinned {commit}")
    if normalize_remote(origin) != normalize_remote(pin["repository"]):
        raise SystemExit(f"Argentum origin {origin} != pinned {pin['repository']}")

    return {
        "repository": pin["repository"],
        "upstream_repository": pin["upstream_repository"],
        "integration_branch": pin["integration_branch"],
        "commit": head,
    }


def load_roster(manifest_path: Path) -> tuple[dict, list[tuple[dict, Path, dict]]]:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise SystemExit(f"Unsupported roster manifest schema: {manifest.get('schema_version')!r}")
    entries = manifest.get("decks")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("Roster manifest contains no decks")

    root = manifest_path.parent
    decks: list[tuple[dict, Path, dict]] = []
    for entry in entries:
        deck_path = root / entry["file"]
        if not deck_path.is_file():
            raise SystemExit(f"Roster deck missing: {deck_path}")
        expected_digest = entry.get("sha256")
        actual_digest = sha256(deck_path)
        if not expected_digest or actual_digest != expected_digest:
            raise SystemExit(
                f"Roster deck digest drift for {deck_path}: expected {expected_digest}, got {actual_digest}"
            )

        deck = load_json(deck_path)
        if deck.get("deck_id") != entry.get("id"):
            raise SystemExit(
                f"Roster deck id mismatch for {deck_path}: {deck.get('deck_id')!r} != {entry.get('id')!r}"
            )
        commander = deck.get("commander")
        cards = deck.get("cards")
        if not isinstance(commander, str) or not commander:
            raise SystemExit(f"Roster deck has no commander: {deck_path}")
        if not isinstance(cards, dict) or not cards:
            raise SystemExit(f"Roster deck has no cards: {deck_path}")
        if any(not isinstance(name, str) or not isinstance(count, int) or count <= 0 for name, count in cards.items()):
            raise SystemExit(f"Roster deck has invalid card quantities: {deck_path}")
        if sum(cards.values()) != 100:
            raise SystemExit(f"Roster deck is not exactly 100 cards: {deck_path}")
        if cards.get(commander) != 1:
            raise SystemExit(f"Roster commander must occur exactly once: {deck_path}")
        decks.append((entry, deck_path, deck))

    return manifest, decks


def write_probe_deck(path: Path, deck: dict) -> None:
    commander = deck["commander"]
    lines = ["Commander", f"1 {commander}", "", "Deck"]
    for name, count in deck["cards"].items():
        library_count = count - (1 if name == commander else 0)
        if library_count > 0:
            lines.append(f"{library_count} {name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_probe(
    argentum: Path,
    output: Path,
    decks: list[tuple[dict, Path, dict]],
) -> dict[str, Path]:
    gradle_runner = argentum / "scripts" / "gradle-locked"
    if not gradle_runner.is_file():
        raise SystemExit(f"Argentum locked Gradle runner not found: {gradle_runner}")

    with tempfile.TemporaryDirectory(prefix="commander-gym-active-roster-coverage-") as temp_dir:
        temp = Path(temp_dir)
        prepared: list[Path] = []
        source_map: dict[str, Path] = {}
        for index, (_, source_path, deck) in enumerate(decks):
            probe_path = temp / f"{index:02d}-{source_path.stem}.txt"
            write_probe_deck(probe_path, deck)
            prepared.append(probe_path)
            source_map[str(probe_path.resolve())] = source_path.resolve()

        subprocess.run(
            [
                str(gradle_runner),
                "--no-configuration-cache",
                "-q",
                ":gym-server:commanderGymDeckCoverage",
                f"-PdeckFiles={';'.join(str(path.resolve()) for path in prepared)}",
                f"-PcoverageOutput={output.resolve()}",
            ],
            cwd=argentum,
            check=True,
        )
        return source_map


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def build_union_summary(report: dict, decks: list[tuple[dict, Path, dict]]) -> dict:
    source_names: set[str] = set()
    source_slots = 0
    for _, _, deck in decks:
        source_names.update(deck["cards"])
        source_slots += sum(deck["cards"].values())

    missing_by_name: dict[str, dict] = {}
    for deck_report in report.get("decks", []):
        deck_file = deck_report["file"]
        for missing in deck_report.get("missing", []):
            name = missing["name"]
            item = missing_by_name.setdefault(
                name,
                {"name": name, "deckCount": 0, "slotCount": 0, "decks": []},
            )
            item["deckCount"] += 1
            item["slotCount"] += missing["copies"]
            item["decks"].append(deck_file)

    missing = sorted(
        missing_by_name.values(),
        key=lambda item: (-item["deckCount"], -item["slotCount"], item["name"]),
    )
    return {
        "sourceSlots": source_slots,
        "uniqueSourceNames": len(source_names),
        "uniqueImplementedAfterFrontFaceNormalization": len(source_names) - len(missing),
        "uniqueMissing": len(missing),
        "missing": missing,
    }


def enrich_report(
    output: Path,
    report: dict,
    manifest_path: Path,
    manifest: dict,
    decks: list[tuple[dict, Path, dict]],
    argentum_identity: dict,
    source_map: dict[str, Path],
) -> dict:
    report["argentum"] = argentum_identity
    report["commanderGym"] = {
        "repository": "https://github.com/Blue42hand/commander-gym.git",
        "commit": git(ROOT, "rev-parse", "HEAD"),
    }
    report["roster"] = {
        "id": manifest.get("roster_id"),
        "manifest": repo_relative(manifest_path),
        "manifestSha256": sha256(manifest_path),
        "deckCount": len(decks),
    }
    report["coverageAuthority"] = (
        "Argentum gym-server CardRegistry via :gym-server:commanderGymDeckCoverage; "
        "source-level card-definition scanning is not used for coverage truth."
    )

    for deck_report in report.get("decks", []):
        probe = str(Path(deck_report["file"]).resolve())
        source = source_map.get(probe)
        if source is None:
            raise SystemExit(f"Argentum report referenced an unknown probe deck: {deck_report['file']}")
        deck_report["file"] = repo_relative(source)

    report["decks"] = sorted(report.get("decks", []), key=lambda item: item["file"])
    report["union"] = build_union_summary(report, decks)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argentum-source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-pin", type=Path, default=DEFAULT_SOURCE_PIN)
    parser.add_argument("--output", type=Path, default=ROOT / "argentum-coverage.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pin = load_json(args.source_pin)
    argentum_identity = verify_argentum_checkout(args.argentum_source, pin)
    manifest, decks = load_roster(args.manifest)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_map = run_probe(args.argentum_source.resolve(), args.output, decks)
    report = load_json(args.output)
    report = enrich_report(
        args.output,
        report,
        args.manifest,
        manifest,
        decks,
        argentum_identity,
        source_map,
    )

    print(
        f"Argentum CardRegistry coverage: {len(report['decks'])} decks, "
        f"{report['union']['uniqueImplementedAfterFrontFaceNormalization']}/"
        f"{report['union']['uniqueSourceNames']} unique active-roster names implemented"
    )
    for deck in report["decks"]:
        print(
            f"{deck['exactImplementedSlots']:3d}/{deck['totalSlotsIncludingCommander']:3d} exact  "
            f"{deck['normalizedCoveragePercent']:6.2f}% normalized  "
            f"{deck.get('commander') or '(none)'}  [{deck['file']}]"
        )


if __name__ == "__main__":
    main()
