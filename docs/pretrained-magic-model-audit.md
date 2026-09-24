# Pretrained Magic model audit

Tracking: #112  
Snapshot: 2026-09-24

This is an evidence inventory, not a strength ranking. Forge/XMage may be training provenance, but imported policies must ultimately use Commander Gym's seat-safe Argentum Pilot boundary.

## Candidate status

| Candidate | Source snapshot | Public checkpoint | Key finding |
| --- | --- | --- | --- |
| Austinio/Talor Forge BC | `Talor-A/forge@ai_investigation` head `511dbb731d2974a6bb32734392bd6a50257101e3` | **Yes** | Nine ONNX model components plus external-data files are committed under `forge-ai-rl/models/`. |
| Anvil | `Tyrathalis/anvil` observed at `67f80e57118aece2a2d0d1cbf0833d9609da7715` | Not located | Checkpoints/models are gitignored and GitHub releases are empty. GPL-3.0-or-later. |
| Jack Maiorino XMage RL | observed at `af17fe16dff6a74b0ea46b3f2bc3d07bba28ac42` | Not located | v2.1 is ~2.1M params with transformer candidate scoring; model/profile artifacts are gitignored. Repo license is MIT. |
| MageZero | observed at `11a5974668c3f0f3d19559d7dcbf6e323f140808` | Not located | v0.2 release contains the framework/XMage bundle, not pretrained weights. MIT. |
| Austinio original Forge RL | `ai_investigation` head `88105ef0325523e1c4a5839e9c2aa2faeea15861` | No release | `rl_data/` is ignored; Talor's fork preserves a usable exported model from this line. GPL-3.0. |
| npiguet/price-predictor | observed at `15d49c875b1f759d8e544a7b8a32e223d0ae2ca5` | Not located | `models/` is ignored, no releases, and GitHub reports no repository license. Gameplay-derived card work needs primary-source tracing. |
| MTG-specialized local LLMs | pending exact model-card pass | Some known downloadable | Semantic baseline only; not gameplay-trained. |

## First obtainable gameplay model: Talor/Austinio ONNX bundle

The Talor branch contains a complete static inference bundle:

- `state_encoder.onnx`
- `value_head.onnx`
- `priority_head.onnx`
- `target_head.onnx`
- `attack_head.onnx`
- `block_head.onnx`
- `card_select_head.onnx`
- `mulligan_head.onnx`
- `binary_head.onnx`

The associated `.onnx.data` weight files are also committed. Git history identifies `f0296448106501e243acc8b460907cfc0ffb3e6e` as the 22-epoch decision-head export used for GUI inference. The architecture document reports 11,044,104 parameters total, including a physically separate 3,409,664-parameter transformer state encoder producing a 512-dimensional embedding. Legal actions use a 64-dimensional candidate representation.

This is behavioral-cloning initialization, not an expert policy. Austinio's primary project notes describe weak/plateauing PPO after imitation learning, so the useful transfer question is whether the representation learned ordinary Magic structure.

### Smallest #114 experiment

Prepare an offline frozen-encoder compatibility probe; do not run Forge or games while #77 is active.

1. Catalog the exact external model files and source revision through #113.
2. Implement a Commander Gym-only read-only adapter from a seat-safe Argentum observation to the documented state-encoder tensor schema. Fail closed when a Forge feature has no exact Argentum equivalent.
3. Run only `state_encoder.onnx` on a small frozen #114 fixture set; submit no actions.
4. Probe the 512d embeddings for seat-visible resource/combat/state labels and compare against an untrained control.
5. Only if the representation transfers, map a narrowly supported subset of Argentum legal actions into the candidate schema and evaluate the frozen priority head.

This tests transfer without making Forge a runtime dependency or restarting ordinary gameplay.

## Consequential MageZero information-boundary finding

Primary upstream files confirm that existing MageZero search supervision is not clean seat-safe expert data:

- `configs/game.yml` defaults MCTS players to `hidden_info.see_opponent_hand: true`.
- `faq_goals.md` says the neural state representation excludes hidden information, but deterministic tree search leaks future draws, opponent hands, and random outcomes.

A future network checkpoint may still be useful after input validation, but existing MCTS action labels must be classified as privileged search evidence rather than deployable-policy supervision.

## Next bounded audit slice

1. Catalog/hash the Talor model bundle in #113 and review the feature extractor field-by-field for information safety.
2. Audit Maiorino's state/candidate encoder and checkpoint availability.
3. Resolve Anvil checkpoint availability.
4. Trace npiguet's gameplay-derived representation and repository terms.
5. Complete exact model-card/revision/terms records for MTG-specialized local LLM semantic baselines.
