import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest

from commander_gym.catalog_mcp import SERVER_INSTRUCTIONS, build_server
from commander_gym.deckbuilding_methodology import (
    BUNDLE_SHA256, DOCUMENTS, EXPECTED_SHA256, EXPECTED_SIZE, SOURCE,
    SOURCE_COMMIT, SOURCE_SKILL_TREE, get_methodology,
)


class DeckbuildingMethodologyTests(unittest.TestCase):
    def test_exact_pinned_bundle_and_all_references(self):
        root = Path(__file__).resolve().parents[1] / "commander_gym" / "deckbuilding_methodology"
        self.assertEqual(set(EXPECTED_SHA256), set(DOCUMENTS))
        self.assertEqual(set(EXPECTED_SIZE), set(DOCUMENTS))
        version = None
        for document in DOCUMENTS:
            with self.subTest(document=document):
                data = (root / document).read_bytes()
                self.assertEqual(len(data), EXPECTED_SIZE[document])
                self.assertEqual(hashlib.sha256(data).hexdigest(), EXPECTED_SHA256[document])
                result = get_methodology(document)
                self.assertEqual(result["content"].encode("utf-8"), data)
                self.assertEqual(result["source"], SOURCE)
                self.assertEqual(result["source_commit"], SOURCE_COMMIT)
                self.assertEqual(result["source_skill_tree"], SOURCE_SKILL_TREE)
                self.assertEqual(result["document"], document)
                self.assertEqual([entry["name"] for entry in result["documents"]], list(DOCUMENTS))
                self.assertEqual([entry["sha256"] for entry in result["documents"]],
                                 [EXPECTED_SHA256[name] for name in DOCUMENTS])
                self.assertEqual([entry["size_bytes"] for entry in result["documents"]],
                                 [EXPECTED_SIZE[name] for name in DOCUMENTS])
                self.assertEqual([entry["source"] for entry in result["documents"]],
                                 [f"{SOURCE}/{name}" for name in DOCUMENTS])
                if version is None:
                    version = result["version"]
                self.assertEqual(result["version"], version)
                self.assertEqual(result["version"], BUNDLE_SHA256)
        main = get_methodology()["content"]
        for reference in DOCUMENTS[1:]:
            self.assertIn(f"({reference})", main)

    def test_rejects_unknown_or_changed_documents(self):
        with self.assertRaisesRegex(ValueError, "unknown methodology document"):
            get_methodology("../private")
        from unittest import mock
        with mock.patch.dict(EXPECTED_SHA256, {"SKILL.md": "0" * 64}):
            with self.assertRaisesRegex(RuntimeError, "incomplete or changed"):
                get_methodology()

    def test_mcp_tool_and_instructions(self):
        from mcp import Client

        async def run():
            with tempfile.TemporaryDirectory() as catalog_root:
                server = build_server(catalog_root)
                await check_server(server)

        async def check_server(server):
            self.assertEqual(server.instructions, SERVER_INSTRUCTIONS)
            self.assertIn("get_deckbuilding_methodology", server.instructions)
            self.assertIn("before building", server.instructions)
            async with Client(server) as client:
                listed = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(listed), {
                    "get_deckbuilding_methodology", "catalog_status", "search_tags",
                    "search_cards", "get_card", "list_engine_coverage", "get_engine_coverage",
                })
                tool = listed["get_deckbuilding_methodology"]
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertEqual(set(tool.input_schema["properties"]), {"document"})
                for document in DOCUMENTS:
                    result = await client.call_tool("get_deckbuilding_methodology",
                                                    {"document": document})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content, get_methodology(document))
                rejected = await client.call_tool("get_deckbuilding_methodology",
                                                  {"document": "../private"})
                self.assertTrue(rejected.is_error)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
