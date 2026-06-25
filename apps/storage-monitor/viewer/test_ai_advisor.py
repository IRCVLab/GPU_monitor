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


class AdvisorAnalyzerTest(unittest.TestCase):
    def valid_recommendation(self, *, category: str = "blocked-scan", target_path: str = "/home/private") -> dict:
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

        for category in ("blocked-scan", "pip-cache"):
            for target_path in unsafe_targets:
                payload = {
                    "schema_version": 1,
                    "host_id": "hinton",
                    "summary": {"health": "warning", "total_recommendations": 1, "top_drivers": []},
                    "recommendations": [self.valid_recommendation(category=category, target_path=target_path)],
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

    def test_readonly_metadata_is_allowlisted_bounded_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="storage-viz-ai-ro.") as tmp:
            root = Path(tmp).resolve()
            inside = root / "project"
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
