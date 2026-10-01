# Talor/Austinio ONNX source artifact catalog

Tracking: #112; intended for later #113 import.

This is a source-side catalog, not an `ExternalModelManifest`. It deliberately does not invent canonical Commander Gym artifact IDs before the bytes are materialized through #113's content-addressed store.

## Exact bundle boundary

Repository: `Talor-A/forge`.

The smallest commit that contains the complete currently referenced graph + external-data bundle is `1513b5d96af827b74998209d6de17a9fa84f803d` (2026-03-29), whose commit message says the nine `.onnx.data` files are required alongside the nine ONNX graphs. Use this commit as the acquisition revision, not the later branch head.

The logical model is composite:

- `state_encoder.onnx` graph last changed at `654ea79bbc29c15cdc7013068bcdda090a6dda0a`;
- the other eight ONNX graph files last changed at `f0296448106501e243acc8b460907cfc0ffb3e6e`, described upstream as the 22-epoch decision-head export;
- all nine external-data files last changed at `1513b5d96af827b74998209d6de17a9fa84f803d`.

Therefore no single graph file is a complete checkpoint. #113 must bind the whole 18-file bundle.

## Artifact inventory

`upstream_git_blob_sha1` is Git's content address and can verify acquisition from the pinned repository revision. `sha256` is recorded where the connected GitHub Contents path exposed the full bytes. Large GitHub blobs return no body through that path, so their canonical #113 SHA-256 remains pending byte materialization; the Git blob SHA-1 + exact size prevents ambiguity in the meantime.

| File | Bytes | Upstream Git blob SHA-1 | SHA-256 |
| --- | ---: | --- | --- |
| attack_head.onnx | 219247 | 98b5d2d0e27d97bd2e5226bfc5116dff8b9134c0 | a6364727fb6df98cc0a2bbc647c34a5898cac34cddc42a02529925dbe2285643 |
| attack_head.onnx.data | 7632896 | 638f06da82d1f88eb1219eee1c900855bb423f2c | pending materialization |
| binary_head.onnx | 24915 | 08be94859613b0090262dcb82613669405ab9da8 | af343be0ba50834e5f895c43ad021d248de3b7e9c243543c910d8ad59b82b65e |
| binary_head.onnx.data | 791552 | a95dd0b19e963fb38e658f7b44bdc6065b124887 | 8c3081ce72cdf374c6fdd850a63bbe2c2d7aa66209ebc30f21ad3ac7e10de90b |
| block_head.onnx | 83854 | e0b53351bb5a4246614cfe7fe793638d0a5dfa8b | 7e809029445e03919b691097e8bbae87ec92abba67591c17db246917deba6728 |
| block_head.onnx.data | 2892800 | 1697ac8bb524757548c868a976c2549ef38d9fed | pending materialization |
| card_select_head.onnx | 126405 | 5b69906c106b61bdc453d41e311fc551c333a664 | b6f7b11019c6d9e5ca36dbf276e183466e2d00c21853b40f6e3e0b4351a7d674 |
| card_select_head.onnx.data | 4473856 | f9429769ef5efad947ec2e97672ec10feb464361 | pending materialization |
| mulligan_head.onnx | 225621 | 9cea5c3bb6fac613481ea9620e01a4c16f96236e | 2a237e43b24cc82e6bdc815b1b866d086e0fdc8d85b56ce507e0c9410a48857f |
| mulligan_head.onnx.data | 7632896 | 8f6c1d1dc29b4789fb495d64f7cfa4440e804714 | pending materialization |
| priority_head.onnx | 65738 | 62491c2235258db499ef9b28a191992b169f8db4 | 791ba67497985f5d4c9e4228e7f7f7d310d33bb203721bcd3389d1596e9fdb02 |
| priority_head.onnx.data | 2172928 | 26c7f81014c7a1a49a656afb4757794621ea98fb | pending materialization |
| state_encoder.onnx | 1523687 | ff70b358345332510a3a84d47a3cb1f117faf2ae | pending materialization |
| state_encoder.onnx.data | 14090240 | ea34b99203cb70d41c5bdb33e44bdd0b4eec2c8c | pending materialization |
| target_head.onnx | 17795 | 5987a723b504e98e5cffbc4b2ae8cf75b3bf3f77 | 0cd2a136ed264c0c03578537d2ad1a6bb5a1188a2a11ea1b17c00f09946f2952 |
| target_head.onnx.data | 1314816 | 704abd50a453bf4c4c7cdca14551f8523e5ad0ff | pending materialization |
| value_head.onnx | 27592 | fa52adf0f18e48f4320c7ae41ab877ce5e12b7d7 | fb91e21cab648e28b9ec5e68a71e0695e7f67dd8a670261714e92d24857af647 |
| value_head.onnx.data | 793600 | c6135b538d0312f1e44d1cefa64cdef7c8860ebd | 50790df84a93a91ed56da1148ad887cf0f8b9537f15e01ceccaf12ff36f37a6c |

## Feature-contract correction

At the exact complete-bundle revision `1513b5d...`, the executable contract is 256 card features, not the stale 128-feature description still present in `docs/architecture.html`:

- `CardFeatures.FEATURE_SIZE = 256`;
- `tools/export_onnx.py` fixes `CARD_DIM = 256`, `GLOBAL_DIM = 96`, `ACTION_DIM = 64`, `STATE_DIM = 512`;
- `ONNXModelClient` uses the same dimensions.

Any Argentum adapter must follow that pinned source/runtime contract rather than the HTML architecture page.

## Information boundary

The 256d card representation includes `face_down` at feature 21 but independently computes the four-byte card-name hash at features 252-255 whenever `card.getPaperCard()` exists. There is no face-down guard around that identity hash. The state encoder includes acting-player hand, both battlefields, both graveyards, and stack, but not opponent hand.

Therefore the imported encoder is not seat-safe if Forge supplied the true identity of a face-down permanent. Commander Gym must build card features only from acting-seat-visible information and fail closed rather than reproducing Forge's hidden identity hash.

## #113-compatible import recipe without schema changes

1. Materialize all 18 pinned files from `Talor-A/forge@1513b5d...`.
2. Verify exact byte size and upstream Git blob SHA-1 for every file.
3. Put each file independently through Commander Gym's existing content-addressed artifact store, producing canonical `sha256:<64hex>` IDs.
4. Create a deterministic, path-sorted bundle-index JSON containing source revision plus each relative path, byte size, upstream Git blob SHA-1, and canonical artifact ID; store that JSON itself as an artifact.
5. Use the bundle-index artifact ID as `ExternalModelManifest.checkpoint_artifact_id`. This preserves #113's single-checkpoint field while transitively binding every ONNX graph and external-data file.
6. Set seat-safe qualification to unreviewed/rejected for the raw Forge feature path; the later #114 frozen-encoder probe must use a Commander Gym-owned actor-relative sanitizer.
7. For the first transfer experiment, load only the state-encoder graph + external data through an offline adapter; no Forge runtime dependency and no gameplay while #77/#72 remain gated.

The remaining import blocker is canonical SHA-256 materialization for the eight >1 MiB blobs. The connected GitHub Contents route does not return their binary body, so those hashes must be computed when #113's acquisition/import path materializes the bytes.
