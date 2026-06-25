#!/usr/bin/env python3
"""Tests for the optional local AI cleanup advisor."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def valid_recommendation(*, category: str = "blocked-scan", target_path: str = "/home/private") -> dict:
    return {
        "id": "test-rec",
        "action": "investigate",
        "category": category,
        "target_path": target_path,
        "related_paths": [],
        "mount": "",
        "owner": "unknown",
        "bytes": 0,
        "priority": "medium",
        "confidence": 0.7,
        "risk": "medium",
        "badge": "AI: test",
        "reason_short": "Test recommendation.",
        "reason_detail": "Test recommendation detail.",
        "evidence": [{"label": "test", "value": target_path}],
        "suggested_next_step": "inspect-owner",
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
        ssd_checkpoint = next(rec for rec in recs if rec["category"] == "checkpoint" and rec["target_path"].startswith("/ssd/"))
        self.assertEqual(ssd_checkpoint["action"], "move")
        self.assertEqual(ssd_checkpoint["suggested_next_step"], "move-to-hdd")
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

    def test_validate_recommendations_rejects_unsafe_targets_for_every_category(self) -> None:
        mount_roots = ["/", "/data"]
        unsafe_targets = ["/", "/home", "/data", "relative/path", "/tmp/a\0b", "/etc/passwd", "/proc/cpuinfo"]

        for category in ("blocked-scan", "pip-cache", "env", "checkpoint", "duplicate"):
            for target_path in unsafe_targets:
                payload = {
                    "schema_version": 1,
                    "host_id": "hinton",
                    "summary": {"health": "warning", "total_recommendations": 1, "top_drivers": []},
                    "recommendations": [valid_recommendation(category=category, target_path=target_path)],
                }
                with self.subTest(category=category, target_path=target_path):
                    with self.assertRaisesRegex(AdvisorValidationError, "target_path is unsafe"):
                        validate_recommendations(payload, mount_roots=mount_roots)

    def test_rule_only_blocked_scan_skips_unsafe_blocked_targets(self) -> None:
        snapshot = synthetic_snapshot()
        snapshot["blocked"] = [
            {"path": "/", "reason": "root denied"},
            {"path": "/home", "reason": "home denied"},
            {"path": "/data", "reason": "mount denied"},
            {"path": "relative/path", "reason": "bad input"},
            {"path": "/home/private", "reason": "permission denied"},
        ]

        payload = build_advisor_response(
            snapshot,
            host_id="hinton",
            exclusions=[],
            config=AdvisorConfig(enabled=False, provider="mock", model="qwen3.6:27b"),
        )

        blocked_scan_targets = [rec["target_path"] for rec in payload["recommendations"] if rec["category"] == "blocked-scan"]
        self.assertEqual(blocked_scan_targets, ["/home/private"])
        self.assertTrue(all(is_safe_target_path(target, mount_roots=["/", "/ssd", "/data"]) for target in blocked_scan_targets))

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

    def test_readonly_metadata_rejects_outside_system_relative_null_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-ro.") as root, tempfile.TemporaryDirectory(prefix="storage-viz-ai-outside.") as outside:
            allowed = Path(root).resolve()
            inside = allowed / "project"
            inside.mkdir()
            escape = inside / "escape"
            escape.symlink_to(Path(outside).resolve(), target_is_directory=True)
            cfg = AdvisorConfig(readonly_inspection=True, allowed_roots=[str(allowed)], max_inspect_paths=10, max_inspect_depth=1, max_inspect_entries=10)

            rejected_paths = ["relative/path", "/", "/etc", "bad\x00path", str(Path(outside).resolve() / "x"), str(escape)]
            for path in rejected_paths:
                with self.subTest(path=path):
                    with self.assertRaises(PermissionError):
                        collect_readonly_metadata([path], cfg)

    def test_readonly_metadata_is_allowlisted_bounded_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-evidence.") as root:
            allowed = Path(root).resolve()
            target = allowed / "run"
            nested = target / "nested"
            nested.mkdir(parents=True)
            (target / "secret.txt").write_text("TOP_SECRET_CONTENT", encoding="utf-8")
            (target / "model.ckpt").write_bytes(b"ckpt")
            (nested / "ignored.log").write_text("should not be listed at depth zero", encoding="utf-8")
            cfg = AdvisorConfig(readonly_inspection=True, allowed_roots=[str(allowed)], max_inspect_paths=2, max_inspect_depth=0, max_inspect_entries=10)

            evidence = collect_readonly_metadata([str(target)], cfg)

        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item["path"], str(target))
        self.assertEqual(item["type"], "directory")
        self.assertEqual(item["entry_count"], 3)
        self.assertEqual(item["extensions"], {".ckpt": 1, ".txt": 1})
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("TOP_SECRET_CONTENT", serialized)
        self.assertNotIn("ignored.log", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
