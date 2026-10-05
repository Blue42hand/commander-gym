# Commander Gym local display

A deliberately thin, read-only kiosk dashboard for a Commander Gym host.

## Data sources

The display does not add a game-server seam. It reads only information already
available on the machine:

- Linux `/proc` and thermal sysfs for uptime/load/memory/temperature.
- `systemctl is-active` for configured services.
- `git rev-parse` for checked-out Commander Gym and Argentum revisions.
- The existing `COMMANDER_GYM_SIDECAR_PROVENANCE` JSONL stream for masked pilot
  observations, recent game log entries, choices, and aggregate callback counts.

Because provenance is emitted at pilot callbacks, the live-game panel is a
near-live *pilot view*, not an authoritative spectator connection. It can lag while
no AI decision is being requested. This is intentional for v1: no Argentum changes
or duplicate rules/state API are introduced.

## Run

Set the sidecar and display to the same provenance file:

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
dashboard refreshes every two seconds and binds only to loopback by default.

## Scope

v1 intentionally does **not** add OpenAI calls, model-generated explanations,
Argentum endpoints, a database, or historical experiment storage. Pilot rationale
is shown only when the existing choice metadata already contains a reason/rationale.
Training counters are computed from the last 500 provenance records.
