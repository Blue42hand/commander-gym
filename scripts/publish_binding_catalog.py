"""One bounded, credential-free publication of committed catalog data."""
from pathlib import Path
import fcntl
import json
import os
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_host import root_json
from commander_gym.manual_runtime_profile import validate_profile, verify_artifacts
from commander_gym.live_binding_catalog import LiveBindingRegistry, atomic, canonical, publish_checkout
from commander_gym.recorder_bridge import _RecordingPilot, recording_registry


def main():
    if sys.argv[1:]: raise ValueError('fixed catalog publisher')
    profile = root_json(Path('/etc/argentum-luna/service-profile.json'))
    validate_profile(profile)
    verify_artifacts(profile, roles=frozenset({'launcher', 'sidecar', 'catalog'}))
    live = profile['liveCatalog']; store = Path(live['snapshotRoot'])
    base = Path(profile['artifacts']['catalog']['root'])
    registry = recording_registry(base / profile['catalog']['roster'], base)
    factory = lambda pilot, binding: _RecordingPilot(pilot)
    store.mkdir(parents=True, exist_ok=True, mode=0o750)
    def validate_decks(candidate):
        if not (store / 'current.json').exists():
            # The bootstrapping set was qualified with this exact native release.
            if candidate.binding_fingerprints != registry.binding_fingerprints:
                raise ValueError('initial_catalog_must_match_qualified_release')
            return
        volume = os.statvfs(store)
        if volume.f_bavail * volume.f_frsize < 32 * 1024**3:
            raise ValueError('catalog_storage_reserve')
        # Validation stays with the native rules implementation. Disable proxy
        # environment discovery and never send deck content outside loopback.
        client = build_opener(ProxyHandler({})); deadline = time.monotonic() + 20
        for item in candidate.profiles:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise ValueError('catalog_validation_timeout')
            request = Request('http://127.0.0.1:18080/api/decks/validate',
                data=canonical({'deckList': dict(item.deck_list), 'commander': item.commander,
                                'format': 'COMMANDER'}), headers={'Content-Type': 'application/json'}, method='POST')
            with client.open(request, timeout=min(3, remaining)) as response:
                raw = response.read(65537)
            if len(raw) > 65536 or json.loads(raw).get('valid') is not True:
                raise ValueError('native_deck_validation_failed')
    fd = os.open(store / '.publish.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = publish_checkout(Path(live['checkoutRoot']), store, live['roster'],
                                      pilot_factory=factory, allowed_pilots=registry.pilot_fingerprints, validate_decks=validate_decks)
        except (OSError, ValueError, KeyError, TypeError):
            # A rejected pull must not prevent restart with a valid prior catalog.
            prior = LiveBindingRegistry(store, pilot_factory=factory, allowed_pilots=registry.pilot_fingerprints)
            result = {'state': 'catalog-update-rejected', 'snapshot': prior._current}
        atomic(store / 'status.json', canonical({**result, 'observedUnix': time.time()}))
    print(result['state'])


if __name__ == '__main__':
    try: main()
    except Exception:
        print('catalog_unavailable', file=sys.stderr)
        raise SystemExit(1)
