#!/usr/bin/env python3
"""Tests for the optional local AI cleanup advisor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_advisor import (
    AdvisorConfig,
    AdvisorValidationError,
    build_advisor_response,
    collect_readonly_metadata,
    filter_excluded,
    is_safe_target_path,
    validate_recommendations,
)


GiB = 1024 ** 3


def synthetic_snapshot() -> dict:
    return {
        "schema_version": 1,
        "hostname": "hinton",
        "scan_started_unix": 1719200000,
        "mounts": [
            {"path": "/", "df_use_pct": 64, "df_avail": 45 * GiB, "tree": {"name": "/", "bytes": 1, "children": []}},
            {"path": "/ssd", "df_use_pct": 96, "df_avail": 8 * GiB, "tree": {"name": "/ssd", "bytes": 1, "children": []}},
            {"path": "/data", "df_use_pct": 52, "df_avail": 500 * GiB, "tree": {"name": "/data", "bytes": 1, "children": []}},
        ],
        "top_files": [
            {"path": "/home/alice/.cache/pip/wheels/pkg.whl", "bytes": 7 * GiB, "uid": 1000, "owner": "alice", "mtime": 1710000000},
            {"path": "/home/alice/miniconda3/envs/nlp/lib/libtorch.so", "bytes": 12 * GiB, "uid": 1000, "owner": "alice", "mtime": 1700000000},
            {"path": "/ssd/alice/runs/exp1/checkpoint_epoch_001.pt", "bytes": 40 * GiB, "uid": 1000, "owner": "alice", "mtime": 1711000000},
            {"path": "/ssd/alice/runs/exp1/events.out.tfevents.1", "bytes": 2 * GiB, "uid": 1000, "owner": "alice", "mtime": 1711000000},
            {"path": "/data/proj/a/model.safetensors", "bytes": 5 * GiB, "uid": 1001, "owner": "bob", "mtime": 1705000000},
            {"path": "/data/proj/b/model.safetensors", "bytes": 5 * GiB, "uid": 1001, "owner": "bob", "mtime": 1705000001},
        ],
        "stale": [
            {"path": "/data/archive/old-dataset.tar", "bytes": 100 * GiB, "uid": 1002, "owner": "carol", "mtime": 1600000000, "age_days": 900},
        ],
        "blocked": [{"path": "/home/private", "reason": "permission denied"}],
    }


class AdvisorAnalyzerTest(unittest.TestCase):
    def test_rule_only_recommendations_cover_cache_env_move_duplicate_and_blocked_scan(self) -> None:
        payload = build_advisor_response(
            synthetic_snapshot(),
            host_id="hinton",
            exclusions=[],
            config=AdvisorConfig(enabled=False, provider="mock", model="qwen3.6:27b"),
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["host_id"], "hinton")
        self.assertEqual(payload["mode"], "rule-only")
        recs = payload["recommendations"]
        by_category = {rec["category"]: rec for rec in recs}
        self.assertEqual(by_category["pip-cache"]["action"], "delete")
        self.assertEqual(by_category["pip-cache"]["suggested_next_step"], "review-delete-command")
        self.assertEqual(by_category["env"]["action"], "investigate")
        self.assertEqual(by_category["checkpoint"]["action"], "move")
        self.assertEqual(by_category["checkpoint"]["suggested_next_step"], "move-to-hdd")
        self.assertEqual(by_category["duplicate"]["action"], "dedupe")
        self.assertEqual(by_category["blocked-scan"]["action"], "investigate")
        self.assertTrue(all(rec["target_path"].startswith("/") for rec in recs))

    def test_safety_filter_rejects_top_level_system_relative_and_mount_roots(self) -> None:
        mount_roots = ["/", "/ssd", "/data"]
        for bad in ["/", "/home", "/ssd", "/data", "relative/path", "/tmp/a\0b", "/etc/passwd", "/proc/cpuinfo"]:
            self.assertFalse(is_safe_target_path(bad, mount_roots=mount_roots), bad)
        self.assertTrue(is_safe_target_path("/home/alice/.cache/pip", mount_roots=mount_roots))
        self.assertTrue(is_safe_target_path("/ssd/alice/run/checkpoint.pt", mount_roots=mount_roots))

    def test_validate_recommendations_rejects_malformed_or_unsafe_model_output(self) -> None:
        malformed = {"schema_version": 1, "host_id": "hinton", "recommendations": [{"target_path": "/"}]}
        with self.assertRaises(AdvisorValidationError):
            validate_recommendations(malformed, mount_roots=["/"])

    def test_exclusions_filter_recommendation_path_pattern_and_action(self) -> None:
        payload = build_advisor_response(synthetic_snapshot(), host_id="hinton", exclusions=[], config=AdvisorConfig(enabled=False))
        filtered = filter_excluded(
            payload,
            [
                {"type": "action", "action": "move"},
                {"type": "path", "path": "/home/alice/.cache/pip"},
                {"type": "pattern", "pattern": "/data/proj/**"},
            ],
        )
        categories = {rec["category"] for rec in filtered["recommendations"]}
        self.assertNotIn("checkpoint", categories)
        self.assertNotIn("pip-cache", categories)
        self.assertNotIn("duplicate", categories)


class ReadonlyCollectorTest(unittest.TestCase):
    def test_readonly_metadata_is_disabled_by_default(self) -> None:
        with self.assertRaises(PermissionError):
            collect_readonly_metadata(["/home/alice/project"], AdvisorConfig(readonly_inspection=False, allowed_roots=["/home"]))

    def test_readonly_metadata_is_allowlisted_bounded_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-ro.") as tmp:
            root = Path(tmp).resolve()
            inside = root / "project"
            inside.mkdir()
            (inside / "notes.txt").write_text("secret contents must not be returned", encoding="utf-8")
            cfg = AdvisorConfig(readonly_inspection=True, allowed_roots=[str(root)], max_inspect_paths=2, max_inspect_depth=1, max_inspect_entries=10)
            evidence = collect_readonly_metadata([str(inside)], cfg)
            self.assertEqual(len(evidence), 1)
            item = evidence[0]
            self.assertEqual(item["path"], str(inside))
            self.assertIn("entry_count", item)
            self.assertNotIn("secret", repr(item).lower())
            with self.assertRaises(PermissionError):
                collect_readonly_metadata(["/etc"], cfg)
            if hasattr(Path, "symlink_to"):
                link = root / "escape"
                try:
                    link.symlink_to("/etc")
                except OSError:
                    return
                with self.assertRaises(PermissionError):
                    collect_readonly_metadata([str(link)], cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
