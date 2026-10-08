"""Offline, seat-visible card exposure metrics; no policy or training promotion."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from .game_journal import _immutable_private, _private_dir, verify_finalized_manifest


class CardOpportunityError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


PLAY_TYPES = {'CastSpell', 'CastSpellMode', 'CastSpellModal', 'PlayLand'}


def frame_from_callback(observation, choice, *, seat_id, context, evidence_ref, decision_id=None):
    """Normalize one masked game-server observation. Never inspect other seats' cards."""
    state = observation.get('state', {})
    if (observation.get('type') != 'GameServerSeat' or
            observation.get('perspectivePlayerId') != seat_id or
            state.get('viewingPlayerId') != seat_id or state.get('hotseat') or
            state.get('youAreHijacking') or state.get('youAreHijackedBy')):
        raise CardOpportunityError('unsupported or mismatched masked perspective')
    cards = []
    for zone in state.get('zones', []):
        zid = zone.get('zoneId', {})
        if zid.get('ownerId') != seat_id or zone.get('isVisible') is not True:
            continue
        for instance in zone.get('cardIds', []):
            card = state.get('cards', {}).get(instance, {})
            name = card.get('name')
            if not isinstance(name, str) or not name or card.get('faceDownMode'):
                continue
            definition = card.get('cardDefinitionId')
            cards.append(dict(instance_id=instance, identity=definition or name,
                              identity_basis='definition_id' if definition else 'visible_name',
                              name=name, in_hand=zid.get('zoneType') == 'HAND'))
    # Fingerprint only explicitly observable material, with logs and opponent cards removed.
    timing = ['turnNumber', 'currentPhase', 'currentStep', 'activePlayerId', 'priorityPlayerId']
    safe_state = {k: v for k, v in state.items() if k not in {'cards', 'zones', 'gameLog'}}
    safe_state['zones'] = [z for z in state.get('zones', []) if z.get('isVisible') is True and
        (z.get('zoneId', {}).get('ownerId') == seat_id or
         z.get('zoneId', {}).get('zoneType') in {'BATTLEFIELD', 'GRAVEYARD', 'EXILE', 'STACK', 'COMMAND'})]
    public_ids = {cid for z in safe_state['zones'] for cid in z.get('cardIds', [])}
    safe_state['cards'] = {cid: state.get('cards', {}).get(cid, {}) for cid in public_ids}
    window = _digest(safe_state) if all(state.get(k) is not None for k in timing) else None
    offers, selected = [], {'status': 'unknown', 'play_instance_id': None}
    actions = observation.get('legalActions', [])
    for offer in actions:
        action = offer.get('action', {})
        if action.get('type') in PLAY_TYPES and action.get('playerId') == seat_id:
            offers.append(dict(instance_id=action.get('cardId'), affordable=offer.get('isAffordable') if type(offer.get('isAffordable')) is bool else None,
                               action_id=offer.get('actionId')))
    if (not observation.get('pendingDecision') and isinstance(choice, dict) and
            choice.get('channel') == 'action'):
        matches = [a for a in actions if a.get('actionId') == choice.get('actionId')]
        if len(matches) == 1:
            action = matches[0].get('action', {})
            if action.get('playerId') == seat_id:
                is_play = action.get('type') in PLAY_TYPES
                selected = dict(status='known' if not is_play or action.get('cardId') else 'unknown',
                                play_instance_id=action.get('cardId') if is_play else None)
    return dict(seat_id=seat_id, context=deepcopy(context), window_key=window,
                turn=state.get('turnNumber'), observable_context={k: state.get(k) for k in timing}, cards=cards, offers=offers, selected=selected,
                evidence_refs=[deepcopy(evidence_ref)], decision_ids=[decision_id] if decision_id else [],
                structured=bool(observation.get('pendingDecision')),
                unattributed_play_offers=sum(o['instance_id'] not in {c['instance_id'] for c in cards} for o in offers))


def analyze_frames(frames, *, source, generator_revision):
    """Conservative distinct-observed-state denominator, grouped by seat and cohort."""
    if not re.fullmatch('[0-9a-f]{40}', generator_revision):
        raise CardOpportunityError('exact generator commit required')
    frames = list(frames)
    coverage = Counter(input_frames=len(frames))
    groups = {}
    for frame in frames:
        if frame.get('window_key') is None:
            coverage['window_identity_unavailable'] += 1
            continue
        if frame.get('structured'):
            coverage['structured_frames_excluded'] += 1
            continue
        key = (frame['seat_id'], _digest(frame['context']), frame['window_key'])
        groups.setdefault(key, []).append(frame)
    coverage['deduplicated_windows'] = len(groups)
    coverage['collapsed_frames'] = sum(len(v)-1 for v in groups.values())
    cards = {}
    all_turns = {}
    for (seat, cohort, window), copies in groups.items():
        turn = copies[0].get('turn')
        if type(turn) is int:
            all_turns.setdefault((seat, cohort), set()).add(turn)
        visible = {}
        for f in copies:
            for c in f['cards']:
                visible.setdefault((c['identity_basis'], c['identity']), []).append(c)
        choices = {f['selected'].get('play_instance_id') for f in copies if f['selected']['status'] == 'known'}
        choice_known = len(choices) == 1
        selected = next(iter(choices)) if choice_known else None
        refs = {_json(r): r for f in copies for r in f['evidence_refs']}
        for identity, instances in visible.items():
            ids = {c['instance_id'] for c in instances}
            offers = [o for f in copies for o in f['offers'] if o['instance_id'] in ids]
            affordable = {o['affordable'] for o in offers}
            if True in affordable:
                availability = 'native_playable'
            elif None in affordable or any(f.get('unattributed_play_offers') for f in copies):
                availability = 'playability_unknown'
            elif offers:
                availability = 'unaffordable_offer'
            else:
                availability = 'not_offered'
            key = (seat, cohort, identity)
            entry = cards.setdefault(key, dict(name=instances[0]['name'], identity=identity[1],
                identity_basis=identity[0], seat_id=seat, context=deepcopy(copies[0]['context']),
                windows=[], _instances=set()))
            entry['_instances'].update(ids)
            selection = ('selected_play' if selected in ids else 'held') if choice_known else 'choice_unknown'
            entry['windows'].append(dict(window_key=window, turn=turn, observable_context=copies[0].get('observable_context', {}),
                in_hand=any(c['in_hand'] for c in instances), availability=availability,
                selection=selection if availability == 'native_playable' else 'not_applicable',
                execution='unknown', evidence_refs=list(refs.values()),
                decision_ids=sorted({d for f in copies for d in f.get('decision_ids', [])})))
    result = []
    for (seat, cohort, _), entry in sorted(cards.items()):
        counts = Counter()
        for w in entry['windows']:
            counts['seen_windows'] += 1
            counts['in_hand_windows'] += int(w['in_hand'])
            counts[w['availability']+'_windows'] += 1
            if w['selection'] != 'not_applicable':
                counts[w['selection']+'_windows'] += 1
        for name in ['seen', 'in_hand', 'native_playable', 'unaffordable_offer', 'not_offered',
                     'playability_unknown', 'selected_play', 'held', 'choice_unknown']:
            counts.setdefault(name+'_windows', 0)
        played = counts['selected_play_windows']
        denominator = played + counts['held_windows']
        hand_turns = sorted({w['turn'] for w in entry['windows'] if w['in_hand'] and type(w['turn']) is int})
        playable_turns = sorted({w['turn'] for w in entry['windows'] if w['in_hand'] and
                                w['availability'] == 'native_playable' and type(w['turn']) is int})
        unaffordable_turns = sorted({w['turn'] for w in entry['windows'] if w['in_hand'] and
                                    w['availability'] == 'unaffordable_offer' and type(w['turn']) is int})
        last = max(all_turns.get((seat, cohort), []), default=None)
        entry.update(counts=dict(counts), unique_instances_seen=len(entry.pop('_instances')),
            play_selection_rate=dict(numerator=played, denominator=denominator,
                                     value=played/denominator if denominator else None,
                                     unknown_choices=counts['choice_unknown_windows']),
            usefulness={basis: dict(helpful=0, neutral=0, harmful=0, unknown=played)
                        for basis in ['ex_ante_choice', 'observed_effect']},
            exposure=dict(observed_hand_turns=hand_turns, observed_playable_hand_turns=playable_turns,
                observed_unaffordable_hand_turns=unaffordable_turns,
                first_seen_hand_turn=hand_turns[0] if hand_turns else None,
                last_observed_seat_turn=last,
                observed_turns_after_first_seen=sum(t>hand_turns[0] for t in all_turns.get((seat,cohort), [])) if hand_turns else None,
                draw_turn=None, turns_held=None, turns_remaining_after_draw=None,
                complete_turns=False, end_censoring='last_observation_only'))
        result.append(entry)
    return dict(kind='commander-gym.card-opportunities', schema_version=1,
                generator_revision=generator_revision, source=deepcopy(source), coverage=dict(coverage),
                unit='seat/cohort/distinct observed state/card identity', cards=result,
                limitations=['Native legality does not imply strategic value.',
                    'Selection does not establish execution or helpfulness.',
                    'Observed hand turns are lower bounds; draw time and full turns are not reconstructed.',
                    'Visible-name grouping does not prove deck membership.',
                    'Outcomes are provenance only, never helpfulness or pilot input.'])


def write_report(run_directory, report):
    directory = Path(run_directory) / 'analysis' / 'card-opportunities-v1'
    _private_dir(Path(run_directory))
    _private_dir(Path(run_directory) / 'analysis', create=True)
    _private_dir(directory, create=True)
    data = _json(report)+b'\n'
    path = directory / ('report-'+hashlib.sha256(data).hexdigest()+'.json')
    if path.exists() and (path.is_symlink() or path.stat().st_mode & 0o077):
        raise CardOpportunityError('existing report must remain private')
    _immutable_private(path, data)
    return path


def analyze_run(directory, *, generator_revision):
    """Verify immutable sources; analyze masked callbacks only; never join admin transitions."""
    directory = Path(directory)
    manifest = verify_finalized_manifest(directory)
    frames, skipped = [], Counter()
    rows = []
    for artifact in manifest['artifacts']:
        if artifact['role'] == 'admin_journal':
            rows.extend((artifact, line, json.loads(raw)) for line, raw in
                enumerate((directory/artifact['path']).read_bytes().splitlines(), 1))
    pins = rows[0][2]['payload'].get('pins', {})
    callback_seats = {r['seat_id'] for _, _, r in rows if r['kind'] in
        {'seat_callback', 'decision_started'} and r['payload'].get('observation', {}).get('type') == 'GameServerSeat'}
    for artifact, line, row in rows:
        payload = row['payload']
        if row['kind'] in {'seat_callback', 'decision_started'}:
            observation, choice = payload.get('observation', {}), payload.get('choice')
        elif row['kind'] == 'native_seat_observation':
            body = payload['native']
            if body.get('kind') != 'seat_observation' or row['seat_id'] in callback_seats:
                skipped['native_source_not_selected'] += 1
                continue
            native = body['payload']
            observation = dict(type='GameServerSeat', perspectivePlayerId=row['seat_id'],
                state=native.get('state', {}), legalActions=native.get('legalActions', []),
                pendingDecision=native.get('pendingDecision'))
            choice = None
        else:
            skipped[row['kind']] += 1
            continue
        if observation.get('type') != 'GameServerSeat':
            skipped['unsupported_observation'] += 1
            continue
        try:
            frames.append(frame_from_callback(observation, choice, seat_id=row['seat_id'],
                context=dict(pins=pins, gym_revision=payload.get('gym_revision')),
                evidence_ref=dict(path=artifact['path'], sha256=artifact['sha256'], line=line),
                decision_id=payload.get('decision_id')))
        except CardOpportunityError:
            skipped['unsupported_perspective'] += 1
    report = analyze_frames(frames, source={k: manifest[k] for k in
        ['run_id', 'artifact_hash', 'recording_complete', 'gaps', 'outcome']}, generator_revision=generator_revision)
    report['coverage']['excluded_source_rows'] = dict(skipped)
    if verify_finalized_manifest(directory) != manifest:
        raise CardOpportunityError('source changed during analysis')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--generator-revision', required=True)
    args = parser.parse_args()
    report = analyze_run(args.run_directory, generator_revision=args.generator_revision)
    path = write_report(args.run_directory, report)
    print(json.dumps(dict(report_path=str(path.relative_to(args.run_directory)), coverage=report['coverage'])))



def frame_from_decision(decision, *, evidence_ref):
    """Canonical Gym adapter: sourceEntityId is required, labels never identify cards."""
    inp, provenance = decision['input'], decision['provenance']
    obs = inp['observation']
    seat = obs.get('perspectivePlayerId')
    if obs.get('type') != 'Game' or not isinstance(seat, str):
        raise CardOpportunityError('unsupported canonical observation')
    cards = []
    for zone in obs.get('zones', []):
        if zone.get('ownerId') != seat or zone.get('hidden') is not False:
            continue
        for card in zone.get('cards', []):
            definition = card.get('cardDefinitionId')
            if not definition:
                continue
            cards.append(dict(instance_id=card['entityId'], identity=definition,
                identity_basis='definition_id', name=card.get('name', definition),
                in_hand=zone.get('zoneType') == 'HAND'))
    offers = []
    chosen = decision['target'].get('chosen_action_id')
    selected = dict(status='unknown', play_instance_id=None)
    legal = inp.get('legal_actions', [])
    for offer in legal:
        payload = offer['payload']
        play = payload.get('kind') in PLAY_TYPES
        instance = payload.get('sourceEntityId')
        if play:
            offers.append(dict(instance_id=instance,
                affordable=payload.get('affordable') if type(payload.get('affordable')) is bool else None,
                action_id=offer['action_id']))
        if chosen == offer['action_id']:
            selected = dict(status='known' if not play or instance else 'unknown',
                            play_instance_id=instance if play else None)
    digest = obs.get('stateDigest')
    return dict(seat_id=seat, context={k: provenance.get(k) for k in
        ['pilot', 'deck_id', 'deck_version', 'primer_version', 'binding']},
        window_key=digest if isinstance(digest, str) and digest else None,
        turn=obs.get('turnNumber'), cards=cards, offers=offers, selected=selected,
        structured='response' in decision['target'], evidence_refs=[evidence_ref],
        decision_ids=[provenance['decision_id']],
        unattributed_play_offers=sum(o['instance_id'] not in {c['instance_id'] for c in cards} for o in offers))


def _pointer(value, pointer):
    if (not isinstance(pointer, str) or not pointer.startswith('/') or
            re.search(r'~(?![01])', pointer)):
        raise CardOpportunityError('evidence pointer must be an absolute JSON pointer')
    try:
        for part in pointer[1:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise CardOpportunityError('unresolved evidence pointer') from exc
    return value


def analyze_artifact(layout, source_artifact_id, *, annotation_artifact_ids=(), generator_revision):
    """Join existing immutable AnnotationStore artifacts to the exact raw source blob.

    card_usefulness.v1 payload: card_definition_id, basis, label, rationale,
    confidence, counterevidence, evidence_pointers. Judgments remain derived data.
    """
    from .annotations import AnnotationStore
    from .evidence import validate_raw_evidence_envelope
    from .storage import LocalArtifactStore
    envelope = json.loads(LocalArtifactStore(layout).read_bytes(source_artifact_id))
    validate_raw_evidence_envelope(envelope)
    frames = [frame_from_decision(d, evidence_ref=dict(artifact_id=source_artifact_id,
        pointer=f'/decisions/{i}/input')) for i, d in enumerate(envelope['decisions'])]
    report = analyze_frames(frames, source=dict(artifact_id=source_artifact_id,
        run=envelope['run']), generator_revision=generator_revision)
    judgments = {}
    records = {d['provenance']['decision_id']: (i, d) for i, d in enumerate(envelope['decisions'])}
    for artifact in sorted(set(annotation_artifact_ids)):
        annotation = AnnotationStore(layout).read_artifact(artifact)
        if (annotation.source_evidence_artifact_id != source_artifact_id or
                annotation.target_kind != 'decision' or annotation.target_id not in records or
                annotation.annotation_type != 'card_usefulness.v1'):
            raise CardOpportunityError('usefulness annotation source/type/target mismatch')
        i, decision = records[annotation.target_id]
        p = annotation.payload
        basis, label = p.get('basis'), p.get('label')
        if (basis not in {'ex_ante_choice', 'observed_effect'} or
                label not in {'helpful', 'neutral', 'harmful', 'unknown'} or
                not isinstance(p.get('rationale'), str) or not p['rationale'] or
                type(p.get('confidence')) not in {int, float} or not 0 <= p['confidence'] <= 1 or
                not isinstance(p.get('counterevidence'), list) or
                not isinstance(p.get('evidence_pointers'), list) or not p['evidence_pointers']):
            raise CardOpportunityError('invalid usefulness judgment')
        allowed = [f'/decisions/{i}/input/', f'/decisions/{i}/target/'] if basis == 'ex_ante_choice' else [f'/decisions/{i}/provenance/outcome/']
        # Targets are selections only; rationale/model output and future observations are excluded.
        if basis == 'ex_ante_choice':
            allowed = [f'/decisions/{i}/input/observation/', f'/decisions/{i}/input/legal_actions/',
                       f'/decisions/{i}/target/chosen_action_id']
        for pointer in p['evidence_pointers']:
            if not isinstance(pointer, str) or not any(
                    pointer.startswith(prefix) if prefix.endswith('/') else pointer == prefix
                    for prefix in allowed):
                raise CardOpportunityError('usefulness evidence violates basis boundary')
            _pointer(envelope, pointer)
        frame = frames[i]
        selected = frame['selected']
        ids = {c['identity'] for c in frame['cards'] if c['instance_id'] == selected['play_instance_id']}
        if selected['status'] != 'known' or p.get('card_definition_id') not in ids:
            raise CardOpportunityError('usefulness must identify the selected visible card')
        key = (frame['seat_id'], _digest(frame['context']), frame['window_key'], p['card_definition_id'], basis)
        judgments.setdefault(key, []).append(dict(artifact_id=artifact, label=label,
            annotation=annotation.to_dict()))
    for card in report['cards']:
        for window in card['windows']:
            if window['selection'] != 'selected_play':
                continue
            for basis in card['usefulness']:
                key = (card['seat_id'], _digest(card['context']), window['window_key'], card['identity'], basis)
                entries = judgments.get(key, [])
                labels = {a['label'] for a in entries}
                label = next(iter(labels)) if len(labels) == 1 else 'unknown'
                window.setdefault('usefulness_annotations', {})[basis] = entries
                card['usefulness'][basis]['unknown'] -= 1
                card['usefulness'][basis][label] += 1
    return report


if __name__ == "__main__":
    main()
