# Live human + three Luna Commander acceptance

This is the opt-in proof for the normal human-play topology after the game-server
provider, Luna sidecar, mulligan semantics, and native ActionParams work are present.

The proof uses:

- one ordinary Argentum WebSocket human client;
- one normal four-seat `FREE_FOR_ALL` tournament lobby;
- Commander rules and Commander deck legality;
- Krenko as the human deck;
- Talrand, Sythis, and Lathril as three independently controlled AI seats;
- the production Commander Gym OpenAI sidecar;
- `gpt-5.6-luna` by default.

It does not use Gym, a scenario-only AI mode, a fork-specific game-server protocol, or
an engine-side strategic fallback.

## Preconditions

Use Commander Gym with PR #61 and #62, plus an Argentum checkout that contains
Blue42hand/argentum-engine#116.

Install the optional OpenAI SDK:

```bash
python3 -m pip install -r requirements-openai.txt
```

Set your API key in the shell that launches the proof:

```bash
export OPENAI_API_KEY='...'
```

The key is inherited only by the local policy-sidecar process. It is not passed into
Argentum messages or written into provenance.

## Run

From the Commander Gym repository:

```bash
bash scripts/run_live_human_three_luna_acceptance.sh
```

If Argentum is elsewhere:

```bash
ARGENTUM_ENGINE_DIR=/path/to/argentum-engine \
  bash scripts/run_live_human_three_luna_acceptance.sh
```

Optional overrides:

```bash
export COMMANDER_GYM_OPENAI_MODEL=gpt-5.6-luna
export COMMANDER_GYM_LIVE_POD_TIMEOUT_SECONDS=900
export COMMANDER_GYM_KEEP_LIVE_EVIDENCE=1
```

With `COMMANDER_GYM_KEEP_LIVE_EVIDENCE=1`, the test prints the JSONL policy evidence
path instead of deleting it.

## Acceptance criteria

The live test fails closed unless all of these are observed in one normal multiplayer
game:

1. four seats in the normal Argentum game lifecycle: one human and three AI;
2. three distinct Argentum AI player ids;
3. all four exact 100-card roster decks submitted successfully;
4. all three AI seats cross the Luna-backed mulligan callback;
5. every AI seat makes at least one normal `chooseAction` policy decision;
6. the configured model identity appears in policy provenance for every AI seat;
7. the human projection observes an AI-owned permanent on the battlefield;
8. the human receives continuing state updates after AI actions;
9. no trusted `AiRuntimeSnapshot` field appears in Commander Gym policy provenance.

Native `ActionParams` and structured policy decisions remain reported in the live
summary when they occur, but they are not acceptance gates: a normal game is not
guaranteed to present either kind of decision in a bounded window. Those paths are
covered deterministically by the focused adapter/sidecar tests.

The test emits one `LIVE_COMMANDER_POD_RESULT=...` line summarizing callbacks,
parameterized actions, structured choices, human state updates, and terminal state if
the game ended before the bounded proof completed.

The default proof stops once the criteria are satisfied. It does not require a natural
terminal winner, because doing so makes the acceptance cost and duration dependent on
game length rather than on the integration properties under test. A terminal result is
still reported if it occurs first.

## Human GUI follow-up

Once this proof is green, the same server configuration is ready for an actual browser
session. The human seat can be replaced by the Argentum web client without changing the
three Luna seats or their policy boundary. That browser run is the final usability
check, not a different architecture.
