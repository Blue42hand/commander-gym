import json
from pathlib import Path
from commander_gym.tolaria_dashboard import _records, build_status

def test_records_ignore_bad_and_keep_policy(tmp_path: Path):
    p=tmp_path/"p.jsonl"
    p.write_text('bad\n'+json.dumps({"event":"other"})+'\n'+json.dumps({"event":"game_server_policy","playerId":"p1","callback":"chooseAction","observation":{"state":{"phase":"MAIN1"},"recentGameLog":["cast Sol Ring"]},"choice":{"channel":"action","actionId":2}})+'\n')
    rows=_records(p)
    assert len(rows)==1
    assert rows[0]["playerId"]=="p1"

def test_status_uses_existing_provenance(tmp_path: Path, monkeypatch):
    p=tmp_path/"p.jsonl"
    rows=[
      {"event":"game_server_policy","playerId":"p1","callback":"decideMulligan","observation":{"state":{}},"choice":{"keep":True}},
      {"event":"game_server_policy","playerId":"p2","callback":"chooseAction","observation":{"state":{"phase":"COMBAT","turnNumber":4},"recentGameLog":["attack"]},"choice":{"channel":"decision","metadata":{"reason":"block"}}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in rows)+"\n")
    monkeypatch.setattr("commander_gym.tolaria_dashboard._host",lambda: {})
    monkeypatch.setattr("commander_gym.tolaria_dashboard._services",lambda: {})
    monkeypatch.setattr("commander_gym.tolaria_dashboard._revisions",lambda: {})
    status=build_status(p)
    assert status["policy"]=={"calls":2,"seats":2,"actions":0,"decisions":1,"mulligans":1}
    assert status["live"]["state"]["phase"]=="COMBAT"
    assert status["live"]["recentGameLog"]==["attack"]
