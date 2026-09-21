# Argentum game-server adapter

This Commander Gym-owned JVM artifact implements vanilla Argentum's
`AiControllerProvider`/`AiPlayerController` seam. It forwards only callback arguments to the
loopback policy sidecar and never retains or calls `AiControllerContext.snapshot`.

Build it against an Argentum checkout:

```bash
ARGENTUM_ENGINE_DIR=/path/to/argentum-engine /path/to/argentum-engine/gradlew -p jvm-adapter test
```

The jar auto-registers `CommanderGymControllerProvider` as an `AiControllerProvider` when
`game.ai.mode=commander-gym`. Configure `commander-gym.sidecar.url` (a loopback HTTP URL),
`commander-gym.sidecar.token`, and optionally `commander-gym.sidecar.timeout-ms`. This module does
not change Argentum or add a second game-server API.
