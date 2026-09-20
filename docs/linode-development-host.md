# Linode development host

This document describes the persistent Commander Gym / Argentum development-test host proven on 2026-09-20.

## Boundary

```text
remote Commander Gym tooling
        |
authenticated HTTPS tunnel
        |
Commander Gym Argentum gateway (127.0.0.1:8082)
        |
Argentum Gym server          (127.0.0.1:8081)
```

Argentum remains the authoritative game/environment. Commander Gym owns orchestration and artificial-player behavior. Raw Argentum ports are not exposed publicly.

Administrator access is over Tailscale. Public SSH is not required.

## Host baseline

Reference host:

- Ubuntu 24.04 LTS
- JDK 21+
- Node.js 18+
- Python 3.12+
- Git / Git LFS
- Docker + Compose plugin
- current upstream `just` (Ubuntu 24.04's packaged 1.21 is too old for Argentum's grouped recipes)
- GitHub CLI
- Tailscale
- `cloudflared` when an HTTPS tunnel is needed

Recommended initial capacity for benchmark/development work: 8 dedicated vCPU, 16 GiB RAM.

## Filesystem layout

```text
/srv/argentum/repo                 pinned clean runtime checkout
/srv/commander-gym/repo            pinned clean gateway/orchestration checkout
/srv/commander-gym-private/repo    private assets
/srv/worktrees/argentum/           disposable development worktrees
/srv/worktrees/commander-gym/      disposable development worktrees
/srv/commander-gym-runs/           durable run artifacts

/var/lib/commander-gym/state/
/var/lib/commander-gym/snapshots/
/var/lib/commander-gym/datasets/
/var/lib/commander-gym/cache/
```

Do not develop directly in the runtime checkouts. The service wrappers deliberately fail closed when a pinned checkout changes revision or has dirty tracked files.

## Argentum Gym

Argentum provides `scripts/run-gym-server-service.sh`. It:

- verifies an expected Git revision;
- refuses dirty tracked files;
- exports the build revision;
- binds the Gym server to `127.0.0.1`;
- launches `:gym-server:bootRun`.

Install the example unit from `deploy/systemd/argentum-gym.service.example`, replacing `__ARGENTUM_REVISION__` with:

```bash
git -C /srv/argentum/repo rev-parse HEAD
```

The wrapper currently does not need executable mode when the systemd unit invokes it via `/bin/bash`.

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now argentum-gym
curl -sS http://127.0.0.1:8081/health
```

## Commander Gym gateway

The narrow gateway is `commander_gym.argentum_gateway`.

Security invariants:

- bind only to loopback;
- bearer authentication required;
- upstream must be loopback HTTP;
- exact route allowlist;
- gateway credentials are never forwarded upstream.

Generate a bearer secret outside the repository:

```bash
openssl rand -hex 32 | sudo tee /etc/commander-gym/gateway.token >/dev/null
sudo chown root:commander /etc/commander-gym/gateway.token
sudo chmod 640 /etc/commander-gym/gateway.token
```

Install `deploy/systemd/commander-gym-gateway.service.example`, replacing `__COMMANDER_GYM_REVISION__` with the pinned checkout revision.

The unit uses the repository's `scripts/run_argentum_gateway_service.sh`, which applies the same clean-checkout and expected-revision provenance guard as the Argentum service.

Local checks:

```bash
curl -i http://127.0.0.1:8082/health
# expected: 401

TOKEN="$(sudo cat /etc/commander-gym/gateway.token)"
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8082/status
```

## Remote HTTPS transport

Expose only `http://127.0.0.1:8082` through Cloudflare Tunnel or an equivalent authenticated/reverse-proxy transport. Do not expose Argentum's 8081 port.

A Cloudflare Quick Tunnel is sufficient for temporary integration proofs:

```bash
cloudflared tunnel --url http://127.0.0.1:8082
```

For durable operation, use a named/persistent tunnel and a controlled hostname.

## Direct orchestration proof

Set runtime-only client credentials:

```bash
export COMMANDER_GYM_ARGENTUM_URL='https://<gateway-hostname>'
export COMMANDER_GYM_ARGENTUM_TOKEN='<bearer-token>'
```

Then:

```bash
python3 -m commander_gym.remote_proof --config <env-config.json>
```

The proof is fail-closed and exercises:

```text
health / compatibility
create
observe
step OR structured decision
observe
dispose
authoritative disposal verification
final health / identity verification
```

The first live direct proof succeeded on 2026-09-20 against Argentum build
`dbb3e0577c7dd9e297dfc3bf52b42079c18db557` and schema
`argentum-gym-contract@v1.7-semantic-state-provenance`.

## Legacy relay

The GitHub Actions relay is transitional evidence only. Its workflow is manual-only and requires an explicit quarantine confirmation string.

Do not add features to it or restore automatic triggers. Normal orchestration uses the direct gateway through `ArgentumGymClient` / `ArgentumOrchestrator`.

## Updating a pinned runtime

1. Stop the affected service.
2. Update the clean runtime checkout with a fast-forward only.
3. Run the relevant tests.
4. Record the new revision.
5. Replace the revision placeholder/value in the installed systemd unit.
6. `systemctl daemon-reload`.
7. Start the service and verify health/status/schema.
8. Keep development changes in worktrees, not the runtime checkout.
