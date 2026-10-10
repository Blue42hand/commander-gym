import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.qualify_manual_luna_release import CONFIGURATION, IMPORTS, QA_GATE, qualify


class ManualLunaReleaseTests(unittest.TestCase):
    def archive(self, entries):
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return result.getvalue()

    def test_release_requires_exact_pins_registration_and_embedded_adapter_bytes(self):
        native, gym = "a" * 40, "b" * 40
        adapter_bytes = self.archive({
            "META-INF/MANIFEST.MF": f"Argentum-Revision: {native}\r\nCommander-Gym-Revision: {gym}\r\n",
            IMPORTS: CONFIGURATION + "\n",
            "org/commandergym/argentum/CommanderGymControllerProvider.class": b"synthetic-class",
        })
        with TemporaryDirectory() as directory:
            root = Path(directory)
            adapter, server = root / "adapter.jar", root / "server.jar"
            adapter.write_bytes(adapter_bytes)
            entries = {
                "META-INF/MANIFEST.MF": f"Argentum-Revision: {native}\r\n",
                "BOOT-INF/lib/commander-gym-argentum-adapter.jar": adapter_bytes,
            }
            server.write_bytes(self.archive(entries))
            receipt = qualify(server, adapter, native, gym)
            self.assertFalse(receipt["deploymentAuthorized"])
            self.assertNotIn("qaCallbackGatePackaged", receipt)
            with self.assertRaisesRegex(ValueError, "admission gate missing"):
                qualify(server, adapter, native, gym, require_qa_callback_gate=True)
            entries[QA_GATE] = b"synthetic-class"
            server.write_bytes(self.archive(entries))
            self.assertTrue(qualify(server, adapter, native, gym,
                                    require_qa_callback_gate=True)["qaCallbackGatePackaged"])
            with self.assertRaisesRegex(ValueError, "pins"):
                qualify(server, adapter, native, "c" * 40)
            for changed in (b"mismatched", None):
                if changed is None:
                    entries.pop("BOOT-INF/lib/commander-gym-argentum-adapter.jar")
                else:
                    entries["BOOT-INF/lib/commander-gym-argentum-adapter.jar"] = changed
                server.write_bytes(self.archive(entries))
                with self.assertRaisesRegex(ValueError, "qualified adapter bytes"):
                    qualify(server, adapter, native, gym)
