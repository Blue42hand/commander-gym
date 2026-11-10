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
    def factory(pilot, binding):
        if (pilot.generalist_base != local_component_ref(inference) or pilot.escalation_provider is not None
            or pilot.skill_set is not None or pilot.specialists):
            raise PilotContractError('Local catalog requires the exact configured local provider')
        compose_pilot_runtime(pilot, LocalBindingComponentResolver(inference))
        return _RecordingPilot(pilot)
    loaded = load_binding_catalog(catalog, instance_root=root, pilot_factory=factory)
    return GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)


def prepare_local_catalog(catalog, instance_root, output, inference):
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
    suffix = local_component_ref(inference).digest.split(':')[1][:16]
    pilot = Pilot(pilot_id='llmster-'+suffix, revision='1', generalist_base=local_component_ref(inference),
                  deterministic_policy=BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF)
    document('local/pilot.json', pilot.to_dict())
    originals = [Binding.from_dict(json.loads(_inside_root(root, name, 'binding').read_text()))
                 for name in raw['bindings']]
    paths, ids = [], []
    for index, binding_id in enumerate(loaded.binding_ids):
        resolved = loaded.resolver.resolve(binding_id)
        original = next(binding for binding in originals if binding.ref() == resolved.binding)
        display = {**original.metadata.get('display', {}), 'name':inference.model+' · '+original.metadata.get('display', {}).get('name', binding_id)}
        binding = replace(original, binding_id=binding_id+'-local-'+suffix, revision='1', pilot=pilot.ref(),
                          metadata={**original.metadata, 'display':display})
        name = f'local/binding-{index}.json'
        document(name, binding.to_dict()); paths.append(name); ids.append(binding.binding_id)
    raw.update(bindings=paths, pilots=['local/pilot.json'], active_bindings=ids)
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
    args = parser.parse_args()
    config = LMStudioConfig(**json.loads(args.inference_config.read_text()))
    registry = prepare_local_catalog(args.catalog, args.instance_root, args.output, config)
    print(json.dumps({'bindings':registry.binding_fingerprints, 'pilotFingerprints':sorted(registry.pilot_fingerprints)}))


if __name__ == '__main__':
    main()
