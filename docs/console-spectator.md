# Direct Linux console live spectator

No desktop environment, browser, or web spectator is used. The display connects to
Argentum's existing public spectator WebSocket protocol.

## Install

```sh
python3 -m pip install websocket-client
python3 -m commander_gym.console_spectator --server ws://127.0.0.1:8080/game --game GAME_SESSION_ID
```

Use the actual Argentum game-server address and active game session ID.
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
