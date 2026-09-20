# Model-service endpoint configuration

Commander Gym may use a local or remote model service behind an artificial-player implementation, but the model connection is not part of the artificial-player contract and is not proxied through the Argentum orchestration gateway.

`commander_gym.model_service.ModelServiceEndpoint` is the provider-neutral transport boundary for that connection. It intentionally contains only a base URL, optional bearer credential, and timeout. Provider selection, model selection, prompts, routing policy, and strategy remain pilot-layer concerns.

## Environment variables

Set `COMMANDER_GYM_MODEL_URL` only when a model service is needed. Optional companion variables are `COMMANDER_GYM_MODEL_TOKEN` and `COMMANDER_GYM_MODEL_TIMEOUT` (seconds, default `60`). If `COMMANDER_GYM_MODEL_URL` is unset, the model service is disabled rather than guessed.

Loopback HTTP is allowed for same-host development, for example `http://127.0.0.1:11434/v1`. A non-loopback model endpoint must use HTTPS and bearer authentication. Credentials embedded in URLs, query strings, and URL fragments are rejected. The bearer token is excluded from the configuration object's repr.

This configuration does not broaden the public gateway. Argentum remains behind the authenticated narrow gateway, and a local model service should remain host-private unless a separately secured remote model endpoint is deliberately configured.

## Example

```python
import os

from commander_gym.model_service import model_service_from_environment

model_service = model_service_from_environment(os.environ)
if model_service is not None:
    # A provider adapter may consume model_service.base_url / timeout and inject the
    # configured credential into its own client. The pilot contract itself does not.
    ...
```

This separation is deliberate: #9 owns secure orchestration/configuration boundaries; #6 owns how an artificial player invokes a model and converts its result into an Argentum-native choice.
