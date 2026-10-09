"""Compose the existing recorder and merged bridge with exactly one capture."""
from pathlib import Path
import time

from .manual_runtime_launch import recorder_pins
from .manual_runtime_profile import require


def run_recorder(runtime, *, sidecar_gid: int, stopping, capture_factory=None,
                 reporter_factory=None, bridge_factory=None, registry_factory=None, sleep=time.sleep):
    from .native_game_capture import NativeGameCapture
    from .recording_health import RecordingHealthReporter
    from .recorder_bridge import RecorderBridge, recording_registry
    capture_factory = capture_factory or NativeGameCapture
    reporter_factory = reporter_factory or RecordingHealthReporter
    bridge_factory = bridge_factory or RecorderBridge
    registry_factory = registry_factory or recording_registry
    profile = runtime.profile
    require(type(sidecar_gid) is int and sidecar_gid > 0, 'sidecar_group_required')
    catalog = Path(profile['artifacts']['catalog']['root'])
    root = Path(profile['paths']['recordingRoot'])
    ipc = Path(profile['paths']['recorderSocket']).parent
    capture = capture_factory(root, recorder_pins(runtime), acknowledged_incomplete_registry=Path(profile['paths']['registryPath']))
    reporter, bridge = None, None
    try:
        registry = registry_factory(catalog / profile['catalog']['roster'], catalog)
        bridge = bridge_factory(capture, registry, native_socket=ipc / 'native.sock', sidecar_socket=ipc / 'sidecar.sock',
                                native_uid=profile['identities']['nativeUid'], sidecar_uid=profile['identities']['sidecarUid'], sidecar_gid=sidecar_gid)
        reporter = reporter_factory(capture, Path(profile['paths']['lifecycleSocket']))
        bridge.start()
        # Reporter.tick already scans; no additional capture scanner thread.
        reporter.start()
        while not stopping(): sleep(0.2)
    finally:
        # A failed earlier close must not let later writers outlive the recorder.
        failure = None
        for owner in (reporter, bridge, capture):
            if owner is not None:
                try: owner.close()
                except Exception as error: failure = failure or error
        if failure is not None: raise failure
