"""Bounded, rebuildable, owner-only narration of recorded native evidence.

No model calls, inferred rules, reconstructed moves, or writes to source evidence.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import stat
import threading
import time
import uuid
from pathlib import Path

from .game_journal import ZERO, _json, _safe
from .record_codec import decode_record

MAX_RECORD = 2 * 1024 * 1024
MAX_ROWS = 120
MAX_EVENTS = 64


def text(value, fallback='Unknown', limit=180):
    if not isinstance(value, (str, int)) or isinstance(value, bool): return fallback
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value))[:limit] or fallback


def words(value):
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text(value)).replace('_', ' ').lower()


def narrate(row, context):
    """Emit concise facts with stable journal anchors. Never copy model rationale."""
    payload = row.get('payload', {})
    if not isinstance(payload, dict): return []
    names, cards = context.setdefault('players', {}), context.setdefault('cards', {})
    def player(value): return names.get(str(value), 'Player '+text(value, 'unknown', 40))
    def card(value): return cards.get(str(value), 'card '+text(value, 'unknown', 40))
    output = []
    def add(message, routine=False, kind='event'):
        if len(output) < MAX_EVENTS:
            output.append({'text':text(message, limit=900),'routine':routine,'kind':kind,
                           'turn':context.get('turn'),'phase':context.get('phase')})
    kind = row.get('kind')
    if kind == 'native_source':
        # Checkpoints establish only explicit turn/phase metadata. Never retain
        # the referee state, libraries, hand contents, RNG or private choices.
        source = payload.get('source_body')
        if isinstance(source, str) and len(source) <= MAX_RECORD:
            try:
                native = json.loads(source)
                state = native.get('payload', {}).get('after', {})
                if native.get('kind') == 'state_checkpoint' and isinstance(state, dict):
                    if type(state.get('turnNumber')) is int: context['turn'] = state['turnNumber']
                    if isinstance(state.get('phase'), str): context['phase'] = words(state['phase'])
            except (ValueError, TypeError, AttributeError): pass
        return []
    if kind == 'seat_callback':
        choice = payload.get('choice', {})
        if isinstance(choice, dict) and choice.get('errorType'):
            add(player(row.get('seat_id'))+' — pilot callback failed ('+text(choice['errorType'])+'). This is not an executed move.', kind='error')
        return output
    if kind == 'terminal':
        add('Capture closed. See the recorded result and supervisor termination for the game outcome.', kind='recording')
        return output
    if kind != 'native_transition': return []
    events = payload.get('events', [])
    if not isinstance(events, list): events = []
    # Names are learned from explicit native events, not guesses from IDs.
    for event in events[:256]:
        if not isinstance(event, dict): continue
        for id_key, name_key in (('entityId','entityName'),('sourceId','sourceName'),('cardId','cardName'),
                                 ('spellEntityId','cardName'),('entityId','name'),('landId','landName'),
                                 ('targetId','targetName')):
            entity, name = event.get(id_key), event.get(name_key)
            if isinstance(entity, str) and isinstance(name, str) and name != 'Player' and len(cards) < 4096:
                cards[entity] = text(name)
    action = payload.get('action', {})
    if not isinstance(action, dict): action = {}
    actor, typ = player(action.get('playerId')), action.get('type')
    result = payload.get('result', {})
    outcome = result.get('outcome', {}) if isinstance(result, dict) else {}
    confirmed = isinstance(outcome, dict) and outcome.get('type') == 'Done' or isinstance(result, dict) and result.get('status') == 'applied'
    known = {
        'PlayLand': f"plays {card(action.get('cardId'))}",
        'CastSpell': f"casts {card(action.get('cardId'))}",
        'ActivateAbility': f"activates {card(action.get('sourceId'))} ({text(action.get('abilityId'), 'ability')})",
        'PassPriority': 'passes priority', 'KeepHand': 'keeps their opening hand',
        'TakeMulligan': 'takes a mulligan',
        'BottomCards': f"puts {len(action.get('cardIds', [])) if isinstance(action.get('cardIds'), list) else 'unknown number of'} cards on the bottom of their library",
        'Concede': 'concedes',
    }
    if typ == 'DeclareAttackers':
        attackers = action.get('attackers', {})
        if isinstance(attackers, dict):
            moves = [card(a)+' attacks '+player(b) if str(b) in names else card(a)+' attacks '+card(b)
                     for a,b in list(attackers.items())[:12]]
            known[typ] = 'declares attackers: '+('; '.join(moves) if moves else 'none')
    if typ == 'DeclareBlockers':
        blockers = action.get('blockers', {})
        if isinstance(blockers, dict):
            known[typ] = 'declares blockers: '+('; '.join(card(k)+' blocks '+', '.join(card(v) for v in values[:12])
                for k,values in list(blockers.items())[:12] if isinstance(values,list)) or 'none')
    if typ == 'SubmitDecision':
        response = action.get('response', {})
        known[typ] = 'submits a decision: '+words(response.get('type')) if isinstance(response,dict) else 'submits a decision'
    if typ:
        message = known.get(typ, 'submits '+words(typ)+' (recorded action)')
        if not confirmed: message = 'submitted action: '+message+'; execution outcome is not established by this row'
        add(actor+' '+message+'.', routine=typ=='PassPriority', kind='action')
    for event in events[:128]:
        if not isinstance(event,dict): continue
        typ = event.get('type')
        if typ == 'TurnChangedEvent':
            if type(event.get('turnNumber')) is int: context['turn'] = event['turnNumber']
            add('Turn '+text(event.get('turnNumber'))+' — '+player(event.get('activePlayerId'))+'.', kind='turn')
        elif typ in ('PhaseChangedEvent','StepChangedEvent'):
            value = event.get('newPhase',event.get('newStep'))
            context['phase'] = words(value)
            add(context['phase'].capitalize()+'.', routine=True)
        elif typ == 'CardsDrawnEvent': add(player(event.get('playerId'))+' draws '+text(event.get('count'))+' card(s).')
        elif typ == 'LifeChangedEvent': add(player(event.get('playerId'))+' life: '+text(event.get('oldLife'))+' → '+text(event.get('newLife'))+'.')
        elif typ == 'DamageDealtEvent':
            target = player(event.get('targetId')) if event.get('targetIsPlayer') else text(event.get('targetName'),card(event.get('targetId')))
            add(text(event.get('sourceName'),card(event.get('sourceId')))+' deals '+text(event.get('amount'))+(' combat' if event.get('isCombatDamage') else '')+' damage to '+target+'.')
        elif typ == 'ResolvedEvent': add(text(event.get('name'),card(event.get('entityId')))+' resolves.')
        elif typ in ('AbilityTriggeredEvent','AbilityResolvedEvent'):
            add(text(event.get('sourceName'),card(event.get('sourceId')))+(' — an ability triggers.' if typ=='AbilityTriggeredEvent' else ' — an ability resolves.'))
        elif typ == 'DecisionRequestedEvent': add(player(event.get('playerId'))+' is asked to decide: '+text(event.get('prompt'),words(event.get('decisionType')))+'.', kind='decision')
        elif typ == 'DecisionSubmittedEvent': add(player(event.get('playerId'))+': '+text(event.get('description'),'decision submitted')+'.', kind='decision')
        elif typ == 'ZoneChangeEvent' and event.get('toZone') in ('Battlefield','Graveyard','Exile'):
            add(text(event.get('entityName'),card(event.get('entityId')))+' moves from '+words(event.get('fromZone'))+' to '+words(event.get('toZone'))+'.')
        elif typ == 'PermanentAttachedEvent': add(text(event.get('attachmentName'),card(event.get('attachmentId')))+' attaches to '+card(event.get('attachedToId'))+'.')
        elif typ == 'BecomesTargetEvent': add(text(event.get('sourceName'),card(event.get('sourceEntityId')))+' targets '+text(event.get('targetName'),card(event.get('targetEntityId')))+'.')
    if len(events) > 128 or len(output) == MAX_EVENTS:
        output = output[:MAX_EVENTS-1]
        output.append({'text':'Additional events omitted from this entry; inspect its source evidence.',
                       'routine':False,'kind':'coverage','turn':context.get('turn'),'phase':context.get('phase')})
    return output


class ActionIndex:
    def __init__(self, directory):
        self.path = Path(directory)/'actions.sqlite3'
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS states(ref TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions(ref TEXT,position INTEGER,turn INTEGER,routine INTEGER,value TEXT NOT NULL,
                    PRIMARY KEY(ref,position));
                CREATE INDEX IF NOT EXISTS actions_turn ON actions(ref,turn,position);''')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=2)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def advance(self, ref, directory, participants, clean):
        """Read at most 120 source rows / 24 MiB / 0.6 seconds per request."""
        with self.lock, self.connect() as db:
            old=db.execute('SELECT value FROM states WHERE ref=?',(ref,)).fetchone()
            state=json.loads(old[0]) if old else {}
            paths=sorted(directory.glob('*.jsonl'))[:1000]
            valid=bool(state)
            for name, observed in state.get('files',{}).items():
                path=directory/name
                try:
                    st=path.stat()
                    if path.is_symlink() or not stat.S_ISREG(st.st_mode) or [st.st_dev,st.st_ino]!=observed['inode'] or st.st_size<observed['offset']: valid=False;break
                    with path.open('rb') as stream:
                        start=max(0,observed['offset']-128);stream.seek(start)
                        if hashlib.sha256(stream.read(observed['offset']-start)).hexdigest()!=observed['edge']: valid=False;break
                except OSError: valid=False;break
            if not valid:
                db.execute('DELETE FROM actions WHERE ref=?',(ref,))
                state={'generation':uuid.uuid4().hex,'files':{},'sequence':0,'previous':ZERO,
                       'context':{'players':{str(p['id']):text(p.get('name'),p.get('deck') or 'Participant')
                          for p in participants if p.get('id')},'cards':{}},'run':None}
            diagnostic=None;scanned=0;physical_bytes=0;start=time.monotonic();caught_up=True
            for index,path in enumerate(paths):
                if path.name!=f'{index:06d}.jsonl' or path.is_symlink(): diagnostic='Missing or unsafe journal segment.';break
                st=path.stat()
                if not stat.S_ISREG(st.st_mode): diagnostic='Unsafe journal file type; reading stopped.';break
                file=state['files'].setdefault(path.name,{'inode':[st.st_dev,st.st_ino],'offset':0,'edge':hashlib.sha256(b'').hexdigest()})
                with path.open('rb') as stream:
                    stream.seek(file['offset'])
                    while True:
                        if scanned>=MAX_ROWS or physical_bytes>=24*1024*1024 or time.monotonic()-start>.6:
                            caught_up=stream.tell()>=st.st_size and index==len(paths)-1
                            break
                        raw=stream.readline(MAX_RECORD+1)
                        if not raw: break
                        if len(raw)>MAX_RECORD: diagnostic='A record exceeds the 2 MiB action-reader limit; source is preserved.';break
                        if not raw.endswith(b'\n'): diagnostic='Waiting for a complete journal tail.';break
                        try:
                            row=json.loads(decode_record(raw,max_bytes=MAX_RECORD))
                            digest=row.pop('sha256')
                            if (type(row['schema_version']) is not int or row['schema_version']!=1
                                or not isinstance(row.get('payload'),dict) or not isinstance(row.get('kind'),str)
                                or row['sequence']==0 and row['kind']!='manifest'
                                or type(row['sequence']) is not int or row['sequence']!=state['sequence']
                                or row['previous_sha256']!=state['previous'] or hashlib.sha256(_json(row)).hexdigest()!=digest
                                or state['run'] is not None and row['run_id']!=state['run']):
                                raise ValueError('chain')
                            _safe(row)
                            if state.get('terminal'): raise ValueError('after terminal')
                            entries=narrate(row,state['context'])
                            for part,entry in enumerate(entries):
                                entry=clean({**entry,'sequence':row['sequence'],'position':row['sequence']*128+part,
                                    'anchor':'event:'+str(row['sequence']),'source_sha256':digest,'recorded_at':row.get('recorded_at')})
                                db.execute('INSERT INTO actions VALUES(?,?,?,?,?)',(ref,entry['position'],entry['turn'],int(entry['routine']),json.dumps(entry)))
                            state.update(sequence=state['sequence']+1,previous=digest,run=row['run_id'],terminal=row.get('kind')=='terminal')
                        except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
                            diagnostic='Malformed, unsafe, too-large or inconsistent journal row; action reading stopped.';break
                        file['offset']=stream.tell();scanned+=1;physical_bytes+=len(raw)
                    current=stream.tell();stream.seek(max(0,file['offset']-128))
                    file['edge']=hashlib.sha256(stream.read(min(128,file['offset']))).hexdigest()
                if diagnostic or not caught_up or scanned>=MAX_ROWS or time.monotonic()-start>.6:
                    caught_up=caught_up and current>=st.st_size and index==len(paths)-1
                    break
            if not paths: diagnostic='No supported game journal is available for readable actions.'
            state.update(caught_up=caught_up and diagnostic is None,diagnostic=diagnostic,observed_unix=time.time(),source_rows=state['sequence'])
            db.execute('INSERT OR REPLACE INTO states VALUES(?,?)',(ref,json.dumps(state)))
            return state

    def page(self,ref,*,before=None,after=None,first=False,routine=False,turn=None,limit=50):
        with self.connect() as db:
            where=['ref=?'];params=[ref]
            if before is not None: where.append('position<?');params.append(before)
            if after is not None: where.append('position>?');params.append(after)
            if not routine:where.append('routine=0')
            if turn is not None:where.append('turn=?');params.append(turn)
            ascending=first or after is not None
            sql='SELECT value FROM actions WHERE '+' AND '.join(where)+' ORDER BY position '+('ASC' if ascending else 'DESC')+' LIMIT ?'
            rows=[json.loads(r[0]) for r in db.execute(sql,(*params,min(2000,limit)))]
            if not ascending:rows.reverse()
            count=db.execute('SELECT count(*) FROM actions WHERE ref=?',(ref,)).fetchone()[0]
        return rows,count
