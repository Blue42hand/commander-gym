# Console live spectator (headless)

This is a **direct Linux terminal/TTY display**, not a web application. It needs no desktop environment, browser, X11, or Wayland.

Run: `python3 -m commander_gym.console_spectator --input /path/to/public-spectator.ndjson`

The input must be newline-delimited **Argentum SpectatorStateUpdate** messages, supplied by an authenticated spectator client/bridge. The console never consumes seat-masked policy provenance because that can contain private hands. It shows the published roster, first two legacy player summaries, visible battlefield names, phase, active/priority seats and changes.

**Integration still needed:** an authenticated Argentum WebSocket spectator bridge that writes these public snapshots to the console process. The existing server SpectatingHandler emits SpectatorStateUpdate, but this module deliberately does not invent authentication/session handshake details. It is a working console renderer and NDJSON reader, not yet an end-to-end live connection.

Use a Linux virtual terminal for direct display, for example `openvt -c 2 -- python3 -m commander_gym.console_spectator --input /path/to/public-spectator.ndjson` (requires console permissions). No web spectator is included.
