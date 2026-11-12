"""Prepare explicit llmster Bindings from an existing validated deck catalog.

Writes a new directory only. Makes no model calls and never changes the source.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from .binding_catalog import load_binding_catalog, _inside_root
from .game_server_binding_lmstudio_sidecar import local_component_ref
from .game_server_binding_openai_sidecar import BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF
from .game_server_bindings import GameServerBindingRegistry
from .identity import Binding, Pilot
from .recorder_bridge import _RecordingPilot
from .pilot import PilotContractError
from .lmstudio_provider import LMStudioConfig


def validate_local_catalog(catalog, root, inference):
    """Validate exact local components without opening listeners or making calls."""
    from .game_server_binding_lmstudio_sidecar import LocalBindingComponentResolver
    from .pilot_composition import compose_pilot_runtime
    from .heuristic_facts import CardFacts
    facts_path = Path(root) / 'local' / 'card-facts.json'
    facts = CardFacts.load(facts_path) if facts_path.exists() else None
    def factory(pilot, binding):
        from .deck_policy_registry import validate_policy_binding
        validate_policy_binding(pilot.skill_set, binding, facts)
        if (pilot.generalist_base != local_component_ref(inference) or pilot.escalation_provider is not None
            or pilot.specialists):
            raise PilotContractError('Local catalog requires the exact configured local provider')
        compose_pilot_runtime(pilot, LocalBindingComponentResolver(inference, card_facts=facts))
        return _RecordingPilot(pilot)
    loaded = load_binding_catalog(catalog, instance_root=root, pilot_factory=factory)
    return GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)


def prepare_local_catalog(catalog, instance_root, output, inference, *, card_facts=None, lathril_policy=None):
    root = Path(instance_root).resolve()
    source = Path(catalog).resolve()
    # Validate references and artifact payload hashes before copying anything.
    loaded = load_binding_catalog(source, instance_root=root, pilot_factory=lambda pilot, _: _RecordingPilot(pilot))
    raw = json.loads(source.read_text())
    output = Path(output)
    if output.resolve().is_relative_to(root):
        raise ValueError('Output must be outside the source catalog root')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {}
    def write(name, content):
        target = output/name
        directory = output
        for part in Path(name).parts[:-1]:
            directory = directory/part
            directory.mkdir(mode=0o700, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o600)
        files[name] = hashlib.sha256(content).hexdigest()
    def document(name, value):
        write(name, json.dumps(value, sort_keys=True, separators=(',',':')).encode())
    # Preserve exact Deck and knowledge bytes and their identity digests.
    for entry in raw.get('decks', []) + raw.get('deck_knowledge', []):
        for key in ('manifest','payload'):
            name = entry[key]
            source_path = _inside_root(root, name, key)
            # Use normalized relative paths in the new snapshot.
            entry[key] = str(source_path.relative_to(root))
            write(entry[key], source_path.read_bytes())
    from .heuristic_facts import CardFacts, heuristic_component_ref
    facts = CardFacts.load(card_facts) if card_facts is not None else None
    skill = heuristic_component_ref(facts)
    if facts is not None:
        document('local/card-facts.json', facts.document())
    provider_digest = local_component_ref(inference).digest
    suffix = (hashlib.sha256((provider_digest + skill.digest).encode()).hexdigest()[:16]
              if skill else provider_digest.split(':')[1][:16])
    pilot = Pilot(pilot_id='llmster-'+suffix, revision='1', generalist_base=local_component_ref(inference),
                  deterministic_policy=BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF,
                  skill_set=skill, compatibility={'engine': facts.engine_revision} if facts else {})
    document('local/pilot.json', pilot.to_dict())
    originals = [Binding.from_dict(json.loads(_inside_root(root, name, 'binding').read_text()))
                 for name in raw['bindings']]
    paths, ids = [], []
    pilot_paths = ['local/pilot.json']
    deck_pilots = {}
    from .deck_policy_registry import select_deck_policy
    for index, binding_id in enumerate(loaded.binding_ids):
        resolved = loaded.resolver.resolve(binding_id)
        original = next(binding for binding in originals if binding.ref() == resolved.binding)
        display = {**original.metadata.get('display', {}), 'name':inference.model+' · '+original.metadata.get('display', {}).get('name', binding_id)}
        selected_pilot, selected_suffix = pilot, suffix
        registration = select_deck_policy(original)
        if registration is not None:
            deck_skill = registration.component_ref(facts)
            deck_suffix = hashlib.sha256((provider_digest + deck_skill.digest).encode()).hexdigest()[:16]
            if registration.policy_id not in deck_pilots:
                deck_pilot = replace(pilot, pilot_id=registration.policy_id+'-'+deck_suffix, skill_set=deck_skill)
                policy_path = 'local/'+registration.policy_id+'-pilot.json'
                document(policy_path, deck_pilot.to_dict()); pilot_paths.append(policy_path)
                deck_pilots[registration.policy_id] = deck_pilot
            selected_pilot, selected_suffix = deck_pilots[registration.policy_id], deck_suffix
            display['name'] += ' · '+registration.policy_id
        binding = replace(original, binding_id=binding_id+'-local-'+selected_suffix, revision='1', pilot=selected_pilot.ref(),
                          metadata={**original.metadata, 'display':display})
        name = f'local/binding-{index}.json'
        document(name, binding.to_dict()); paths.append(name); ids.append(binding.binding_id)
    raw.update(bindings=paths, pilots=pilot_paths, active_bindings=ids)
    document('catalog.json', raw)
    document('inference.json', asdict(inference))
    registry = validate_local_catalog(output/'catalog.json', output, inference)
    document('manifest.json', {'provider':'lmstudio', 'model':inference.model, 'profiles':ids, 'files':dict(files)})
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--instance-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--inference-config', required=True, type=Path)
    parser.add_argument('--card-facts', type=Path,
                        help='Optional pinned native facts; defaults apply to every deck without this flag')
    parser.add_argument('--lathril-policy', action='store_true', help='Compatibility alias; matching registered deck policies are selected automatically')
    args = parser.parse_args()
    config = LMStudioConfig(**json.loads(args.inference_config.read_text()))
    registry = prepare_local_catalog(args.catalog, args.instance_root, args.output, config, card_facts=args.card_facts, lathril_policy=args.lathril_policy)
    print(json.dumps({'bindings':registry.binding_fingerprints, 'pilotFingerprints':sorted(registry.pilot_fingerprints)}))


if __name__ == '__main__':
    main()
