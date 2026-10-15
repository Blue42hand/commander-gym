# Direct Linux console live spectator

No desktop environment, browser, or web spectator is used. The display connects to
Argentum's existing public spectator WebSocket protocol.

## Install

```sh
python3 -m pip install websocket-client
python3 -m commander_gym.console_spectator --server ws://127.0.0.1:8080/game --game GAME_SESSION_ID
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

Currently the rich battlefield summary uses the server's first two legacy
spectator seats; additional players are shown in the published seat roster.
An extended 4-player board view can use the already-public `gameState` without
changing the native game server.

## Limitations

This feature still requires the user or a launcher to supply an active
`gameSessionId`. Automatic game discovery and switching between simultaneous
matches have not been implemented. The transport tests simulate Argentum's
protocol; physical-console deployment and live end-to-end testing on the server
are separate verification steps.


## Existing console service integration

The existing `commander-gym-display.service` occupies VT7 and runs with
`PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX`. Those settings
block a WebSocket connection, including loopback. Do **not** start two services
against VT7 at once. When swapping to the live spectator, configure a single
service with `PrivateNetwork=no` (or omit the directive), allow
`AF_INET AF_INET6`, point `ExecStart` to this module, and provide the
active game session ID. An example unit is in
`deploy/systemd/commander-gym-console-spectator.service.example`.

The module does not currently discover game IDs or manage a game queue.
A live game must be running, and the game ID must be supplied by the
launcher. This is not a graphical web spectator.

## Verification

On the headless host, verified the WebSocket upgrade, `connect` response,
and `spectateGame` protocol error for a deliberately invalid session ID
against the native port. The transport unit tests pass (3/3). An actual
matching `spectatorStateUpdate` is covered by a simulated protocol test but
has not yet been observed from a real match on the host.
