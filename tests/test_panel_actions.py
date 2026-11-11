import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from commander_gym.game_journal import PrivateGameJournal, PIN_KEYS
from commander_gym.panel_actions import ActionIndex, narrate, MAX_ROWS
from commander_gym.control_panel import sanitized


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.capture=self.root/'capture'
        self.index=ActionIndex(self.root)
        pins={k:None for k in PIN_KEYS}
        self.writer=PrivateGameJournal(self.capture,'run',pins,game_id='game',compress_records=True)
        self.addCleanup(self.writer.close)
        self.players=[{'id':'p','name':'Noah','deck':'Meren','seat':0}]

    def transition(self,typ='PlayLand',events=None):
        self.writer.append('native_transition',{'game_id':'game','native_schema':'synthetic-v1',
            'before_state_digest':'before','after_state_digest':'after','action':{'type':typ,'playerId':'p','cardId':'e1'},
            'result':{'outcome':{'type':'Done'}},'events':events or [{'type':'ZoneChangeEvent',
            'entityId':'e1','entityName':'Forest','fromZone':'Hand','toZone':'Battlefield'}]},source='native',source_sequence=self.writer.sequence-1)

    def advance(self):return self.index.advance('capture',self.capture,self.players,sanitized)

    def test_readable_facts_links_and_restart_without_source_writes(self):
        self.transition();before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.capture.iterdir() if p.is_file()}
        state=self.advance();rows,count=self.index.page('capture',first=True)
        self.assertTrue(state['caught_up']);self.assertEqual(count,2)
        self.assertEqual(rows[0]['text'],'Noah plays Forest.')
        self.assertEqual(rows[0]['anchor'],'event:1');self.assertEqual(len(rows[0]['source_sha256']),64)
        reopened=ActionIndex(self.root);self.assertEqual(reopened.page('capture',first=True)[0],rows)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.capture.iterdir() if p.is_file()})

    def test_live_append_resumes_without_duplicate_actions(self):
        self.transition();state=self.advance();self.advance()
        self.assertEqual(self.index.page('capture')[1],2)
        self.transition('PassPriority',events=[{'type':'TurnChangedEvent','turnNumber':2,'activePlayerId':'p'}])
        after=self.advance();self.assertEqual(state['generation'],after['generation'])
        rows,count=self.index.page('capture',routine=True)
        self.assertEqual(count,4);self.assertEqual(rows[-1]['turn'],2)
        self.assertEqual(self.index.page('capture',turn=2)[0][0]['text'],'Turn 2 — Noah.')
        self.assertEqual(self.index.page('capture',after=rows[1]['position'],routine=True)[0],rows[2:])

    def test_large_journal_is_incremental_and_pages_are_bounded(self):
        for _ in range(300):self.transition()
        state=self.advance()
        self.assertLessEqual(state['source_rows'],MAX_ROWS);self.assertFalse(state['caught_up'])
        self.assertLessEqual(len(self.index.page('capture')[0]),50)
        for _ in range(10):
            if state['caught_up']:break
            state=self.advance()
        self.assertTrue(state['caught_up']);self.assertEqual(state['source_rows'],301)
        self.assertEqual(self.index.page('capture')[1],600)

    def test_truncated_source_invalidates_rebuildable_cache(self):
        self.transition();old=self.advance()
        path=self.capture/'000000.jsonl';first=path.read_bytes().splitlines(keepends=True)[0];path.write_bytes(first)
        state=self.advance();self.assertNotEqual(old['generation'],state['generation'])
        self.assertEqual(self.index.page('capture')[1],0)

    def test_partial_tail_waits_then_reads_completed_record(self):
        self.transition();path=self.capture/'000000.jsonl';raw=path.read_bytes();path.write_bytes(raw[:-3])
        state=self.advance();self.assertIn('complete journal tail',state['diagnostic'])
        self.assertEqual(self.index.page('capture')[1],0)
        path.write_bytes(raw);state=self.advance()
        self.assertTrue(state['caught_up']);self.assertEqual(self.index.page('capture')[1],2)

    def test_malformed_and_too_large_sources_are_diagnostic(self):
        path=self.capture/'000000.jsonl';path.write_bytes(b'{bad json}\n')
        state=self.advance();self.assertIn('Malformed',state['diagnostic'])
        path.write_bytes(b'x'*(2*1024*1024+1)+b'\n')
        state=self.advance();self.assertIn('2 MiB',state['diagnostic'])
        self.assertEqual(self.index.page('capture')[1],0)

    def test_symlink_and_fifo_do_not_get_read(self):
        path=self.capture/'000000.jsonl';path.unlink();path.symlink_to('/etc/passwd')
        self.assertIn('unsafe',self.advance()['diagnostic'].lower())
        path.unlink();os.mkfifo(path,0o600)
        self.assertIn('Unsafe',self.advance()['diagnostic'])

    def test_a_hash_mismatch_never_becomes_an_action(self):
        from commander_gym.record_codec import decode_record,encode_record
        self.transition();path=self.capture/'000000.jsonl'
        lines=path.read_bytes().splitlines(keepends=True)
        row=json.loads(decode_record(lines[1]));row['payload']['action']['cardId']='forged'
        lines[1]=encode_record(json.dumps(row).encode()+b'\n',compress=False)
        path.write_bytes(b''.join(lines))
        state=self.advance();self.assertIn('inconsistent',state['diagnostic'])
        self.assertEqual(self.index.page('capture')[1],0)

    def test_decisions_are_not_invented_moves_and_checkpoint_is_not_exposed(self):
        context={'players':{'p':'Noah'},'cards':{}}
        source={'kind':'native_source','payload':{'source_body':json.dumps({'kind':'state_checkpoint',
            'payload':{'after':{'turnNumber':7,'phase':'PRECOMBAT_MAIN','secretHand':['PRIVATE CARD']}}})}}
        self.assertEqual(narrate(source,context),[]);self.assertEqual(context['turn'],7)
        self.assertNotIn('PRIVATE CARD',json.dumps(context))
        rows=narrate({'kind':'seat_callback','seat_id':'p','payload':{'choice':{'errorType':'PilotError','rationale':'PRIVATE RATIONALE'}}},context)
        self.assertIn('not an executed move',rows[0]['text']);self.assertNotIn('PRIVATE',json.dumps(rows))
        rows=narrate({'kind':'native_transition','payload':{'action':{'type':'PlayLand','playerId':'p','cardId':'e1'},'result':{'status':'rejected'}}},context)
        self.assertIn('execution outcome is not established',rows[0]['text'])

    def test_combat_damage_names_and_no_fabricated_winner(self):
        context={'players':{'p':'Noah'},'cards':{}}
        rows=narrate({'kind':'native_transition','payload':{'events':[{'type':'DamageDealtEvent',
            'sourceName':'Llanowar Elves','targetId':'p','targetIsPlayer':True,'amount':1,'isCombatDamage':True},
            {'type':'LifeChangedEvent','playerId':'p','oldLife':40,'newLife':39}]}},context)
        self.assertIn('1 combat damage to Noah',rows[0]['text']);self.assertIn('40 → 39',rows[1]['text'])
        terminal=narrate({'kind':'terminal','payload':{'termination':{'kind':'session_closed'}}},context)
        self.assertNotIn('wins',terminal[0]['text'])


if __name__=='__main__':unittest.main()
