"""Real local Git pulls/commits and immutable catalog snapshots; no providers."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.identity import Binding, Pilot
from commander_gym.live_binding_catalog import CatalogReloadError, LiveBindingRegistry, publish_checkout, verify_snapshot
from commander_gym.recorder_bridge import _RecordingPilot
from test_game_server_binding_openai_sidecar import synthetic_catalog, write_json


class LiveBindingCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.repo = self.base/'repo'; self.repo.mkdir()
        self.roster = synthetic_catalog(self.repo).relative_to(self.repo).as_posix()
        self.store = self.base/'snapshots'
        self.factory = lambda pilot, binding: _RecordingPilot(pilot)
        pilot = Pilot.from_dict(json.loads((self.repo/'pilots/pilot.json').read_text()))
        self.allowed = {pilot.fingerprint()}
        self.git('init', '-q'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL)

    def commit(self):
        self.git('add', '.'); self.git('commit', '-qm', 'fixture')

    def publish(self):
        return publish_checkout(self.repo, self.store, self.roster, pilot_factory=self.factory, allowed_pilots=self.allowed)

    def reader(self):
        return LiveBindingRegistry(self.store, pilot_factory=self.factory, allowed_pilots=self.allowed)

    def change_binding(self):
        path = self.repo/'bindings/seat-a.json'
        binding = Binding.from_dict(json.loads(path.read_text()))
        write_json(path, replace(binding, revision='r2', metadata={'display': {'name': 'Updated Binding'}}).to_dict())
        self.commit()

    def test_pull_publishes_new_selection_and_retains_old_game_identity(self):
        first = self.publish(); reader = self.reader()
        old_id = reader.profile_payloads()[0]['id']
        old_seat = reader.create_seat('one', old_id)
        # A real fast-forward of the watched checkout, not a file timestamp heuristic.
        author = self.base/'author'
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(author)], check=True)
        original = self.repo; self.repo = author
        self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        self.change_binding(); self.repo = original
        self.git('pull', '--ff-only', str(author))
        second = self.publish(); new_id = reader.profile_payloads()[0]['id']
        self.assertNotEqual(first['snapshot'], second['snapshot']); self.assertNotEqual(old_id, new_id)
        self.assertEqual(reader.profile_payloads()[0]['displayName'], 'Updated Binding')
        self.assertEqual(reader.selection(old_id)[1], 'seat-a')
        self.assertEqual(old_seat._recording_lineage['binding']['revision'], 'r1')
        self.assertEqual(reader.create_seat('two', new_id)._recording_lineage['binding']['revision'], 'r2')
        self.assertEqual(self.reader().selection(old_id)[1], 'seat-a')

    def test_dirty_and_invalid_commits_preserve_previous_catalog(self):
        self.publish(); before = (self.store/'current.json').read_bytes()
        path = self.repo/self.roster; path.write_text('{')
        with self.assertRaisesRegex(CatalogReloadError, 'checkout_not_clean'): self.publish()
        self.commit()
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual((self.store/'current.json').read_bytes(), before)
        self.assertEqual(len(self.reader().profile_payloads()), 1)

    def test_old_snapshot_resolves_after_bounded_cache_eviction(self):
        first=self.publish();reader=self.reader();old=reader.profile_payloads()[0]['id']
        for index in range(6):
            (self.repo/'note.txt').write_text(str(index));self.commit();self.publish();reader.profile_payloads()
        self.assertLessEqual(len(reader._loaded),4)
        self.assertNotIn(first['snapshot'],reader._loaded)
        self.assertTrue(reader.contains(old))
        seat=reader.create_seat('old-seat',old)
        self.assertEqual(seat._recording_lineage['catalog']['sha256'],first['snapshot'])

    def test_native_deck_rejection_keeps_last_good_snapshot(self):
        self.publish(); before = (self.store/'current.json').read_bytes(); self.change_binding()
        def reject(registry): raise ValueError('unsupported_card_fixture')
        with self.assertRaisesRegex(ValueError, 'unsupported_card_fixture'):
            publish_checkout(self.repo, self.store, self.roster, pilot_factory=self.factory,
                             allowed_pilots=self.allowed, validate_decks=reject)
        self.assertEqual((self.store/'current.json').read_bytes(), before)

    def test_unknown_pilot_is_not_published(self):
        self.publish(); before = (self.store/'current.json').read_bytes()
        self.change_binding()
        with self.assertRaisesRegex(CatalogReloadError, 'pilot_not_installed'):
            publish_checkout(self.repo, self.store, self.roster, pilot_factory=self.factory, allowed_pilots=set())
        self.assertEqual((self.store/'current.json').read_bytes(), before)

    def test_changed_tree_during_publication_keeps_previous_selection(self):
        from commander_gym import live_binding_catalog as module
        self.publish(); before = (self.store/'current.json').read_bytes(); self.change_binding()
        head = module.clean_head(self.repo)
        with patch.object(module, 'clean_head', side_effect=[head, '0'*40]):
            with self.assertRaisesRegex(CatalogReloadError, 'checkout_changed'): self.publish()
        self.assertEqual((self.store/'current.json').read_bytes(), before)

    def test_symlinks_and_executable_files_are_rejected(self):
        self.publish(); before = (self.store/'current.json').read_bytes()
        (self.repo/'outside.json').symlink_to(self.base/'private.json'); self.commit()
        with self.assertRaisesRegex(CatalogReloadError, 'data_files'): self.publish()
        self.git('rm', 'outside.json'); p = self.repo/'hook.sh'; p.write_text('exit 99\n'); p.chmod(0o755); self.commit()
        with self.assertRaisesRegex(CatalogReloadError, 'data_files'): self.publish()
        self.assertEqual((self.store/'current.json').read_bytes(), before)

    def test_tampered_snapshot_and_bad_pointer_leave_loaded_catalog_working(self):
        selected = self.publish(); reader = self.reader(); previous = reader.profile_payloads()
        (self.store/'current.json').write_text('{')
        self.assertEqual(reader.profile_payloads(), previous)
        self.assertEqual(reader.last_error, 'catalog_reload_rejected')
        directory, _ = verify_snapshot(self.store, selected['snapshot'])
        p = directory/'bindings/seat-a.json'; p.chmod(0o640); p.write_text('{}')
        with self.assertRaisesRegex(CatalogReloadError, 'snapshot_file_changed'): verify_snapshot(self.store, selected['snapshot'])

    def test_profile_path_cannot_escape_snapshot_store(self):
        self.publish(); reader = self.reader()
        for value in ('../../private', 's_'+'0'*64+'_99999', 'seat-a'):
            self.assertFalse(reader.contains(value))

    def test_running_sidecar_and_recorder_resolve_the_same_immutable_selection(self):
        from commander_gym.game_server_binding_openai_sidecar import BindingOpenAIGameServerConfig, build_binding_openai_game_server_sidecar
        from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfig
        from test_game_server_binding_openai_sidecar import FakeClient, free_port
        self.publish()
        config = BindingOpenAIGameServerConfig(
            sidecar=OpenAIGameServerSidecarConfig(api_key='fixture-only', token='fixture-token', port=free_port()),
            catalog_path=self.repo/self.roster, instance_root=self.repo, live_catalog_root=self.store)
        server = build_binding_openai_game_server_sidecar(config, client=FakeClient())
        self.addCleanup(server.server_close)
        recorder = self.reader()
        old_id = server.controller_profiles[0]['id']
        old_seat = server.resolve_seat('old-seat', old_id, 'old-game')
        self.change_binding(); self.publish()
        new_id = server.controller_profiles[0]['id']
        self.assertNotEqual(old_id, new_id)
        self.assertIs(server.resolve_seat('old-seat', old_id, 'old-game'), old_seat)
        new_seat = server.resolve_seat('new-seat', new_id, 'new-game')
        self.assertEqual(new_seat._recording_lineage, recorder.create_seat('new-seat', new_id)._recording_lineage)
        self.assertEqual(old_seat._recording_lineage, recorder.create_seat('old-seat', old_id)._recording_lineage)
