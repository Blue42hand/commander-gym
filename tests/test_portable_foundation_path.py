from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from commander_gym.binding_resolver import BindingResolver
from commander_gym.binding_session import run_binding_full_game
from commander_gym.dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestStore,
    DatasetSplit,
)
from commander_gym.deck_package import ArtifactRef
from commander_gym.evidence import IDENTITY_EPOCH_BINDING_V1, RawEvidenceStore
from commander_gym.identity import Binding, Deck, DeckKnowledge, Pilot
from commander_gym.manifest_export import (
    build_manifest_training_rows,
    write_manifest_training_jsonl,
)
from commander_gym.orchestration import ArgentumOrchestrator
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_routing import RoutingPilot
from commander_gym.run_records import RUN_STATUS_COMPLETED
from commander_gym.storage import LocalArtifactStore, StorageLayout


class StrategicFixturePilot:
    name = "portable-foundation-strategic-fixture"
    version = "1"

    def choose(self, observation: Mapping[str, Any]) -> ArgentumActionChoice:
        return ArgentumActionChoice(observation["legalActions"][0]["actionId"])


class PortableFoundationBackend:
    """Small authoritative Argentum fixture for the cross-contract qualification."""

    def __init__(self) -> None:
        self.ids = [f"p-{index}" for index in range(4)]
        self.names = [f"Seat {index}" for index in range(4)]
        self.index = 0
        self.envs: set[str] = set()
        self.created_config: Mapping[str, Any] | None = None

    def health(self) -> Mapping[str, Any]:
        return {"status": "ok"}

    def status(self) -> Mapping[str, Any]:
        return {
            "service": "argentum-gym-server",
            "schemaHash": "schema-portable-foundation-v1",
            "buildRevision": "build-portable-foundation-v1",
        }

    def schema_hash(self) -> Mapping[str, Any]:
        return {"schemaHash": "schema-portable-foundation-v1"}

    def list_envs(self) -> Sequence[str]:
        return sorted(self.envs)

    def create_env(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        self.created_config = dict(config)
        self.envs.add("env-portable-foundation")
        return {"envId": "env-portable-foundation"}

    def observe_env(
        self,
        env_id: str,
        *,
        reveal_all: bool | None = None,
        perspective_player_id: str | None = None,
    ) -> Mapping[str, Any]:
        return self._observation(perspective_player_id)

    def step_env(
        self,
        env_id: str,
        action_id: int,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if action_id != 100 + self.index:
            raise AssertionError("stale action")
        self.index += 1
        return self._observation(self.ids[0])

    def submit_decision(
        self,
        env_id: str,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise AssertionError("structured decisions are not used by this fixture")

    def dispose_envs(self, env_ids: Sequence[str]) -> None:
        self.envs.difference_update(env_ids)

    def _observation(self, perspective: str | None) -> Mapping[str, Any]:
        terminal = self.index >= 4
        agent = None if terminal else self.ids[self.index % 4]
        return {
            "type": "Game",
            "schemaHash": "schema-portable-foundation-v1",
            "stateDigest": f"state-{self.index}-{perspective}",
            "perspectivePlayerId": perspective,
            "agentToAct": agent,
            "players": [
                {"id": player_id, "name": name}
                for player_id, name in zip(self.ids, self.names)
            ],
            "pendingDecision": None,
            "legalActions": []
            if terminal
            else [
                {
                    "actionId": 100 + self.index,
                    "semanticId": f"pass-{self.index}",
                    "kind": "PassPriority",
                    "description": "Pass priority",
                    "affordable": True,
                }
            ],
            "terminated": terminal,
            "winnerId": self.ids[0] if terminal else None,
        }


def binding_resolver() -> BindingResolver:
    decks: list[Deck] = []
    knowledge: list[DeckKnowledge] = []
    pilots: list[Pilot] = []
    bindings: list[Binding] = []
    payloads: dict[str, Mapping[str, Any]] = {}

    for index in range(4):
        deck_artifact = ArtifactRef(
            kind="decklist",
            artifact_id=f"portable-deck-payload-{index}",
            version="v1",
            digest=f"sha256:{index + 1:064x}",
        )
        deck = Deck(
            deck_id=f"portable-deck-{index}",
            revision="r1",
            format_id="commander",
            deck_artifact=deck_artifact,
            format_metadata={"commander": f"Commander {index}"},
        )
        knowledge_artifact = ArtifactRef(
            kind="deck-knowledge",
            artifact_id=f"portable-knowledge-payload-{index}",
            version="v1",
            digest=f"sha256:{index + 101:064x}",
        )
        deck_knowledge = DeckKnowledge(
            knowledge_id=f"portable-knowledge-{index}",
            revision="r1",
            content=knowledge_artifact,
        )
        pilot = Pilot(pilot_id=f"portable-pilot-{index}", revision="r1")
        binding = Binding(
            binding_id=f"portable-binding-{index}",
            revision="r1",
            deck=deck.ref(),
            deck_knowledge=deck_knowledge.ref(),
            pilot=pilot.ref(),
            metadata={"display": {"player_name": f"Seat {index}"}},
        )
        payloads[deck_artifact.artifact_id] = {
            "schema_version": 1,
            "deck_id": deck.deck_id,
            "name": f"Portable Deck {index}",
            "commander": f"Commander {index}",
            "cards": {
                f"Commander {index}": 1,
                "Forest": 99,
            },
        }
        decks.append(deck)
        knowledge.append(deck_knowledge)
        pilots.append(pilot)
        bindings.append(binding)

    return BindingResolver(
        bindings=bindings,
        decks=decks,
        deck_knowledge=knowledge,
        pilots=pilots,
        deck_payload_loader=lambda ref: payloads[ref.artifact_id],
        pilot_factory=lambda pilot, binding: RoutingPilot(StrategicFixturePilot()),
    )


class PortableFoundationPathTests(unittest.TestCase):
    def test_binding_game_evidence_dataset_survive_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            first_root = workspace / "host-a" / "deploy-one" / "data-root"
            first_layout = StorageLayout.create(first_root)
            first_layout.ensure_directories()

            backend = PortableFoundationBackend()
            result = run_binding_full_game(
                ArgentumOrchestrator(backend),
                binding_resolver(),
                [f"portable-binding-{index}" for index in range(4)],
                {
                    "format": {"type": "com.wingedsheep.sdk.core.Format.Commander"},
                    "skipMulligans": False,
                    "revealAll": False,
                },
                run_id="portable-foundation-run-1",
                max_choices=8,
            )

            self.assertEqual(result.run.termination.status, RUN_STATUS_COMPLETED)
            self.assertEqual(len(result.decisions), 4)
            self.assertEqual(backend.envs, set())
            self.assertIsNotNone(backend.created_config)
            players = backend.created_config["players"]
            self.assertEqual(
                [player["commanderCardName"] for player in players],
                [f"Commander {index}" for index in range(4)],
            )
            self.assertEqual(
                [participant.binding.artifact_id for participant in result.run.participants],
                [f"portable-binding-{index}" for index in range(4)],
            )
            self.assertTrue(all(record.binding is not None for record in result.decisions))

            evidence_store = RawEvidenceStore(first_layout)
            evidence_write = evidence_store.write(
                result.run,
                result.decisions,
                commander_gym_revision="portable-foundation-qualification",
            )
            evidence_artifact_id = evidence_write.artifact.artifact_id
            self.assertEqual(evidence_write.identity_epoch, IDENTITY_EPOCH_BINDING_V1)
            self.assertEqual(evidence_write.accounting.decision_count, 4)
            self.assertGreater(evidence_write.accounting.raw_bytes, 0)

            decision_ids = tuple(result.run.decision_ids)
            manifest = DatasetManifest(
                dataset_id="portable-foundation-dataset",
                version="r1",
                purpose="public end-to-end portability qualification",
                created_at="2026-09-24T03:30:00Z",
                source_population="one completed binding-v1 qualification run",
                source_query={
                    "qualification": "completed",
                    "identity_epoch": IDENTITY_EPOCH_BINDING_V1,
                },
                selection_rules={"run_id": result.run.run_id},
                exclusion_rules={"technical_failures": True},
                required_annotation_artifact_ids=(),
                transformations=(
                    {"name": "input-target-provenance", "version": 1},
                ),
                splits=(
                    DatasetSplit(
                        name="train",
                        role="train",
                        selections=(
                            DatasetEvidenceSelection(
                                evidence_artifact_id,
                                decision_ids,
                            ),
                        ),
                    ),
                ),
                generator_revision="portable-foundation-qualification",
            )
            manifest_write = DatasetManifestStore(first_layout).write(manifest)
            manifest_artifact_id = manifest_write.artifact.artifact_id

            first_rows = build_manifest_training_rows(
                first_layout,
                manifest_artifact_id,
            )
            self.assertEqual(len(first_rows), 4)
            self.assertEqual(
                [row["provenance"]["decision_id"] for row in first_rows],
                list(decision_ids),
            )
            self.assertTrue(
                all(
                    row["provenance"]["binding"]["artifact_id"].startswith(
                        "portable-binding-"
                    )
                    for row in first_rows
                )
            )

            first_export = first_layout.path("datasets") / "portable-foundation.jsonl"
            write_manifest_training_jsonl(
                first_export,
                first_layout,
                manifest_artifact_id,
            )
            expected_export = first_export.read_bytes()

            moved_root = workspace / "host-b" / "different-deployment" / "moved-data"
            moved_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(first_root, moved_root)
            moved_layout = StorageLayout.create(moved_root)

            self.assertNotEqual(
                first_layout.path("artifacts"),
                moved_layout.path("artifacts"),
            )
            first_artifacts = LocalArtifactStore(first_layout)
            moved_artifacts = LocalArtifactStore(moved_layout)
            self.assertEqual(
                moved_artifacts.read_bytes(evidence_artifact_id),
                first_artifacts.read_bytes(evidence_artifact_id),
            )
            self.assertEqual(
                moved_artifacts.read_bytes(manifest_artifact_id),
                first_artifacts.read_bytes(manifest_artifact_id),
            )

            moved_evidence = RawEvidenceStore(moved_layout).read(result.run.run_id)
            self.assertEqual(moved_evidence, evidence_store.read(result.run.run_id))
            moved_manifest = DatasetManifestStore(moved_layout).read(
                dataset_id=manifest.dataset_id,
                version=manifest.version,
            )
            self.assertEqual(moved_manifest.dataset_digest, manifest.dataset_digest)

            moved_rows = build_manifest_training_rows(
                moved_layout,
                manifest_artifact_id,
            )
            self.assertEqual(moved_rows, first_rows)
            moved_export = moved_layout.path("datasets") / "portable-foundation.jsonl"
            write_manifest_training_jsonl(
                moved_export,
                moved_layout,
                manifest_artifact_id,
            )
            self.assertEqual(moved_export.read_bytes(), expected_export)


if __name__ == "__main__":
    unittest.main()
