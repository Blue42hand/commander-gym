"""Offline synthetic card availability/usefulness evidence tests."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from commander_gym.card_opportunities import analyze_frames, frame_from_callback, write_report, CardOpportunityError


def callback(state=None, actions=None, choice=None):
    state = state or dict(viewingPlayerId='a', turnNumber=1, currentPhase='MAIN', currentStep='PRECOMBAT_MAIN',
        activePlayerId='a', priorityPlayerId='a', isGameOver=False,
        cards={'c1': {'id':'c1','name':'Synthetic Draw'}, 'c2':{'id':'c2','name':'Synthetic Shield'},
               'secret':{'id':'secret','name':'OPPONENT_SECRET'}},
        zones=[{'zoneId':{'ownerId':'a','zoneType':'HAND'},'cardIds':['c1','c2'],'isVisible':True},
               {'zoneId':{'ownerId':'b','zoneType':'HAND'},'cardIds':['secret'],'isVisible':False}])
    actions = actions or [dict(actionId=0, actionType='PassPriority', isAffordable=True, action={'type':'PassPriority','playerId':'a'}),
                         dict(actionId=1, actionType='CastSpell', isAffordable=True, action={'type':'CastSpell','playerId':'a','cardId':'c1'})]
    obs=dict(type='GameServerSeat', perspectivePlayerId='a',agentToAct='a',state=state,legalActions=actions,pendingDecision=None)
    return frame_from_callback(obs, choice, seat_id='a', context={'deck_revision':None},
        evidence_ref={'path':'000000.jsonl','line':2}, decision_id='choice-1')


def report(*frames):
    return analyze_frames(frames, source={'run_id':'synthetic','artifact_hash':'a'*64}, generator_revision='b'*40)


class CardOpportunityTests(unittest.TestCase):
    def card(self, result, name='Synthetic Draw'):
        return next(c for c in result['cards'] if c['name']==name)

    def test_seen_is_not_playable_and_unknown_choice_is_not_hold(self):
        r=report(callback())
        c=self.card(r)
        self.assertEqual(1,c['counts']['native_playable_windows'])
        self.assertEqual(1,c['counts']['choice_unknown_windows'])
        self.assertIsNone(c['play_selection_rate']['value'])
        shield=self.card(r,'Synthetic Shield')
        self.assertEqual(1,shield['counts']['in_hand_windows'])
        self.assertEqual(0,shield['counts']['native_playable_windows'])
        self.assertNotIn('OPPONENT_SECRET',str(r))

    def test_play_vs_hold_with_explicit_denominator(self):
        a=callback(choice={'channel':'action','actionId':1})
        b=callback(choice={'channel':'action','actionId':0}); b['window_key']='later'
        c=self.card(report(a,b))
        self.assertEqual({'numerator':1,'denominator':2,'value':0.5,'unknown_choices':0},c['play_selection_rate'])
        self.assertEqual(1,c['counts']['held_windows'])
        self.assertEqual(1,c['usefulness']['ex_ante_choice']['unknown'])
        self.assertEqual(1,c['usefulness']['observed_effect']['unknown'])

    def test_retries_modes_targets_and_multiple_copies_do_not_inflate(self):
        frame=callback(choice={'channel':'action','actionId':1})
        repeated=deepcopy(frame); repeated['evidence_refs']=[{'path':'000000.jsonl','line':3}]
        frame['offers']+=deepcopy(frame['offers'])
        duplicate=deepcopy(frame['cards'][0]); duplicate['instance_id']='other-copy'
        frame['cards'].append(duplicate)
        c=self.card(report(frame,repeated))
        self.assertEqual(1,c['counts']['native_playable_windows'])
        self.assertEqual(1,c['counts']['selected_play_windows'])
        self.assertEqual(2,c['unique_instances_seen'])
        self.assertEqual(2,len(c['windows'][0]['evidence_refs']))

    def test_unaffordable_and_missing_affordability_not_legal_opportunities(self):
        for affordable,status in [(False,'unaffordable_offer_windows'),(None,'playability_unknown_windows')]:
            frame=callback()
            frame['offers'][0]['affordable']=affordable
            c=self.card(report(frame))
            self.assertEqual(0,c['counts']['native_playable_windows'])
            self.assertEqual(1,c['counts'][status])

    def test_conflicting_choices_do_not_fabricate_play_or_hold(self):
        frame=callback(choice={'channel':'action','actionId':1})
        other=deepcopy(frame); other['selected']={'status':'known','play_instance_id':'c2'}
        c=self.card(report(frame,other))
        self.assertEqual(1,c['counts']['choice_unknown_windows'])
        self.assertEqual(0,c['counts']['held_windows'])

    def test_callback_dedup_ignores_log_and_routing_noise(self):
        frame=callback()
        # Two same-state offers with differently routed selected IDs remain one window.
        repeated=callback(choice={'channel':'action','actionId':1})
        self.assertEqual(frame['window_key'],repeated['window_key'])
        self.assertEqual(1,self.card(report(frame,repeated))['counts']['native_playable_windows'])

    def test_masked_perspective_and_hotseat_are_rejected(self):
        for change in ({'viewingPlayerId':'b'},{'hotseat':True},{'youAreHijacking':'b'}):
            state=deepcopy(callback()['test_state']) if 'test_state' in callback() else dict(
                viewingPlayerId='a',turnNumber=1,currentPhase='MAIN',currentStep='MAIN',activePlayerId='a',priorityPlayerId='a',cards={},zones=[])
            state.update(change)
            with self.assertRaises(CardOpportunityError): callback(state=state)

    def test_missing_window_identity_stays_unqualified(self):
        frame=callback(); frame['window_key']=None
        r=report(frame)
        self.assertEqual(1,r['coverage']['window_identity_unavailable'])
        self.assertEqual([],r['cards'])

    def test_private_report_is_idempotent_and_outside_source_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp); run.chmod(0o700)
            r=report(callback(choice={'channel':'action','actionId':1}))
            path=write_report(run,r)
            self.assertEqual(path,write_report(run,r))
            self.assertEqual(0o600,path.stat().st_mode&0o777)
            self.assertEqual(0o700,path.parent.stat().st_mode&0o777)
            self.assertEqual('analysis',path.relative_to(run).parts[0])

    def test_turn_exposure_is_lower_bound_and_end_censored(self):
        frames=[callback() for _ in range(3)]
        for f,t in zip(frames,[28,28,30]): f['turn']=t; f['window_key']=str(t)
        c=self.card(report(*frames))
        self.assertEqual([28,30],c['exposure']['observed_hand_turns'])
        self.assertEqual(1,c['exposure']['observed_turns_after_first_seen'])
        self.assertIsNone(c['exposure']['draw_turn'])
        self.assertIsNone(c['exposure']['turns_held'])
        self.assertFalse(c['exposure']['complete_turns'])

    def test_cohorts_and_structured_callbacks_are_not_pooled(self):
        first=callback(); second=deepcopy(first); second['context']['deck_revision']='other'
        structured=deepcopy(first); structured['structured']=True
        r=report(first,second,structured)
        self.assertEqual(4,len(r['cards']))
        self.assertEqual(1,r['coverage']['structured_frames_excluded'])

    def test_run_analysis_preserves_manifest_and_journal(self):
        from commander_gym.game_journal import PrivateGameJournal, RecorderSeatSink
        from commander_gym.game_server_seat import SeatProvenance
        from commander_gym.card_opportunities import analyze_run
        from tests.test_game_journal import PINS
        state=dict(viewingPlayerId='a',turnNumber=1,currentPhase='MAIN',currentStep='MAIN',
            activePlayerId='a',priorityPlayerId='a',cards={'c':{'name':'Synthetic'}},
            zones=[{'zoneId':{'ownerId':'a','zoneType':'HAND'},'isVisible':True,'cardIds':['c']}])
        obs=dict(type='GameServerSeat',perspectivePlayerId='a',state=state,legalActions=[])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'g'
            with PrivateGameJournal(root,'r',PINS,game_id='g') as journal:
                sink=RecorderSeatSink(journal,'a',gym_revision='b'*40)
                e=SeatProvenance('chooseAction',obs,{'channel':'action','actionId':9})
                sink.started(e); sink.finished(e)
                journal.finish({'kind':'operator_stop'},expected_sources={'seat:a':1},gaps=[])
            before={p.name:p.read_bytes() for p in root.iterdir()}
            result=analyze_run(root,generator_revision='b'*40)
            write_report(root,result)
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir() if p.is_file()})
            self.assertEqual(PINS,result['cards'][0]['context']['pins'])
            self.assertEqual(1,result['coverage']['collapsed_frames'])

    def test_canonical_missing_card_source_is_unknown(self):
        from commander_gym.card_opportunities import frame_from_decision
        d=dict(input=dict(observation=dict(type='Game',perspectivePlayerId='a',stateDigest='s',turnNumber=1,
            zones=[dict(ownerId='a',zoneType='HAND',hidden=False,cards=[dict(entityId='c',cardDefinitionId='def',name='Synthetic')])]),
            legal_actions=[dict(action_id='cast',payload=dict(kind='CastSpell',affordable=True,sourceEntityId=None))]),
            target=dict(chosen_action_id='cast'),provenance=dict(decision_id='d'))
        c=report(frame_from_decision(d,evidence_ref=dict(pointer='/decisions/0/input')))['cards'][0]
        self.assertEqual(1,c['counts']['playability_unknown_windows'])
        self.assertEqual(0,c['counts']['native_playable_windows'])

    def test_artifact_usefulness_separates_basis_and_rejects_future_leakage(self):
        from dataclasses import replace
        from tests.test_annotations import AnnotationTests
        from commander_gym.records import ActionRecord
        from commander_gym.evidence import RawEvidenceStore
        from commander_gym.annotations import AnnotationStore
        from commander_gym.storage import StorageLayout
        from commander_gym.card_opportunities import analyze_artifact
        fixture=AnnotationTests()
        record=replace(fixture.make_record(), observation=dict(type='Game',schemaHash='argentum-schema-v9',
            stateDigest='before', perspectivePlayerId='a',turnNumber=1,zones=[dict(ownerId='a',zoneType='HAND',
            hidden=False,cards=[dict(entityId='c',cardDefinitionId='def',name='Synthetic Draw')])]),
            legal_actions=[ActionRecord('cast',dict(kind='CastSpell',affordable=True,sourceEntityId='c'))],
            chosen_action_id='cast')
        with tempfile.TemporaryDirectory() as tmp:
            layout=StorageLayout.create(Path(tmp)); layout.ensure_directories()
            source=RawEvidenceStore(layout).write(fixture.make_run(),[record],commander_gym_revision='b'*40).artifact.artifact_id
            payload=dict(card_definition_id='def',basis='ex_ante_choice',label='helpful',rationale='Synthetic evidence',
                confidence=0.7,counterevidence=[],evidence_pointers=['/decisions/0/input/observation/turnNumber'])
            annotation=replace(fixture.annotation(source),annotation_type='card_usefulness.v1',payload=payload)
            store=AnnotationStore(layout)
            aid=store.write(annotation).artifact.artifact_id
            r=analyze_artifact(layout,source,annotation_artifact_ids=[aid,aid],generator_revision='b'*40)
            c=self.card(r)
            self.assertEqual(1,c['usefulness']['ex_ante_choice']['helpful'])
            self.assertEqual(1,c['usefulness']['observed_effect']['unknown'])
            conflict=replace(annotation,annotation_id='independent-review',payload={**payload,'label':'harmful'})
            bid=store.write(conflict).artifact.artifact_id
            c=self.card(analyze_artifact(layout,source,annotation_artifact_ids=[aid,bid],generator_revision='b'*40))
            self.assertEqual(1,c['usefulness']['ex_ante_choice']['unknown'])
            leaked=replace(annotation,revision='r2',payload={**payload,
                'evidence_pointers':['/decisions/0/provenance/outcome/result_observation/stateDigest']})
            lid=store.write(leaked).artifact.artifact_id
            with self.assertRaisesRegex(CardOpportunityError,'boundary'):
                analyze_artifact(layout,source,annotation_artifact_ids=[lid],generator_revision='b'*40)
            observed=replace(annotation,revision='r3',payload={**payload,'basis':'observed_effect',
                'evidence_pointers':['/decisions/0/provenance/outcome/result_observation/stateDigest']})
            oid=store.write(observed).artifact.artifact_id
            c=self.card(analyze_artifact(layout,source,annotation_artifact_ids=[oid],generator_revision='b'*40))
            self.assertEqual(1,c['usefulness']['observed_effect']['helpful'])
            self.assertEqual(1,c['usefulness']['ex_ante_choice']['unknown'])

    def test_public_board_changes_split_windows_hidden_identities_do_not(self):
        base=dict(viewingPlayerId='a',turnNumber=1,currentPhase='MAIN',currentStep='MAIN',
            activePlayerId='a',priorityPlayerId='a',cards={'c1':{'name':'Synthetic Draw'},'secret':{'name':'HIDDEN'}},
            zones=[{'zoneId':{'ownerId':'a','zoneType':'HAND'},'isVisible':True,'cardIds':['c1']},
                   {'zoneId':{'ownerId':'b','zoneType':'HAND'},'isVisible':False,'cardIds':['secret']}])
        hidden=deepcopy(base); hidden['cards']['secret']['name']='CHANGED_SECRET'; hidden['gameLog']=['noise']
        self.assertEqual(callback(state=base)['window_key'],callback(state=hidden)['window_key'])
        public=deepcopy(base); public['cards']['creature']={'name':'Public Creature'}
        public['zones'].append({'zoneId':{'ownerId':'b','zoneType':'BATTLEFIELD'},'isVisible':True,'cardIds':['creature']})
        self.assertNotEqual(callback(state=base)['window_key'],callback(state=public)['window_key'])
        self.assertNotIn('Public Creature',str(report(callback(state=public))))
