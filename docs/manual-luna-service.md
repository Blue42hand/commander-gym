# Manual Luna games in Argentum's shared GUI

This release adds Commander Gym's existing Binding profiles to the normal Argentum
catalog alongside keyless Engine AI. Argentum owns rules, legality, masked views,
manual lobby starts, native recording and cancellation. Gym owns the selected Pilot.
No launcher creates a game. No private catalog, credential or deck is shipped here.

## Matched build and inspection

Use clean checkouts of the reviewed native and Gym commits, JDK 21, and the
repository build semaphore. Run from the native checkout:

```sh
ARGENTUM_ENGINE_DIR=/absolute/native just gradle -p /absolute/gym/jvm-adapter test jar
just gradle :game-server:bootJar -PaiControllerAdapterJar=/absolute/gym/jvm-adapter/build/libs/commander-gym-argentum-adapter.jar
```

The optional native build property embeds the adapter in `BOOT-INF/lib`. It is not
a plugin downloaded at server startup. The adapter manifest pins both source
commits; the server manifest pins native source. Inspect the produced pair:

```sh
python3 scripts/qualify_manual_luna_release.py \
  --server-jar /absolute/server.jar --adapter-jar /absolute/adapter.jar \
  --native-sha <reviewed-native-commit> --gym-sha <reviewed-gym-commit> \
  --output /absolute/release-receipt.json
```

This command checks exact source pins, compiled provider registration, and byte
identity of the embedded adapter. It starts no service and reads no credential.
The matching frontend is built from the same native commit (`npm ci`, `npm run
build` in `web-client`); record its artifact hash in the operator release record.

## Runtime proposal — operator approval required before applying

Keep the native default `game.ai.mode=engine`. Configure:

```text
game.ai.enabled=true
game.ai.mode=engine
game.dev-endpoints.enabled=false
commander-gym.enabled=true
commander-gym.manual-only=true
commander-gym.sidecar.url=http://127.0.0.1:8083
commander-gym.sidecar.token=<private shared sidecar token>
commander-gym.sidecar.timeout-ms=120000
server.address=127.0.0.1
```

The native token is separate from the provider key. The sidecar process receives:

```text
OPENAI_API_KEY=<existing server-held key, injected only into this process>
COMMANDER_GYM_SIDECAR_TOKEN=<same private sidecar token>
COMMANDER_GYM_SIDECAR_HOST=127.0.0.1
COMMANDER_GYM_SIDECAR_PORT=8083
COMMANDER_GYM_OPENAI_MODEL=gpt-6-luna
COMMANDER_GYM_OPENAI_TIMEOUT=90
COMMANDER_GYM_OPENAI_MAX_ATTEMPTS=2
COMMANDER_GYM_MANUAL_UNCAPPED=true
COMMANDER_GYM_INSTANCE_ROOT=<existing private instance root>
COMMANDER_GYM_BINDING_CATALOG=<approved active Binding catalog under that root>
COMMANDER_GYM_RECORDING_ROOT=<existing private recording root>
COMMANDER_GYM_RECORDING_PINS=<private matched-release pins JSON>
```

Run the existing Binding entry point:
`python3 -m commander_gym.game_server_binding_openai_sidecar`. Do not use the
experimental human-three-Luna launcher or any batch/game-start tool. Unset all
`COMMANDER_GYM_OPENAI_BUDGET_*`, `COMMANDER_GYM_OPENAI_SESSION_*`,
`COMMANDER_GYM_PREFIX_*` and `COMMANDER_GYM_HUMAN_GUI_*` bounds for this distinct
manual service; conflicting bounds fail startup. Experimental capped services
retain their current behavior.

The key loading proposal is to reuse the operator's existing private key source
without displaying, copying into source, rotating, or replacing it. If that source
is an environment file with an `OPENAI_API_KEY` entry, the sidecar service alone
uses `EnvironmentFile=<exact existing private file>` (owner-only permissions).
Do not add it to the native, proxy, frontend or shell launcher environment. If the
existing source uses another format, the operator must supply its format/path
before choosing the loading command; no path or contents were inspected here.

Native recording remains enabled through its existing private runtime setup
(`-Dgame.recording.root=<private root>`, matching release/engine metadata). Keep
the Gym `engine`, `gym`, `models`, `decks`, `bindings`, `config`, and `rng` pins exact.
Preserve the existing journal ownership, lifecycle sockets, incomplete-history
acknowledgments and recording-health settings. Never make recording directories
or health detail public.

## Admission and technical bounds

The normal quick lobby and human Free-for-All pod mark their native session as a
manual human start. Automatic bracket matches and AI-only pods do not. The marker
persists across recovery; older unmarked sessions fail closed for manual Luna.
Each policy callback rechecks the server marker and persisted classification of a human seat (a reconnecting socket is not required).
The authenticated sidecar additionally requires the adapter's admission marker,
game identity and explicit Binding. This is server authorization, not a client
Boolean or network classification inferred by the Pilot.

Uncapped means no cumulative dollar, request, turn or game-duration cap. A callback
still has a 110-second whole-callback budget, at most 90 seconds per provider
request, and the configured attempt ceiling; the JVM allows 120 seconds. The
existing recovery Pilot retains transient-server retries and failure receipts.
Whole-request workers are killed and reaped on timeout. Removing spending limits
does not enable infinite technical retries or strategic fallback. Native cancel,
stale-callback rejection and game shutdown still stop native action delivery.
An in-flight policy callback is not cooperatively cancelled at the Python edge:
it may finish a provider request or technical retry after native user cancellation,
bounded by the callback deadline. Native shutdown rejects its action delivery.
Cancellation does not undo incurred provider charges; immediate cross-process
provider cancellation is not supplied by this release.

## Listener/access proposal

Retain `https://tolaria.taila3c720.ts.net/` and its existing tailnet TLS ingress.
Only the GUI/native HTTP+WebSocket ingress is reachable by users. Keep the native
backend and Gym policy/recording endpoints loopback-only. Keep Tailscale Funnel
disabled. Do not expose `8080` or `8083` to the WAN or add router port forwarding.

For the home LAN, bind the existing TLS reverse proxy to the server's **actual home
LAN interface address**, allow only its **actual home LAN subnet**, and proxy the
same GUI, `/api` and `/game` routes to the loopback backend. A broad RFC1918 or
100.64/10 source rule is insufficient: bind/interface scope must distinguish this
home LAN and this tailnet from other networks. Tailnet admission must be through
the actual Tailscale listener/ACL, not a forwarded header supplied by a caller.
Strip untrusted identity/forwarding headers at the proxy boundary.

Require the exact approved HTTPS GUI Origins on WebSocket upgrade and mutating
HTTP requests. Native currently accepts every WebSocket Origin; subnet filtering
alone would allow an unrelated webpage visited by a LAN user to open a socket.
Permit missing Origin only for separately approved operator tooling. Do not route
policy, recording, admin or development endpoints through the GUI proxy.

The LAN address/subnet, local HTTPS hostname/certificate path, existing key source,
private catalog/recording paths and current proxy configuration are deliberately
unresolved: only the sole host operator may inspect or change that runtime. The
parent should obtain those exact values from task
`01a11d9c-f74e-7676-ac7a-98223cc5400d` and present the concrete service/proxy diff.

Minimum approval before a persistent change: approve that exact diff to (1) reuse
the existing key in the sidecar only, (2) install/start the matched native+Gym
release with manual-only/uncapped settings, and (3) restrict/enable the GUI ingress
on the named home-LAN address/subnet and existing tailnet, including exact Origin
checks. Noah already authorized uncapped spending for games people manually start
on those networks; no additional spending authorization is needed. Review/merge
authorization does not authorize applying this runtime diff.
