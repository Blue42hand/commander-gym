"""Compose the existing recorder and merged bridge with exactly one capture."""
from pathlib import Path
import time

from .manual_runtime_launch import recorder_pins
from .manual_runtime_profile import require


def run_recorder(runtime, *, sidecar_gid: int, stopping, capture_factory=None,
                 reporter_factory=None, bridge_factory=None, registry_factory=None, disposition_publisher=None, sleep=time.sleep):
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
    capture = capture_factory(root, recorder_pins(runtime), acknowledged_incomplete_registry=Path(profile['paths']['registryPath']),
                              background_finalization=True)
    reporter, bridge = None, None
    try:
        registry = registry_factory(catalog / profile['catalog']['roster'], catalog)
        if 'liveCatalog' in profile:
            from .live_binding_catalog import LiveBindingRegistry
            from .recorder_bridge import _RecordingPilot
            registry = LiveBindingRegistry(profile['liveCatalog']['snapshotRoot'],
                pilot_factory=lambda pilot, binding: _RecordingPilot(pilot),
                allowed_pilots=registry.pilot_fingerprints)
        local_peer = {}
        if 'localInference' in profile:
            from .manual_runtime_local import local_recording_registry, RecordingRegistries, local_ipc
            local = profile['localInference']
            registry = RecordingRegistries(registry, local_recording_registry(profile), local['catalog']['bindings'])
            directory = local_ipc(profile)
            import os, stat
            info = directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and not directory.is_symlink()
                    and info.st_uid == profile['identities']['nativeUid'] and not info.st_mode & 0o027,
                    'local_ipc_custody')
            require(local['gid'] in {*os.getgroups(), os.getgid()}, 'local_recorder_group')
            os.chown(directory, -1, local['gid'])
            local_peer = dict(local_socket=directory / 'local.sock', local_uid=local['uid'], local_gid=local['gid'],
                              local_binding_ids=local['catalog']['bindings'])
        bridge = bridge_factory(capture, registry, native_socket=ipc / 'native.sock', sidecar_socket=ipc / 'sidecar.sock',
                                native_uid=profile['identities']['nativeUid'], sidecar_uid=profile['identities']['sidecarUid'], sidecar_gid=sidecar_gid, **local_peer)
        reporter = reporter_factory(capture, Path(profile['paths']['lifecycleSocket']))
        bridge.start()
        # Reporter.tick already scans; no additional capture scanner thread.
        reporter.start()
        while not stopping():
            if disposition_publisher is not None:
                disposition_publisher({'releaseId': runtime.runtime_id, 'gymSha': profile['gymSha'],
                                       'socketPath': profile['paths']['lifecycleSocket']}, reporter)
            sleep(0.2)
    finally:
        # A failed earlier close must not let later writers outlive the recorder.
        failure = None
        for owner in (reporter, bridge, capture):
            if owner is not None:
                try: owner.close()
                except Exception as error: failure = failure or error
        if failure is not None: raise failure
