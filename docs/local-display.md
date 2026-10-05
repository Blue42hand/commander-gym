# Commander Gym local display

A deliberately thin, read-only kiosk dashboard for a Commander Gym host.

## Data sources

The display does not add a game-server seam. It reads only information already
available on the machine:

- Linux `/proc` and thermal sysfs for uptime/load/memory/temperature.
- `systemctl is-active` for configured services.
- `git rev-parse` for checked-out Commander Gym and Argentum revisions.
- The existing `COMMANDER_GYM_SIDECAR_PROVENANCE` JSONL stream for a restricted
  summary of pilot state and aggregate callback counts. The API does not return
  player IDs, hand contents, choice metadata, rationale, or game-log entries.

Because provenance is emitted at pilot callbacks, the pilot panel shows the
latest recorded snapshot, not an authoritative spectator connection. It can
remain stale while no AI decision is being requested. No Argentum changes or
duplicate rules/state API are introduced.

## Run

If policy telemetry is intended for this display, set the sidecar and display
to the same private provenance file:

```sh
export COMMANDER_GYM_SIDECAR_PROVENANCE=/var/lib/commander-gym/policy-provenance.jsonl
export COMMANDER_GYM_DISPLAY_PROVENANCE=/var/lib/commander-gym/policy-provenance.jsonl
export COMMANDER_GYM_DISPLAY_REPO_DIR=/opt/commander-gym
export COMMANDER_GYM_DISPLAY_ARGENTUM_DIR=/opt/argentum-engine
python3 -m commander_gym.local_display
```

Open `http://127.0.0.1:8090/` in the local browser and use its kiosk/fullscreen
mode. The included systemd example keeps the dashboard server alive after reboot.

Optional: set `COMMANDER_GYM_DISPLAY_SERVICES` to a comma-separated list of unit names. The
dashboard refreshes every two seconds and refuses non-loopback bind addresses.
Without a configured provenance file, it shows host and service status only.

## Scope

v1 intentionally does **not** add OpenAI calls, model-generated explanations,
Argentum endpoints, a database, or historical experiment storage. It omits pilot
rationale and game-log text because the source records can contain private seat
information. Training counters are computed from the last 500 provenance records.
