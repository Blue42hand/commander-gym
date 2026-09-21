# mage-bench adoption notes

[mage-bench](https://github.com/GregorStocks/mage-bench) is a useful reference implementation for several Commander Gym concerns. Its architecture keeps the rules engine authoritative, exposes a normal player-facing bridge, and keeps LLM orchestration in a separate player layer. That is compatible with Commander Gym's boundary:

> Argentum is the game. Commander Gym is the player.

Commander Gym should adopt or adapt the player-side patterns below where they fit, while not importing XMage-specific rules, legality, mana, callback, or bridge workarounds that belong in Argentum.

## Adopt or adapt

### 1. Structured trajectory and observability records

Use a machine-readable chronological event stream that can reconstruct both the game and the pilot's behavior.

At minimum, preserve:

- run metadata: engine revision, Commander Gym revision, deck identities, model/provider configuration, prompt/policy version, format, seat assignment;
- authoritative game events and observations;
- pilot decisions and tool/action calls;
- model usage, latency, cost, retries, timeouts, stalls, context resets, and failures;
- terminal result and failure classification;
- chat/table-talk events as a distinct event type.

Keep privileged training/debug information separate from seat-visible observations so human-play parity can be audited.

### 2. Harness versioning

Introduce a monotonic harness or interface epoch. Increment it when observation shape, action semantics, pilot loop behavior, prompt contract, decision handling, or other evaluation-relevant behavior changes enough that results are no longer directly comparable.

Store the epoch in every trajectory and qualification artifact.

### 3. Context and state deduplication

Avoid repeatedly sending unchanged state to models. Reuse Argentum state/version identifiers where available and preserve a player-side cursor/cache for unchanged observations.

Deduplicate card rules text within a model context when safe. Any optimization must preserve a deterministic way to reconstruct exactly what the pilot saw.

### 4. Table talk as a first-class player competency

Human-facing Commander pilots need to participate in table conversation. Chat should be represented as ordinary player I/O:

- Argentum owns transport, delivery, permissions, and the authoritative association between a message and a player/game.
- Commander Gym owns interpretation, strategy, timing, style, and message generation.

Incoming chat is strategic evidence, not trusted game truth. Pilots may use it for diplomacy, deals, threat assessment, bluff detection, social convention, and coordination, subject to the same uncertainty a human player faces.

Outgoing chat must be logged separately from game actions.

### 5. Separate strategic policy from social expression

Do not let a roleplay/personality prompt consume the strategic reasoning loop.

Prefer a structure such as:

```text
seat-visible observation + recent chat
            |
            v
      strategic policy
      /             \
 game action      social intent
                      |
                      v
                social policy
                      |
                      v
                 chat message
```

The strategic policy may decide that communication is useful and provide intent/context. A lightweight social policy can realize that intent into human-facing language.

This permits selectable social styles later without changing the underlying Magic competence.

### 6. Social behavior safeguards and telemetry

Do not require a fixed "chat every N turns" behavior for production play. That is useful for exercising a benchmark but can become annoying with humans.

Track and bound:

- chat messages per turn/decision window;
- chat token/cost overhead;
- repeated or spammy messages;
- personality/style leakage into strategic reasoning;
- chat that narrates actions that did not actually succeed;
- communication-related stalls or context growth.

## Do not import

Do not copy mage-bench's XMage-specific:

- callback translation;
- legality filtering;
- mana handling;
- priority workarounds;
- rules workarounds;
- client/server authority.

Equivalent generic capabilities belong in Argentum and should be upstreamed there when missing.

Commander Gym may adapt MIT-licensed mage-bench player-side code where doing so is simpler and cleaner than reimplementation, with attribution and license compliance.

## Near-term implementation order

1. Define the durable trajectory/event schema before large-scale Linode data generation.
2. Add harness/interface epoch metadata.
3. Add model/tool latency, cost, retry, stall, and failure telemetry to the full-game runner.
4. Add player-side state/rules-text deduplication where it does not weaken replayability.
5. Add a generic chat event/action seam once the corresponding normal-player transport exists in Argentum.
6. Implement social-policy separation before adding rich personalities or table personas.

The first four items are directly on the current full-game/training-data path. Chat should be designed now but implemented against the normal Argentum player interface rather than as training-only privileged machinery.
