import unittest

from commander_gym.deck_package import ArtifactRef
from commander_gym.identity import Pilot
from commander_gym.pilot import ArgentumActionChoice, PilotContractError, choose_for_observation
from commander_gym.pilot_composition import (
    PILOT_ROUTING_MODE_STATIC_ORDER_V1,
    PilotComponentRegistry,
    SubsystemDecision,
    compose_pilot_runtime,
    subsystem_specs_for_pilot,
)


def ref(kind, artifact_id, version="1", digest=None):
    return ArtifactRef(
        kind=kind,
        artifact_id=artifact_id,
        version=version,
        digest=digest,
    )


def observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@fixture",
        "stateDigest": "state-1",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 7,
                "semanticId": "argentum-action-v1:fixture",
                "kind": "CastSpell",
                "description": "Fixture choice",
                "affordable": True,
            }
        ],
        "terminated": False,
    }


class FixtureSubsystem:
    def __init__(self, name, version, *, choice=None, reason=None, metadata=None):
        self.name = name
        self.version = version
        self.choice = choice
        self.reason = reason
        self.metadata = dict(metadata or {})
        self.calls = 0

    def try_choose(self, observation):
        self.calls += 1
        return SubsystemDecision(
            choice=self.choice,
            reason=self.reason,
            metadata=self.metadata,
        )


class PilotCompositionTests(unittest.TestCase):
    def pilot(self):
        return Pilot(
            pilot_id="fixture-pilot",
            revision="2026-09-24.1",
            deterministic_policy=ref("deterministic-policy", "mechanical", digest="aaa"),
            skill_set=ref("skill-set", "skills", digest="bbb"),
            specialists=(
                ref("learned-policy", "combat-specialist", digest="ccc"),
                ref("value-policy", "threat-specialist", digest="ddd"),
            ),
            generalist_base=ref("local-model", "magic-generalist", digest="eee"),
            escalation_provider=ref("provider", "frontier-teacher", digest="fff"),
            routing_config={"mode": PILOT_ROUTING_MODE_STATIC_ORDER_V1},
        )

    def test_manifest_components_define_one_canonical_static_order(self):
        specs = subsystem_specs_for_pilot(self.pilot())

        self.assertEqual(
            [spec.role for spec in specs],
            [
                "deterministic",
                "skill",
                "specialist",
                "specialist",
                "local_generalist",
                "frontier_escalation",
            ],
        )
        self.assertEqual([spec.ordinal for spec in specs], list(range(6)))
        self.assertEqual(specs[2].ref.artifact_id, "combat-specialist")
        self.assertEqual(specs[3].ref.artifact_id, "threat-specialist")

    def test_composed_pilot_preserves_artificial_player_contract_and_route_provenance(self):
        pilot = self.pilot()
        mechanical = FixtureSubsystem(
            "mechanical-runtime", "1", reason="not-certified-for-decision"
        )
        skills = FixtureSubsystem("skills-runtime", "3", reason="no-matching-skill")
        combat = FixtureSubsystem(
            "combat-runtime",
            "9",
            choice=ArgentumActionChoice(action_id=7),
            metadata={"policyFamily": "combat"},
        )
        threat = FixtureSubsystem("threat-runtime", "4", reason="not-reached")
        local = FixtureSubsystem("local-runtime", "2", reason="not-reached")
        frontier = FixtureSubsystem("frontier-runtime", "5", reason="not-reached")

        components = [mechanical, skills, combat, threat, local, frontier]
        specs = subsystem_specs_for_pilot(pilot)
        registry = PilotComponentRegistry(
            {
                PilotComponentRegistry.key_for(spec.ref): component
                for spec, component in zip(specs, components)
            }
        )
        runtime = compose_pilot_runtime(pilot, registry)

        choice = choose_for_observation(runtime, observation())

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(runtime.name, pilot.pilot_id)
        self.assertEqual(runtime.version, pilot.revision)
        self.assertEqual(mechanical.calls, 1)
        self.assertEqual(skills.calls, 1)
        self.assertEqual(combat.calls, 1)
        self.assertEqual(threat.calls, 0)
        self.assertEqual(local.calls, 0)
        self.assertEqual(frontier.calls, 0)

        routing = choice.metadata["routing"]
        self.assertEqual(routing["mode"], PILOT_ROUTING_MODE_STATIC_ORDER_V1)
        self.assertEqual(routing["pilotId"], pilot.pilot_id)
        self.assertEqual(routing["pilotRevision"], pilot.revision)
        self.assertEqual(routing["handledBy"]["role"], "specialist")
        self.assertEqual(
            routing["handledBy"]["component"]["artifactId"], "combat-specialist"
        )
        self.assertEqual(
            routing["escalationReasons"],
            ("not-certified-for-decision", "no-matching-skill"),
        )
        self.assertEqual(
            [attempt["status"] for attempt in routing["attempts"]],
            ["deferred", "deferred", "handled"],
        )
        self.assertEqual(
            routing["attempts"][1]["reason"],
            "no-matching-skill",
        )
        self.assertEqual(
            routing["attempts"][2]["metadata"]["policyFamily"],
            "combat",
        )
        self.assertNotIn("confidence", routing)

    def test_exact_component_identity_is_fail_closed(self):
        pilot = Pilot(
            pilot_id="fixture-pilot",
            revision="1",
            generalist_base=ref(
                "local-model", "magic-generalist", version="2", digest="expected"
            ),
        )
        registry = PilotComponentRegistry(
            {
                (
                    "local-model",
                    "magic-generalist",
                    "2",
                    "stale",
                ): FixtureSubsystem(
                    "stale-local", "2", choice=ArgentumActionChoice(action_id=7)
                )
            }
        )

        with self.assertRaisesRegex(PilotContractError, "missing exact pilot component"):
            compose_pilot_runtime(pilot, registry)

    def test_all_subsystems_deferring_fails_without_fabricating_choice(self):
        pilot = Pilot(
            pilot_id="fixture-pilot",
            revision="1",
            skill_set=ref("skill-set", "skills"),
            escalation_provider=ref("provider", "frontier"),
        )
        skill = FixtureSubsystem("skills", "1", reason="not-applicable")
        frontier = FixtureSubsystem("frontier", "1", reason="provider-disabled")
        specs = subsystem_specs_for_pilot(pilot)
        registry = PilotComponentRegistry(
            {
                PilotComponentRegistry.key_for(spec.ref): component
                for spec, component in zip(specs, (skill, frontier))
            }
        )
        runtime = compose_pilot_runtime(pilot, registry)

        with self.assertRaisesRegex(
            PilotContractError,
            "all configured pilot subsystems deferred",
        ):
            runtime.choose(observation())

        self.assertEqual(skill.calls, 1)
        self.assertEqual(frontier.calls, 1)

    def test_unknown_routing_mode_is_not_silently_reinterpreted(self):
        pilot = Pilot(
            pilot_id="fixture-pilot",
            revision="1",
            generalist_base=ref("local-model", "magic-generalist"),
            routing_config={"mode": "future-empirical-router"},
        )

        with self.assertRaisesRegex(PilotContractError, "unsupported pilot routing mode"):
            subsystem_specs_for_pilot(pilot)


if __name__ == "__main__":
    unittest.main()
