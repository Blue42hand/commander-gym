"""Trusted code registrations for optional deck overlays, never catalog-provided code.

Add a DefaultPolicySubsystem subclass, a reviewed data profile, and one registration.
An unmatched deck/primer pair always uses the shared defaults and bounded fallback.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import hashlib
from .deck_package import ArtifactRef
from .heuristic_facts import canonical
from .default_policies import DefaultPolicySubsystem
from .heuristic_facts import heuristic_component_ref
from .pilot import PilotContractError


@dataclass(frozen=True)
class DeckPolicyRegistration:
    policy_id: str
    deck_fingerprint: str
    primer_fingerprint: str
    factory: Callable
    component_ref: Callable

    def matches(self, binding):
        return (binding.deck.fingerprint == self.deck_fingerprint and binding.deck_knowledge is not None
                and binding.deck_knowledge.fingerprint == self.primer_fingerprint)


def registered_deck_policies():
    from .lathril_policy import LathrilDeckSubsystem, lathril_component_ref, policy_document
    doc = policy_document()
    from .engine_deck_policy import EngineDeckPolicy, engine_deck_component_ref, deck_profile
    additional = []
    for key in ['talrand','sythis','alela']:
        profile = deck_profile(key)
        additional.append(DeckPolicyRegistration(profile['policy_id'],profile['deck_fingerprint'],profile['primer_fingerprint'],
                          lambda facts, key=key: EngineDeckPolicy(facts,key),
                          lambda facts, key=key: engine_deck_component_ref(facts,key)))
    return (DeckPolicyRegistration(doc['policy_id'], doc['deck_fingerprint'], doc['primer_fingerprint'],
                                   LathrilDeckSubsystem, lathril_component_ref), *additional)


def select_deck_policy(binding):
    matches = [p for p in registered_deck_policies() if p.matches(binding)]
    if len(matches) > 1: raise PilotContractError('ambiguous deck policy registration')
    return matches[0] if matches else None


def resolve_policy_component(ref, facts=None):
    if ref == heuristic_component_ref(facts): return DefaultPolicySubsystem(facts)
    for registration in registered_deck_policies():
        if ref == registration.component_ref(facts): return registration.factory(facts)
    raise PilotContractError('missing exact registered policy component')


def validate_policy_binding(ref, binding, facts=None):
    if ref is None:
        if facts is not None: raise PilotContractError('facts-backed defaults require a pinned policy skill')
        return
    if ref == heuristic_component_ref(facts): return
    matching = [r for r in registered_deck_policies() if ref == r.component_ref(facts)]
    if not matching: raise PilotContractError('missing exact registered policy component')
    if not any(r.matches(binding) for r in matching):
        raise PilotContractError('deck policy requires its exact reviewed deck and primer')


def profile_component_ref(facts, profile, *, sources=()):
    """Pin a future deck's data profile and optional custom hook files."""
    payload = {'defaults': heuristic_component_ref(facts).digest, 'profile': profile,
               'sources': {str(Path(p).name):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}}
    return ArtifactRef(kind='skill-set', artifact_id=profile['policy_id'], version=str(profile.get('version','1')),
                       digest='sha256:'+hashlib.sha256(canonical(payload)).hexdigest())
