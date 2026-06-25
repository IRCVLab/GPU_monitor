#!/usr/bin/env python3
"""Tests for AI advisor safety invariants."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_advisor import (
    ReadOnlyInspectionConfig,
    collect_readonly_evidence,
    readonly_config_from_env,
)


class ReadOnlyInspectionSafetyTest(unittest.TestCase):
    def test_inspection_is_disabled_by_default_and_does_not_touch_paths(self) -> None:
        config = readonly_config_from_env({})

        evidence = collect_readonly_evidence(["/definitely/missing"], config)

        self.assertFalse(config.enabled)
        self.assertEqual(evidence["enabled"], False)
        self.assertEqual(evidence["items"], [])
        self.assertEqual(evidence["rejected"], [])

    def test_rejects_paths_without_allowlisted_bounded_metadata_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-safe.") as root, tempfile.TemporaryDirectory(
            prefix="storage-viz-ai-outside."
        ) as outside:
            allowed = Path(root).resolve()
            inside = allowed / "project"
            inside.mkdir()
            escape = inside / "escape"
            escape.symlink_to(Path(outside).resolve(), target_is_directory=True)
            config = ReadOnlyInspectionConfig(enabled=True, allowed_roots=(allowed,))

            evidence = collect_readonly_evidence(
                ["relative/path", "/", "/etc", "bad\x00path", str(Path(outside) / "x"), str(escape)],
                config,
            )

        self.assertEqual(evidence["items"], [])
        rejected = {item["path"]: item["reason"] for item in evidence["rejected"]}
        self.assertEqual(rejected["relative/path"], "relative")
        self.assertEqual(rejected["/"], "root")
        self.assertEqual(rejected["/etc"], "system")
        self.assertEqual(rejected["bad\x00path"], "null-byte")
        self.assertEqual(rejected[str(Path(outside) / "x")], "outside-allowed-roots")
        self.assertEqual(rejected[str(escape)], "symlink-escape")

    def test_collects_metadata_only_without_file_contents_or_directory_walks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-evidence.") as root:
            allowed = Path(root).resolve()
            target = allowed / "run"
            nested = target / "nested"
            nested.mkdir(parents=True)
            (target / "secret.txt").write_text("TOP_SECRET_CONTENT", encoding="utf-8")
            (target / "model.ckpt").write_bytes(b"ckpt")
            (nested / "ignored.log").write_text("should not be listed at depth zero", encoding="utf-8")
            config = ReadOnlyInspectionConfig(enabled=True, allowed_roots=(allowed,), max_depth=0, max_entries=10)

            evidence = collect_readonly_evidence([str(target)], config)

        self.assertEqual(evidence["enabled"], True)
        self.assertEqual(evidence["rejected"], [])
        self.assertEqual(len(evidence["items"]), 1)
        item = evidence["items"][0]
        self.assertEqual(item["path"], str(target))
        self.assertEqual(item["kind"], "directory")
        self.assertEqual(item["entry_count"], 3)
        self.assertEqual(item["extension_counts"], {".ckpt": 1, ".txt": 1, "<dir>": 1})
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("TOP_SECRET_CONTENT", serialized)
        self.assertNotIn("ignored.log", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
