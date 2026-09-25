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
| MageZero | current `11a5974668c3f0f3d19559d7dcbf6e323f140808`; legacy artifact lineage pinned below | Current v0.2: **No**; legacy UWTempo: **Yes** | Current v0.2 release contains the framework/XMage bundle but no weights. A legacy 2.21M-param UWTempo checkpoint + exact state/action sidecars are recoverable, but its evaluation and mutable action-vocabulary path have major qualification hazards. MIT rights are explicit for the current project and for an immutable tree retaining the legacy ONNX. |
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

## Current MageZero v0.2: evaluation provenance is non-stationary

Exact current source: `WillWroble/MageZero@11a5974668c3f0f3d19559d7dcbf6e323f140808`.

The README reports long-running UWTempo reinforcement-learning results including a rise from a 16% minimax baseline to about 66% RL win rate, but the underlying `results/roundrobin-trainlog.txt` is not a stationary generation-by-generation evaluation series:

- between generation-7 runs it explicitly records `SWITCHED SEARCH BUDGET FROM 150 TO 300`;
- nearby runs are marked `gen7 - no policy` and `gen7 - no target policy`;
- generation 12 is marked `target was turned on`;
- the later `b` trajectory explicitly switches to a new value-labeling scheme at gen17, runs gen18b-gen21b without policy, then restores policy at gen22b.

The README's approximately 66% UWTempo headline is consistent with the later gen23b five-opponent mean (about 65.9%), not with one controlled model-only trajectory. That point occurs after the value-labeling change and after policy was re-enabled. Therefore the source win-rate curve mixes checkpoint progression with search-budget, policy/target-mode, and value-labeling changes.

For #112/#114, treat all current MageZero win-rate figures as source-context only unless each result is bound to an exact checkpoint, search budget, policy/target mode, value-target regime, opponent/deck pool, and fallback behavior. Do not compare these percentages directly with Anvil, Talor, Maiorino, or Commander Gym cohorts.

## Next bounded audit slice

1. Catalog/hash the Talor model bundle in #113 and review the feature extractor field-by-field for information safety.
2. Audit Maiorino's state/candidate encoder and checkpoint availability.
3. Resolve Anvil checkpoint availability.
4. Trace npiguet's gameplay-derived representation and repository terms.
5. Complete exact model-card/revision/terms records for MTG-specialized local LLM semantic baselines.

## MTG-Llama semantic baseline

Exact model snapshot: `jakeboggs/MTG-Llama@4bd3d56a8655f176d1215dadc73f53d3c4d16d30`.

Exact training-code snapshot: `JakeBoggs/Large-Language-Models-for-Magic-the-Gathering@dfd71e489bd83d48c6c8c736d1eb18de8f1e5e83`.

The public checkpoint is a merged Llama 3 8B Instruct causal language model in F16 safetensors, roughly 16 GB of first-party weights. Training uses QLoRA with rank 64, alpha 32, dropout 0.05 over attention and MLP projections, then merges the adapters into the base model.

The training corpus is `jakeboggs/MTG-Eval`: 80,032 synthetic QA pairs made from MTGJSON and Commander Spellbook and reformatted with GPT-3.5. It contains 26,702 card-description examples, 27,104 rules questions, and 26,226 card-interaction examples. It contains no game-state/action trajectories, legal-action policy labels, combat or multiplayer decisions, or opponent populations.

**Qualification:** semantic/rules/combo knowledge only. Do not classify MTG-Llama as gameplay pretraining or place it in the 1v1-enriched gameplay cohort.

**Licensing blocker:** the Hugging Face model card exposes no license metadata; the training repository reports no license and has no LICENSE file. The merged derivative also inherits relevant Meta Llama 3 terms. Until explicit derivative terms are documented, #113 should treat redistribution/training rights as unresolved even though the checkpoint is publicly downloadable.

**Evaluation caveat:** the source project uses a random 95/5 example split rather than card/combo/source-group-disjoint evaluation. Its evaluation script uses stochastic generation plus GPT-4 judging and, at the audited source revision, points `model_id` at `NousResearch/Meta-Llama-3-8B-Instruct` rather than the fine-tuned checkpoint. The reported uplift therefore remains source-project context, not Commander Gym qualification evidence.

Smallest #114 use: register the exact merged checkpoint through #113 (and any quantization as a separate artifact), then use it only as a semantic/rules baseline on input-only held-out #114 fixtures. Optionally compare generated MTG context or hidden-state features against the base local model, but do not place it in the 1v1-enriched gameplay cohort.

## Wyrmling compact semantic baseline

Exact checkpoint snapshot: `Tagashy/wyrmling-110M-mtg-dsl@cd7172fc0a0f18c07f344ec8ddf908730f181732`.

The project is MIT licensed for its rights in the weights; its model card separately notes Fan Content/Wizards-IP limits. Public artifacts include:

- `model.safetensors` — SHA-256 `28862accda2e1dfac555d7432f677c1538ab8b43b623f1a30d4db137309b66c4`;
- `wyrmling-sft-final.pt` — LFS object SHA-256 `efbb23a2a256b700bae47bc00c25eb4abad3048430ea8f3b1e52d99adab1874d`.

Architecture/training: about 118M parameters, decoder-only, d=768, 14 layers, SwiGLU, RoPE, QK normalization, untied embeddings, logit soft-cap, and a custom roughly 12k BPE vocabulary. It was trained from scratch on about 0.295B token-positions of v8 MTG DSL corpus plus rare-dense synthetic grammar data, then SFT for two epochs on intent-to-DSL pairs. It has no gameplay supervision.

The model card reports a 5,051-card held-out v8 test with 91.6% parse and 45.6% canonical-exact accuracy. Those are mechanics/compiler metrics only. The audited v8 DSL also contains 117 later-retired counterfeit enum values; v9 checkpoints are expected to supersede it.

**Qualification:** use only as a frozen card/ability-mechanics representation control against random or price/text encoders. Make no gameplay-policy claim and do not create a production compiler dependency.


## Commander AI Lab branch-level checkpoint/adaptation audit

Primary repository: `KoalaTrapLord/commander-ai-lab`.

A branch-level artifact check confirms there is still no public trained neural checkpoint in the repository, including the most suggestive non-main branches:

- `feat/overnight-update-weights@a3e35c73cd59212de049a7d36430f560bef75481` contains `ml/scripts/update_weights.py`, but that script updates scalar simulation heuristics into `learned_weights.json`; it does not download or publish a neural policy checkpoint. The branch still expects locally trained `ml/models/checkpoints/best_policy.pt` / `best_ppo.pt`, neither of which is committed.
- `feature/n-player-sim@fa43fe3ecde86eaae5b950fd02086e83604e7843` extends the simulator, but the ML encoder remains hard-coded to the 1v1 6,177d contract: it explicitly iterates `players[:2]`, encodes four zones for exactly two players, and the policy network still consumes a 6,177d state and emits eight macro-actions.

This means the repository's n-player simulation work does **not** supply a reusable multi-opponent learned representation. Even if a checkpoint later appears, a direct Commander transfer would still omit/collapse two opponents in the model input. The smallest acceptable #114 experiment remains a Commander Gym-owned actor-relative/multi-opponent wrapper around any frozen reusable substrate; do not import the existing 1v1 action head as a four-player policy.

No public checkpoint, release asset, NPZ dataset, or committed `learned_weights.json` was found on the audited branches. Treat this project as architecture/training-pipeline evidence unless an exact artifact becomes public.


## Historical MageZero UWTempo checkpoint: artifact usability provenance

A historically public MageZero gameplay checkpoint family is recoverable even though current MageZero v0.2 publishes no weights. Treat this as a separate legacy lineage, not as a v0.2 checkpoint.

- Primary model source: `WillWroble/MageZero@14669a98d4be0a2b1d865174bad6217c530e885b`.
  - `models/ckpt_40.pt`: Git blob `d3f97f798f70c832b3b0b058b8d4a9ca6a21f04a`, 8,854,912 bytes.
  - `exports/UWTempo/UWTempo2.onnx`: Git blob `c5020186a9d364be027048cb98a5118b4d47cb99`, 8,853,386 bytes.
  - `data/UWTempo/ver3/training.bin`: blob `dc31632caa1dd612ee5ef36e71ebbe30ccc1bff4`, 5,074,872 bytes.
  - `data/UWTempo/ver3/testing.bin`: blob `de07c1420a88c8035a0f84e38f6d19041fa2388b`, 129,012 bytes.
- Rights anchor: upstream `WillWroble/MageZero@a494fd3e57581c35d7ab0e1a2a22ddfa16628cc3` adds the project MIT license while retaining both historical ONNX exports. Prefer the ONNX artifact for #113 provenance. Canonical SHA-256 is still unresolved until exact binary bytes can be materialized and verified; do not infer it from the Git blob SHA-1.
- Compatible checkpoint-era network: dense 4000-bit actor state -> 512 -> 256 shared trunk, 128-way policy head, tanh scalar value head; 2,212,993 parameters. Later EmbeddingBag-era exporters are not a reproducibility source for this ONNX.
- Exact XMage-side source/data pairing is recoverable at `WillWroble/mage@95391f5d35c980f13ade2fb0369f941aed2a6827` (MIT). Its committed `Mage.Tests/training.bin` and `testing.bin` are byte-identical by Git blob identity to MageZero UWTempo/ver3 above. The extraction test explicitly uses `UWTempo.dck` as PlayerA and `simplegreen.dck` as PlayerB.
- Crucially, the serialization sidecars required to interpret the checkpoint are also committed in that exact XMage tree:
  - `Mage.Tests/features_mapping.ser`: blob `0db8f8a0c459f580fd97105321f0c3a0f502d18d`, 1,135,107 bytes.
  - `Mage.Tests/actions_mapping.ser`: blob `3aa6a115d8f4db185bd9b51bd32b047e7fd27f3b`, 2,814 bytes.
  - The action map is a recoverable 51-entry string->index map. It assigns, for example, Pass=0, Play Adarkar Wastes=1, Play Island=24, Cast Spell Pierce=37, and Cast No More Lies=40. The map also contains simplegreen/opponent-era actions because mappings were persistent across extraction runs; do not assume all 51 indices occur as UWTempo policy labels without inspecting the corpus.
- Feature reconstruction is deterministic in source: `features_mapping.ser` stores the hierarchical feature map plus `ignoreList`; `StateEncoder.getCompressedVector()` scans raw feature indices in ascending order, skips ignored indices, and packs the first 4000 surviving features. Therefore the frozen legacy feature contract is in principle reproducible without XMage at runtime once the Java serialization artifact is decoded.
- Information boundary: checkpoint-era `StateEncoder.processOpponentState()` includes public opponent battlefield/graveyard and opponent hand count only, not hidden hand identities. Historical `ComputerPlayerMCTS.createMCTSGame()` also randomizes acting-library order and replaces each opponent hidden hand by shuffling it back into that opponent library and redrawing the same count before search. Classify supervision as search-teacher provenance, but do not conflate it with deterministic hidden-hand leakage.

### Legacy evaluation and action-vocabulary qualification

The exact committed `testing.bin` contains 250 fixed-size records and exercises only 13 of the 51 persisted action indices. `Pass` is 155/250 records (62%). The held-out artifact contains no Spell Pierce or No More Lies policy labels despite both actions existing in the mapping.

The checkpoint-era test extractor describes its sample as “5 random states,” but the source actually uses `labeledStateBatch.subList(0, 5)` for each of 50 games. This systematically selects the first five recorded macro decisions per game. The historical roughly 90% policy-accuracy claim is therefore early-game, class-imbalanced, deck-local evidence rather than broad gameplay-policy qualification. A Pass-only classifier already scores 62% on the committed test artifact.

The value label is also not a terminal win probability: the source blends a tanh-normalized evaluator score with discounted terminal result. The flat binary stores state bits, action index, and value only; it does not retain a game/source identifier or RNG seed. The committed artifacts alone therefore cannot prove group-disjoint train/test leakage. #113/#114 must recover independent generation provenance sufficient for leakage grouping or fail closed.

There is also a deployment hazard in the policy vocabulary. The network emits 128 policy logits, while the persisted action map has only 51 strings. Checkpoint-era `MCTSNode.expand()` passes current legal actions through mutable `ActionEncoder.addAction()` and directly indexes `policy[idx]`. Previously unseen action strings can therefore be assigned indices 51-127 that were never positive training targets, and dynamic growth to index >=128 can exceed the policy vector.

Commander Gym must not reproduce this behavior. Freeze the recovered action vocabulary, map only exact verified action semantics to Argentum legal actions, and fail closed on unsupported actions. This materially downgrades direct legacy policy-head transfer; the frozen 256d shared trunk/value representation remains the preferred first #114 probe.

### Bounded #114 transfer experiment

1. #113-bind the exact ONNX, mapping sidecars, extractor/XMage revision, corpus blobs, deck pair, and split construction. Materialize exact bytes and compute canonical SHA-256 only after verifying Git blob and byte size.
2. Decode `features_mapping.ser` and reproduce the 4000-bit actor-visible feature vector from frozen Argentum masked-seat fixtures. If feature semantics cannot be reproduced exactly, fail closed rather than inventing a mapping.
3. Probe the frozen 256d shared representation/value path against random/general baselines on input-only #114 fixtures before adapting policy.
4. Only use the 128-way policy head where exact action-index semantics and corresponding Argentum legal actions are established. Unsupported actions must fail closed.
5. Source-project policy accuracy is context only; split leakage and label distribution still require independent qualification.

This changes the legacy MageZero status from “historical bytes exist” to “artifact is plausibly adaptable”: both the model and the exact state/action interpretation sidecars are publicly recoverable. Remaining hard gates are binary materialization/canonical hashing, exact Java-sidecar decoding, and #114 leakage-safe qualification.
