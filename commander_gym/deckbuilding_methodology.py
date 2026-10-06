"""Pinned, read-only copy of the installed Commander Deckbuilding skill.

The four Markdown files are copied byte-for-byte from the named skill package.
The tool exposes fixed document names only and verifies their pinned digests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal


SOURCE = "skill://user/6ac524a46d9c8191ac21a47200eb24b1/commander-deckbuilding"
SOURCE_COMMIT = "c320a1dea845397c3b1fa9c7535e219c7781939d"
SOURCE_SKILL_TREE = "f328c2cc07d5c8edb3994b05efcb605a355815d9"
BUNDLE_SHA256 = "32693bef7c899f3131db8301ba87d11f1040108de1417e60c3d64e5ddb65f970"
Document = Literal[
    "SKILL.md",
    "references/research-and-evidence.md",
    "references/validation-checklist.md",
    "references/revision-and-testing.md",
]
DOCUMENTS: tuple[Document, ...] = (
    "SKILL.md",
    "references/research-and-evidence.md",
    "references/validation-checklist.md",
    "references/revision-and-testing.md",
)
# Filled from the exact installed skill and its three references. A mismatch
# fails closed so the advertised version can never identify changed content.
EXPECTED_SHA256: dict[str, str] = {
    "SKILL.md": "4fdf6a088e348bcca6e8d093ce3532bf31faa38fce594864cb169e83227552b0",
    "references/research-and-evidence.md": "082d75b62c9c7c0349b6824abe2aef6bfbe61250a496d96700a2f54aedb2ecc4",
    "references/validation-checklist.md": "17d85ba6cdcbc51088bb935faebdb059ebd7ac07e881288b8153ac249d835441",
    "references/revision-and-testing.md": "d6ecfc322aca24aa7acc301c32c08cfebc51329daebb443274bd348d5edd0540",
}
EXPECTED_SIZE: dict[str, int] = {
    "SKILL.md": 9861,
    "references/research-and-evidence.md": 5167,
    "references/validation-checklist.md": 6805,
    "references/revision-and-testing.md": 6166,
}


def get_methodology(document: Document = "SKILL.md") -> dict[str, object]:
    if document not in DOCUMENTS:
        raise ValueError("unknown methodology document")
    root = Path(__file__).with_name("deckbuilding_methodology")
    contents: dict[str, str] = {}
    for name in DOCUMENTS:
        try:
            data = (root / name).read_bytes()
            content = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("pinned deckbuilding methodology is incomplete or changed") from exc
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != EXPECTED_SIZE.get(name) or digest != EXPECTED_SHA256.get(name):
            raise RuntimeError("pinned deckbuilding methodology is incomplete or changed")
        contents[name] = content
    manifest = [
        {"path": name, "size_bytes": EXPECTED_SIZE[name], "sha256": EXPECTED_SHA256[name]}
        for name in DOCUMENTS
    ]
    manifest_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(manifest_bytes).hexdigest() != BUNDLE_SHA256:
        raise RuntimeError("pinned deckbuilding methodology is incomplete or changed")
    return {
        "version": BUNDLE_SHA256,
        "source": SOURCE,
        "source_commit": SOURCE_COMMIT,
        "source_skill_tree": SOURCE_SKILL_TREE,
        "documents": [
            {"name": name, "source": f"{SOURCE}/{name}",
             "size_bytes": EXPECTED_SIZE[name], "sha256": EXPECTED_SHA256[name]}
            for name in DOCUMENTS
        ],
        "document": document,
        "content": contents[document],
    }
