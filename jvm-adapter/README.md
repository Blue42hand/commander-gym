# Argentum game-server adapter

This Commander Gym-owned JVM artifact implements vanilla Argentum's
`AiControllerProvider`/`AiPlayerController` seam. It forwards only callback arguments to the
loopback policy sidecar. Trusted `AiControllerContext.snapshot` state is never sent to policy;
it is consulted only when non-empty `ActionParams` must be applied by Argentum's native
`ActionParameterizer`.

Build it against an Argentum checkout:

```bash
ARGENTUM_ENGINE_DIR=/path/to/argentum-engine /path/to/argentum-engine/gradlew -p jvm-adapter test
```

The jar auto-registers `CommanderGymControllerProvider` as an `AiControllerProvider` when
`game.ai.mode=commander-gym`. Configure `commander-gym.sidecar.url` (a loopback HTTP URL),
`commander-gym.sidecar.token`, and optionally `commander-gym.sidecar.timeout-ms`. This module does
not change Argentum or add a second game-server API.
