# Tolaria local inference rollout — November 10, 2026

Managed batch selection is active at the existing private `/gym/` panel. The
installed release is `9f2499bf7dc3e8d048ff92a155e15253bd0f2e04`, with a matching
adapter built against engine `12ab2f0ed5bd018334521dc52497e6444f26f5a9`. The isolated
server embeds that adapter; every other server archive member is byte-identical
to the installed base server. The catalog offers six local Bindings alongside
paid and synthetic execution. Local plans cannot use cloud Bindings, have a zero
provider spending fence, and retain request/token/decision/time limits.

The panel and batch supervisor select the new immutable release. Application
checks returned 200 for the owner and 403 without identity. Served batch UI bytes
match the release. The local batch unit's D-Bus `LoadCredential` array is empty;
network access is restricted to localhost and the configured inference IP.
No new paid or local game was started during this deployment.

Human local play is **activated**. Protected idle-only promotion completed at
sequence 14 after recovery validated the existing 6 GB recording archive. The
recorder is healthy, pending record writes are zero, and new-game admission is
open. The live native catalog, verified through the existing TLS WebSocket,
exposes six local Bindings alongside the six existing cloud Bindings.

The configured llmster endpoint, `http://192.168.0.242:1234`, is reachable.
The owner reloaded `qwen3.5-9b` with a 65,536-token context, and production model
readiness passed. A real inference using the exact production configuration and
a synthetic one-action observation returned the valid action in 2.74 seconds.
This verifies transport and action validation, not a completed human game.

The minimal human candidate is Gym
`b0802af8153a90eb59dc252416acd807bbdf1a03`, matched to the same engine. Its four
GitHub checks passed in run `42770223793`; 28 focused runtime/QA tests and 13 JVM
adapter tests passed. Draft PR: https://github.com/Blue42hand/commander-gym/pull/283.
It corrects QA catalog readiness and places local recorder IPC beside the private
cloud IPC directory, preserving the separate provider identities and access rules.
The candidate production profile is
`83bc09a3bf31a123d3bd5a4e4204f27d118bc68b6bd30ec48a504a658e61a07f`.

The isolated rollback test passed: a controlled publication failure restored the
previous profile, healthy and accepting. Local callback admission, provider
isolation, recorder peer access, failure cancellation, and recorder reopen checks
passed in the positive runs. Earlier failed runs remain retained: staging file
permissions, QA IPC placement, an unstable inventory read, and a final cleanup
health observation. The final positive run (`guest-11-local-positive`) passed all checks, including
unchanged recordings across recorder reopen, stopped role closure, and reacquired
writer locks. Operator cleanup takes a fresh healthy idle observation after
hashing the copied recordings; it retains the exact identity, drain, and inventory
equality validators. Failed runs remain retained separately.

A validated production qualification receipt was issued for the exact candidate
profile, bound to both isolated results and the supplemental production checks.
Following the model reload, the real-endpoint readiness check passed and the
protected promotion returned `promoted`. The qualification remained within its
24-hour validity window. Retained receipts include `activation-result.json`,
`post-activation-health.json`, `live-native-local-catalog.json`, and
`live-production-config-inference.json` in the candidate evidence bank.

Production supplemental checks passed for exact artifact readability, Python
dependencies, read-only credential delivery, local discovery under the actual
local service identity and network restrictions, original recording preservation,
and unchanged HTTPS ingress. Existing human play remains healthy at runtime
`83bc09a3bf31a123d3bd5a4e4204f27d118bc68b6bd30ec48a504a658e61a07f`, sequence 14.

Deployment checks passed 137 focused Python tests and 13 JVM adapter tests.
The initial Java 21 build invocation was rejected and rerun using sealed Java 25.
An unsuitable combined test working directory was corrected; the suites passed
from their required directories. These checks establish source behavior and
packaging, not a completed native game or human acceptance.

Private backups, exact unit/configuration observations, SQLite backups, test
status, and staging scripts are retained in
`/var/lib/commander-gym-local-rollout/20261110-9f2499b/`. Isolated guest evidence
is retained in their original staging banks. The current human candidate and
its evidence are under
`/srv/argentum-luna/prepared/llmster-human-b0802af-20261110/`.

To roll back only the batch/panel deployment, first resolve all active or queued
batches, then run as root:

```sh
python3 -B /var/lib/commander-gym-local-rollout/20261110-9f2499b/rollback-batches.py
```

The script refuses an unresolved active batch, restores the previous units,
configuration and panel selector, and retains databases, annotations and captures.
Human play is unaffected by that rollback.
