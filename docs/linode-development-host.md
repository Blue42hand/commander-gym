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

Expose only `http://127.0.0.1:8082` through Cloudflare Tunnel or an equivalent outbound reverse-proxy transport. Do not expose Argentum's 8081 port, the gateway's 8082 port, or add router/NAT port forwarding for either service.

A Cloudflare Quick Tunnel is sufficient only for temporary integration proofs:

```bash
cloudflared tunnel --url http://127.0.0.1:8082
```

Quick Tunnel hostnames are ephemeral and the foreground process is not the production transport. The durable path is a named tunnel with a controlled hostname and a boot-persistent `cloudflared` service. The current production-style development endpoint is `https://gym.commander-gym.com`.

### Durable named Cloudflare Tunnel

The live development deployment uses a remotely managed Cloudflare Tunnel publishing `gym.commander-gym.com` to the loopback-only Commander Gym gateway at `http://127.0.0.1:8082`. Raw Argentum remains private on `127.0.0.1:8081`.

A locally managed tunnel using the checked-in `deploy/cloudflared/config.yml.example` is also supported if needed; its tunnel credentials JSON is a secret and must stay outside Git.

For the remotely managed path, create the tunnel in the Cloudflare dashboard, install the connector on the host using the generated service-install token, and publish the hostname to `http://127.0.0.1:8082`. No public inbound rule is required for 8081 or 8082.

For the alternative locally managed path, create and DNS-route the tunnel from an administrator workstation that can complete Cloudflare browser login:

```bash
cloudflared tunnel login
cloudflared tunnel create commander-gym-argentum
# record the printed UUID as TUNNEL_UUID
cloudflared tunnel route dns "$TUNNEL_UUID" gym.example.com
```

`cloudflared tunnel login` creates the account authorization certificate used for management commands. The production host does not need that certificate merely to run the named tunnel; it needs the tunnel-specific credentials JSON produced by `tunnel create`.

Transfer only that tunnel credentials JSON to the host over the administrator path (for example Tailscale SSH/SCP), then install it as a root-readable secret:

```bash
sudo install -d -m 755 /etc/cloudflared
sudo install -o root -g root -m 600 /tmp/$TUNNEL_UUID.json \
  /etc/cloudflared/$TUNNEL_UUID.json
```

Render the repository template on the host:

```bash
cd /srv/commander-gym/repo
HOSTNAME='gym.example.com'
sed \
  -e "s/__TUNNEL_UUID__/$TUNNEL_UUID/g" \
  -e "s/__GATEWAY_HOSTNAME__/$HOSTNAME/g" \
  deploy/cloudflared/config.yml.example \
  | sudo tee /etc/cloudflared/config.yml >/dev/null

sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule "https://$HOSTNAME/status"
```

Stop the temporary Quick Tunnel process before installing the persistent service. A host should have one intended `cloudflared` service/control plane for this route.

Install and start Cloudflare's system service using the explicit configuration path:

```bash
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared --no-pager
```

The named tunnel is outbound from the host to Cloudflare, so no public inbound firewall rule is required for ports 8081 or 8082. Keep those services bound to loopback. Keep Tailscale as the administrator path.

Validate the external boundary before treating the tunnel as durable:

```bash
curl -i "https://$HOSTNAME/health"
# expected: 401 from the Commander Gym bearer gateway

TOKEN="$(sudo cat /etc/commander-gym/gateway.token)"
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  "https://$HOSTNAME/status"
```

Then run the direct orchestration proof through the named hostname. A durable deployment is not complete until `systemctl status cloudflared` is healthy after a host reboot and the direct proof still succeeds.

Because this endpoint is machine-to-machine rather than browser-facing, Cloudflare Browser Integrity Check must not block API clients on `gym.commander-gym.com`. In the live deployment, Browser Integrity Check is disabled specifically for this hostname via a Cloudflare configuration rule; bearer authentication remains enforced by the Commander Gym gateway.

If tunnel configuration changes, validate it first and restart the service explicitly:

```bash
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo systemctl restart cloudflared
```

Do not commit `cert.pem`, tunnel credential JSON files, gateway bearer tokens, Cloudflare API tokens, or environment files containing them.

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

The permanent-hostname proof also succeeded on 2026-09-20 through `https://gym.commander-gym.com`, including create, observe, step, observe, dispose, disposal verification, and post-disposal health verification.

## Retired GitHub relay

The temporary GitHub Actions/control-branch relay used for the bootstrap proof was removed from `main` on 2026-09-20 after the permanent direct path and reboot persistence were proven. Do not recreate it or add repository-write/Actions latency back into normal orchestration. Historical commits remain sufficient evidence of how the bootstrap proof worked.

The dedicated `argentum-relay-control` branch and the old `ARGENTUM_GATEWAY_URL` / `ARGENTUM_GATEWAY_TOKEN` repository secrets are no longer required by Commander Gym. Delete them from GitHub once no external automation depends on those legacy names. The live host token at `/etc/commander-gym/gateway.token` remains required for the direct gateway and is unrelated to the retired GitHub Actions secrets.

## Updating a pinned runtime

1. Stop the affected service.
2. Update the clean runtime checkout with a fast-forward only.
3. Run the relevant tests.
4. Record the new revision.
5. Replace the revision placeholder/value in the installed systemd unit.
6. `systemctl daemon-reload`.
7. Start the service and verify health/status/schema.
8. Keep development changes in worktrees, not the runtime checkout.
