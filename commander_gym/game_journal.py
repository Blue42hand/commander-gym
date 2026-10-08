"""Private append-only game evidence. No journal data is a pilot input.

Argentum owns masking, rules, actions, events and replay. This module preserves
producer evidence; it does not reconstruct state or manufacture legal options.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .evidence import validate_raw_evidence_envelope
from .storage import artifact_id_for_bytes

VERSION = 1
MANIFEST_KIND = "commander-gym.game-capture-manifest"
ZERO = "0" * 64
PIN_KEYS = ("engine", "gym", "models", "decks", "bindings", "config", "rng")
FORBIDDEN = {"authorization", "headers", "rawheaders", "apikey", "password",
             "secret", "credentials", "reconnecttoken", "accesstoken",
             "refreshtoken", "bearertoken", "sidecartoken", "token"}


class JournalError(ValueError):
    pass


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _safe(value: Any) -> None:
    """Reject transport/config secrets before either admin or seat persistence."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in FORBIDDEN or normalized.endswith("apikey"):
                raise JournalError("credential or transport field is forbidden")
            _safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe(item)
    elif isinstance(value, str) and re.search(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}", value):
        raise JournalError("credential-shaped value is forbidden")


def _private_dir(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise JournalError("journal directory must be private (0700) and not a symlink")


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_private(path: Path, data: bytes) -> None:
    temporary = path.parent / (".pending-" + str(uuid.uuid4()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()  # only our never-published temporary, never history


def inspect_journal(directory: Path) -> dict[str, Any]:
    """Verify a consistent prefix. Call after closing the writer for final evidence.

    Hashes detect accidental alteration, not an attacker rewriting the whole chain.
    Preserve the final root separately to establish a trusted integrity anchor.
    """
    _private_dir(directory)
    previous, sequence, run_id = ZERO, 0, None
    rows, issues, sources = [], [], {}
    terminal = False
    files = sorted(directory.glob("*.jsonl"))
    for index, path in enumerate(files):
        if path.name != f"{index:06d}.jsonl":
            issues.append("missing_segment")
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise JournalError("journal segment must be private and not a symlink")
        with path.open("rb") as handle:
            for line in handle:
                if not line.endswith(b"\n"):
                    issues.append("partial_tail")
                    break
                try:
                    row = json.loads(line)
                    digest = row.pop("sha256")
                    if type(row["schema_version"]) is not int or row["schema_version"] != VERSION:
                        raise JournalError("unsupported_schema")
                    if row["sequence"] != sequence or row["previous_sha256"] != previous:
                        raise JournalError("sequence_or_chain_gap")
                    if hashlib.sha256(_json(row)).hexdigest() != digest:
                        raise JournalError("hash_mismatch")
                    if run_id is not None and row["run_id"] != run_id:
                        raise JournalError("run_id_mismatch")
                    if terminal:
                        raise JournalError("event_after_terminal")
                    _safe(row)
                    source = row.get("source")
                    if source is not None:
                        if row["source_sequence"] != sources.get(source, 0):
                            raise JournalError("source_gap_or_duplicate")
                        sources[source] = row["source_sequence"] + 1
                    if sequence == 0 and row["kind"] != "manifest":
                        raise JournalError("missing_manifest")
                    terminal = row["kind"] == "terminal"
                    run_id, previous = row["run_id"], digest
                    row["sha256"] = digest
                    rows.append(row)
                    sequence += 1
                except (ValueError, KeyError, TypeError) as exc:
                    issues.append(str(exc) if isinstance(exc, JournalError) else "invalid_row")
                    break
        if issues:
            break
    if not rows:
        issues.append("empty_journal")
    if not terminal:
        issues.append("missing_terminal")
    return {"schema_version": VERSION, "run_id": run_id, "rows": rows,
            "issues": issues, "root_sha256": previous, "sources": sources,
            "integrity_ok": not any(i != "missing_terminal" for i in issues),
            "closed": terminal and not issues,
            "recording_complete": terminal and not issues and
            rows[-1]["payload"].get("recording_complete") is True}


class PrivateGameJournal:
    """Single-process writer, durable per event, segmented without history deletion.

    Caller supplies exact immutable pins or null for unavailable evidence. Limits
    stop collection explicitly; they never delete old segments or change game rules.
    """
    def __init__(self, directory: Path, run_id: str, pins: Mapping[str, Any], *,
                 game_id: str | None = None, required_sources: tuple[str, ...] = ("native",),
                 segment_bytes: int = 16 * 1024 * 1024,
                 max_bytes: int = 1024 * 1024 * 1024,
                 terminal_reserve: int = 64 * 1024) -> None:
        if not isinstance(run_id, str) or not run_id:
            raise JournalError("run_id is required")
        if set(pins) != set(PIN_KEYS):
            raise JournalError("pins must contain engine/gym/models/decks/bindings/config/rng")
        _safe(pins)
        if not 0 < terminal_reserve < max_bytes or segment_bytes <= 0:
            raise JournalError("invalid storage limits")
        _private_dir(directory, create=True)
        self.directory, self.run_id = directory, run_id
        self.game_id, self.required_sources = game_id, required_sources
        self.segment_bytes, self.max_bytes = segment_bytes, max_bytes
        self.reserve = terminal_reserve
        self._lock = threading.RLock()
        self._fd = os.open(directory / ".writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.sequence, self.previous, self.sources = 0, ZERO, {}
            self.terminal, self.failed = False, False
            self.clock_id, self.origin = str(uuid.uuid4()), time.monotonic_ns()
            self.segment, self.used = 0, 0
            files = sorted(directory.glob("*.jsonl"))
            if files:
                report = inspect_journal(directory)
                if not report["integrity_ok"] or report["closed"]:
                    raise JournalError("cannot resume damaged or closed journal")
                if report["run_id"] != run_id or report["rows"][0]["payload"]["pins"] != dict(pins):
                    raise JournalError("resume identity or pins mismatch")
                manifest = report["rows"][0]["payload"]
                if manifest["game_id"] != game_id or manifest["required_sources"] != list(required_sources):
                    raise JournalError("resume game or source contract mismatch")
                self.sequence, self.previous = len(report["rows"]), report["root_sha256"]
                self.sources = report["sources"]
                self.segment = len(files) - 1
                self.used = sum(path.stat().st_size for path in files)
                self.append("resume", {"previous_clock_id": report["rows"][-1]["clock_id"]})
            else:
                self.append("manifest", {"pins": dict(pins), "pin_digest": hashlib.sha256(_json(pins)).hexdigest(),
                                         "game_id": game_id, "required_sources": list(required_sources),
                                         "unavailable_pins": [k for k, v in pins.items() if v is None]})
        except BaseException:
            os.close(self._fd)
            self._fd = -1
            raise

    def append(self, kind: str, payload: Mapping[str, Any], *, seat_id: str | None = None,
               source: str | None = None, source_sequence: int | None = None) -> str:
        with self._lock:
            if self._fd < 0 or self.terminal or self.failed:
                raise JournalError("journal is closed or failed")
            if kind not in {"manifest", "resume", "native_transition", "native_replay",
                            "seat_callback", "decision_started", "terminal", "coverage_gap", "raw_evidence"}:
                raise JournalError("unsupported event kind")
            _safe(payload)
            if (source is None) != (source_sequence is None):
                raise JournalError("source and sequence must be supplied together")
            if source is not None and (type(source_sequence) is not int or
                                       source_sequence != self.sources.get(source, 0)):
                raise JournalError("source gap or duplicate")
            if kind in {"seat_callback", "decision_started"}:
                if not isinstance(seat_id, str) or not seat_id:
                    raise JournalError("seat evidence requires seat_id")
                observation = payload.get("observation")
                if not isinstance(observation, Mapping):
                    raise JournalError("seat observation must be an object")
                state = observation.get("state")
                viewers = [observation.get("viewingPlayerId"), observation.get("perspectivePlayerId"),
                           state.get("viewingPlayerId") if isinstance(state, Mapping) else None]
                if seat_id not in viewers or any(
                        v is not None and v != seat_id for v in viewers):
                    raise JournalError("seat observation must have matching viewingPlayerId")
                if not isinstance(payload.get("decision_id"), str) or not payload["decision_id"]:
                    raise JournalError("seat evidence requires decision_id")
            elif seat_id is not None:
                raise JournalError("admin evidence must not be marked seat-visible")
            if kind == "native_transition":
                if source != "native" or not all(key in payload for key in (
                        "game_id", "native_schema", "action", "events", "result",
                        "before_state_digest", "after_state_digest")):
                    raise JournalError("native transition requires ordered versioned action/events/result evidence")
                if not isinstance(payload["events"], list) or payload["game_id"] != self.game_id:
                    raise JournalError("native transition game or events mismatch")
            row = {"schema_version": VERSION, "run_id": self.run_id,
                   "sequence": self.sequence, "kind": kind, "seat_id": seat_id,
                   "visibility": "seat" if seat_id is not None else "admin",
                   "recorded_at": datetime.now(timezone.utc).isoformat(),
                   "clock_id": self.clock_id, "elapsed_ns": time.monotonic_ns() - self.origin,
                   "source": source, "source_sequence": source_sequence,
                   "payload": dict(payload), "previous_sha256": self.previous}
            digest = hashlib.sha256(_json(row)).hexdigest()
            data = _json({**row, "sha256": digest}) + b"\n"
            limit = self.max_bytes if kind == "terminal" else self.max_bytes - self.reserve
            if self.used + len(data) > limit:
                raise JournalError("storage limit reached; stop collection and seal partial journal")
            path = self.directory / f"{self.segment:06d}.jsonl"
            if path.exists() and path.stat().st_size + len(data) > self.segment_bytes:
                self.segment += 1
                path = self.directory / f"{self.segment:06d}.jsonl"
            created = not path.exists()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_mode & 0o077:
                    raise JournalError("segment permissions are not private")
                with os.fdopen(fd, "ab", closefd=False) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(fd)
                if created:
                    _sync_dir(self.directory)
            except BaseException:
                self.failed = True  # no further writes after uncertain partial write
                raise
            finally:
                os.close(fd)
            self.used += len(data)
            self.sequence += 1
            self.previous = digest
            if source is not None:
                self.sources[source] = source_sequence + 1
            self.terminal = kind == "terminal"
            return digest

    def finish(self, outcome: Mapping[str, Any], *, expected_sources: Mapping[str, int],
               gaps: list[str]) -> str:
        """Outcome is evidence, never supervisor qualification or a replay guarantee."""
        with self._lock:
            report = inspect_journal(self.directory)
            if not report["integrity_ok"]:
                raise JournalError("cannot seal a damaged journal")
            missing_pins = report["rows"][0]["payload"]["unavailable_pins"]
            pending: set[tuple[str, str]] = set()
            for row in report["rows"]:
                decision = row["payload"].get("decision_id")
                if row["kind"] == "decision_started":
                    pending.add((row["seat_id"], decision))
                elif row["kind"] == "seat_callback":
                    pending.discard((row["seat_id"], decision))
            missing = sorted(set(gaps) | {"pin:" + k for k in missing_pins} |
                             ({"game_id_unavailable"} if self.game_id is None else set()) |
                             ({"required_sources"} if not set(self.required_sources) <= set(expected_sources) else set()) |
                             ({"source_counts"} if dict(expected_sources) != self.sources else set()) |
                             ({"pending_decisions"} if pending else set()) |
                             ({"non_native_terminal"} if outcome.get("kind") != "native_terminal" else set()) |
                             ({"canonical_training_evidence_unavailable"} if not any(
                                 r["kind"] == "raw_evidence" for r in report["rows"]) else set()) |
                             ({"native_transitions_unavailable"} if not any(
                                 r["kind"] == "native_transition" for r in report["rows"]) else set()))
            root = self.append("terminal", {"outcome": dict(outcome), "gaps": missing,
                                           "expected_sources": dict(expected_sources),
                                           "observed_sources": dict(self.sources),
                                           "recording_complete": not missing,
                                           "exact_replay_verified": False})
            self.publish_manifest()
            return root

    def native_replay(self, replay: Mapping[str, Any]) -> str:
        """Preserve native CompactReplay verbatim privately, including RNG/card pins.

        This is admin evidence, not the public replay presentation. It cannot supply
        human legal menus, per-choice timing, or emitted events absent from the input.
        """
        if type(replay.get("version", 1)) is not int or replay.get("version", 1) not in (1, 2, 3, 4):
            raise JournalError("unsupported native compact replay version")
        if replay.get("gameId") != self.game_id or not isinstance(replay.get("actions"), list):
            raise JournalError("native replay identity or actions missing")
        if not isinstance(replay.get("setup"), Mapping) or "seed" not in replay["setup"]:
            raise JournalError("native replay setup/RNG missing")
        return self.append("native_replay", {"replay": dict(replay)})

    def raw_evidence(self, envelope: Mapping[str, Any]) -> str:
        """Reuse the existing raw-run-evidence contract, not a parallel dataset shape."""
        validate_raw_evidence_envelope(envelope)
        run = envelope["run"]
        if run["run_id"] != self.run_id or run["game_id"] != self.game_id:
            raise JournalError("raw evidence identity mismatch")
        rows = inspect_journal(self.directory)["rows"]
        if any(row["kind"] == "raw_evidence" for row in rows):
            raise JournalError("canonical raw evidence already attached")
        ids = [row["payload"]["decision_id"] for row in rows if row["kind"] == "seat_callback"]
        if run["decision_ids"] != ids:
            raise JournalError("canonical decision IDs must match captured callbacks in order")
        return self.append("raw_evidence", {"envelope": dict(envelope)})

    def finish_from_native_receipt(self, receipt_path: Path, *,
                                   expected_sources: Mapping[str, int], gaps: list[str],
                                   supervisor_termination: str) -> str:
        """Consume #205's receipt only from this journal's own private run directory.

        Receipt has no game ID. The orchestration owner must assign its path to this
        session before launch; do not attach a historical or foreign receipt.
        Supervisor termination remains an independent unchanged fact.
        """
        from .human_gui_session import GuiNativeTerminalWatch
        if (receipt_path.is_symlink() or not receipt_path.resolve().is_relative_to(self.directory.resolve())
                or receipt_path.stat().st_mode & 0o077):
            raise JournalError("native terminal receipt must belong to this private run")
        watcher = GuiNativeTerminalWatch(receipt_path)
        watcher.scan()
        receipt = watcher.receipt
        if receipt is None:
            raise JournalError("native terminal receipt is unavailable")
        return self.finish({"kind": "native_terminal", "game_id": self.game_id,
                            "winner_id": receipt["winnerId"], "native_receipt": receipt,
                            "supervisor_termination": supervisor_termination},
                           expected_sources=expected_sources, gaps=gaps)

    def publish_manifest(self) -> dict[str, Any]:
        """Recover publication after a crash between terminal fsync and manifest replace.

        Analysis must independently verify referenced hashes. All file paths are
        relative to this canonical game directory, never URLs or upload targets.
        """
        with self._lock:
            report = inspect_journal(self.directory)
            if not report["closed"]:
                raise JournalError("only a sealed intact journal can be published")
            artifacts = []
            for path in sorted(self.directory.glob("*.jsonl")):
                artifacts.append({"path": path.name, "role": "admin_journal", "size_bytes": path.stat().st_size,
                                  "artifact_id": artifact_id_for_bytes(path.read_bytes()),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            seats = sorted({row["seat_id"] for row in report["rows"] if row["seat_id"] is not None})
            for seat in seats:
                # IDs never become file paths; projections are derived, not competing truth.
                name = "seat-" + hashlib.sha256(seat.encode()).hexdigest() + ".projection.json"
                data = _json(seat_projection(report, seat)) + b"\n"
                _atomic_private(self.directory / name, data)
                artifacts.append({"path": name, "role": "derived_seat_projection", "seat_id": seat,
                                  "size_bytes": len(data), "artifact_id": artifact_id_for_bytes(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
            for row in report["rows"]:
                if row["kind"] == "raw_evidence":
                    data = _json(row["payload"]["envelope"]) + b"\n"
                    name = "raw-evidence-" + hashlib.sha256(data).hexdigest() + ".json"
                    _atomic_private(self.directory / name, data)
                    artifacts.append({"path": name, "role": "canonical_raw_evidence",
                                      "size_bytes": len(data), "artifact_id": artifact_id_for_bytes(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})
            evidence_hash = hashlib.sha256(_json(artifacts)).hexdigest()
            result = {"schema_version": VERSION, "kind": MANIFEST_KIND, "run_id": report["run_id"],
                      "game_id": report["rows"][0]["payload"]["game_id"],
                      "ready_for_analysis": True, "recording_complete": report["recording_complete"],
                      "journal_root_sha256": report["root_sha256"],
                      "artifact_hash": evidence_hash, "artifacts": artifacts,
                      "storage_bytes": sum(a["size_bytes"] for a in artifacts),
                      "outcome": report["rows"][-1]["payload"]["outcome"],
                      "gaps": report["rows"][-1]["payload"]["gaps"],
                      "analysis": {"report_path_template": "analysis/{analysis_version}/report.json",
                                   "idempotency_fields": ["run_id", "artifact_hash", "analysis_version"]}}
            _atomic_private(self.directory / "manifest.json", _json(result) + b"\n")
            return result

    def close(self) -> None:
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1

    def __enter__(self) -> "PrivateGameJournal":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()  # no invented terminal on crashes or exceptions


def seat_projection(report: Mapping[str, Any], seat_id: str) -> list[dict[str, Any]]:
    """Offline training projection; admin/referee payloads never enter this output."""
    if not report["integrity_ok"]:
        raise JournalError("cannot project damaged journal")
    return [{"schema_version": VERSION, "run_id": row["run_id"],
             "sequence": row["sequence"], "kind": row["kind"],
             "recorded_at": row["recorded_at"], "payload": row["payload"]}
            for row in report["rows"] if row["visibility"] == "seat" and row["seat_id"] == seat_id]


def publish_finalized_manifest(directory: Path) -> dict[str, Any]:
    """Idempotent local recovery after terminal durability but before publication."""
    journal = object.__new__(PrivateGameJournal)
    journal.directory, journal._lock = directory, threading.RLock()
    return journal.publish_manifest()


def verify_finalized_manifest(directory: Path) -> dict[str, Any]:
    """Discovery gate: verify journal and every artifact before analysis claims."""
    report = inspect_journal(directory)
    path = directory / "manifest.json"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise JournalError("manifest must be private and not a symlink")
    manifest = json.loads(path.read_bytes())
    _safe(manifest)
    if (manifest.get("kind") != MANIFEST_KIND or type(manifest.get("schema_version")) is not int or
            manifest.get("schema_version") != VERSION or not report["closed"] or
            manifest.get("ready_for_analysis") is not True or
            manifest.get("run_id") != report["run_id"] or
            manifest.get("journal_root_sha256") != report["root_sha256"] or
            manifest.get("recording_complete") != report["recording_complete"] or
            manifest.get("game_id") != report["rows"][0]["payload"]["game_id"] or
            manifest.get("outcome") != report["rows"][-1]["payload"]["outcome"] or
            manifest.get("gaps") != report["rows"][-1]["payload"]["gaps"]):
        raise JournalError("finalized manifest does not match sealed journal")
    artifacts = manifest["artifacts"]
    names = [artifact["path"] for artifact in artifacts]
    if len(set(names)) != len(names) or sorted(a["path"] for a in artifacts if a["role"] == "admin_journal") != [
            p.name for p in sorted(directory.glob("*.jsonl"))]:
        raise JournalError("manifest journal membership mismatch")
    for artifact in artifacts:
        name = artifact["path"]
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise JournalError("artifact path must be local to game directory")
        path = directory / name
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise JournalError("artifact must be private and not a symlink")
        data = path.read_bytes()
        if (hashlib.sha256(data).hexdigest() != artifact["sha256"] or
                artifact_id_for_bytes(data) != artifact["artifact_id"] or
                len(data) != artifact["size_bytes"]):
            raise JournalError("finalized artifact hash or size mismatch")
    if hashlib.sha256(_json(artifacts)).hexdigest() != manifest["artifact_hash"]:
        raise JournalError("manifest artifact hash mismatch")
    return manifest


def discover_finalized_manifests(runs_root: Path) -> list[dict[str, Any]]:
    """Local metadata only; damaged/unsealed/legacy runs are not silently promoted."""
    results = []
    for directory in sorted(runs_root.iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not (directory / "manifest.json").exists():
            continue
        try:
            manifest = verify_finalized_manifest(directory)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        results.append({"relative_run_directory": directory.name, **{key: manifest[key] for key in (
            "schema_version", "run_id", "game_id", "ready_for_analysis", "recording_complete",
            "artifact_hash", "journal_root_sha256", "outcome", "gaps", "analysis")}})
    return results


class RecorderSeatSink:
    """Pair with GameServerSeatAdapter's start and completion sinks.

    The existing SeatProvenance observation/choice/model-I/O schema is preserved.
    No rationale is invented; provider metadata includes it only when supplied.
    """
    def __init__(self, journal: PrivateGameJournal, seat_id: str) -> None:
        self.journal, self.seat_id = journal, seat_id
        self._pending = threading.local()

    def started(self, event: Any) -> None:
        if getattr(self._pending, "decision_id", None) is not None:
            raise JournalError("overlapping seat callback on the same worker")
        decision_id = str(uuid.uuid4())
        self.journal.append("decision_started", {"decision_id": decision_id,
                            "callback": event.callback, "observation": event.observation},
                            seat_id=self.seat_id)
        self._pending.decision_id = decision_id

    def finished(self, event: Any) -> None:
        decision_id = getattr(self._pending, "decision_id", None)
        if decision_id is None:
            raise JournalError("seat completion has no durable start")
        source = "seat:" + self.seat_id
        with self.journal._lock:
            self.journal.append("seat_callback", {"decision_id": decision_id,
                                "callback": event.callback, "observation": event.observation,
                                "choice": event.choice}, seat_id=self.seat_id,
                                source=source, source_sequence=self.journal.sources.get(source, 0))
        self._pending.decision_id = None
