# Direct Linux console live spectator

No desktop environment, browser, or web spectator is used. The display connects to
Argentum's existing public spectator WebSocket protocol.

## Install

```sh
python3 -m pip install websocket-client
python3 -m commander_gym.console_spectator --server ws://127.0.0.1:18080/game --auto
```

Use the actual **native Argentum game-server** address and active game session ID. For the current headless installation, the native endpoint was verified at `ws://127.0.0.1:18080/game`; the HTTP facade on port 8180 and bearer-protected gateway on 8082 are not interchangeable with it.
The existing `/game` WebSocket endpoint accepts an anonymous `connect` message,
returns `connected`, then accepts `spectateGame`. The console subscribes,
renders each matching `spectatorStateUpdate` snapshot, and reconnects on
transport loss. An explicit Argentum error fails visibly rather than silently
pretending the game is available.

For the built-in physical monitor, run from an existing Linux virtual console or
launch it using `openvt`. The process writes ANSI terminal text directly to
stdout; it needs neither X11 nor Wayland. Its WebSocket client is the only extra
Python runtime dependency.

## Privacy and boundaries

Only spectator updates are accepted for rendering. Ordinary player state
updates and pilot provenance are ignored; spectators never receive private hands.
The display is read-only and does not submit game actions. The server-supplied
public data determines which cards and fields can be shown.

Every seat has its own section with its published name, life total, hand count,
and battlefield from the already-masked spectator `gameState`. Cards are grouped
by current controller (including control changes), with tapped status and
creature power/toughness. Identical permanents are counted together; card lists
wrap to the display width. On a terminal, space is shared between seats so
large battlefields do not scroll earlier players off-screen; omitted battlefield
rows are counted explicitly. Redirected text output retains the complete lists.
Face-down names and hand/library contents are not
rendered. Older two-player snapshots fall back to the legacy summaries.

## Existing console service integration

The existing `commander-gym-display.service` occupies VT7 and runs with
`PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX`. Those settings
block a WebSocket connection, including loopback. Do **not** start two services
against VT7 at once. When swapping to the live spectator, configure a single
service with `PrivateNetwork=no` (or omit the directive), allow
`AF_INET AF_INET6`, point `ExecStart` to this module, and use `--auto`, an explicit `--game`, or `--auto --lobby LOBBY_ID`. An example unit is in
`deploy/systemd/commander-gym-console-spectator.service.example`.

## Verification

On the headless host, verified the WebSocket upgrade, `connect` response,
and `spectateGame` protocol error for a deliberately invalid session ID
against the native port. The transport unit tests pass (3/3). An actual
matching `spectatorStateUpdate` is covered by a simulated protocol test but
has not yet been observed from a real match on the host.

## Automatic game selection

`--auto` polls the native Argentum public live-game APIs:
`/api/quick-games/live` and `/api/tournaments/live`. It deterministically
selects the first active match by game session ID and attaches the native
spectator stream. Once a match ends, it resumes polling. When no match is
available, VT7 displays an idle/waiting message.

Private lobbies are omitted from public discovery. To follow a particular private
or public lobby, copy its ID from `/tournament/LOBBY_ID`:

```sh
python3 -m commander_gym.console_spectator --server ws://127.0.0.1:18080/game --auto --lobby LOBBY_ID
```

This checks the lobby status, connects as a fresh observer, and joins an
already-active lobby as a spectator using the native WebSocket protocol. It reads
`freeForAllGameStarting` for four-player pods or `activeMatches` for brackets,
then subscribes to the selected game's public spectator stream. Development
endpoints and player tokens are unnecessary. Discovery errors are displayed and
retried. The client never joins a waiting lobby, where joining would create a
playing seat.

The console does not enumerate arbitrary private lobbies. Without `--lobby`, it
follows only games published by the public APIs. Selecting a new private lobby
requires changing the configured lobby ID. Game selection does not start matches
or submit player actions. The renderer shows each seat's name, life total, and public battlefield.
