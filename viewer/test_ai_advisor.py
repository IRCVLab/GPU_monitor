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
    DEFAULT_MAX_LLM_RECOMMENDATIONS,
    DEFAULT_MODEL,
    OllamaProvider,
    TwoPassProviderMixin,
    urlrequest as advisor_urlrequest,
    advisor_analysis_prompt,
    advisor_translation_prompt,
    build_advisor_response,
    build_evidence_pack,
    collect_readonly_metadata,
    ensure_korean_user_text,
    filter_excluded,
    is_safe_target_path,
    rule_recommendations,
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
    def test_default_llm_timeout_allows_local_gpu_two_pass_startup(self) -> None:
        payload = AdvisorConfig.from_env()

        self.assertGreaterEqual(payload.timeout_sec, 120.0)
        self.assertEqual(payload.model, DEFAULT_MODEL)
        self.assertEqual(payload.max_llm_recommendations, DEFAULT_MAX_LLM_RECOMMENDATIONS)

    def test_ollama_request_bounds_context_length_to_avoid_huge_server_default(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"response":"{\\"ok\\":true}"}'

        def fake_urlopen(req: object, timeout: float) -> FakeResponse:
            captured["timeout"] = timeout
            captured["body"] = json.loads(getattr(req, "data").decode("utf-8"))
            return FakeResponse()

        old_urlopen = advisor_urlrequest.urlopen
        advisor_urlrequest.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            out = OllamaProvider()._complete_json("{}", AdvisorConfig(context_length=4096, num_predict=777, timeout_sec=123))
        finally:
            advisor_urlrequest.urlopen = old_urlopen  # type: ignore[assignment]

        self.assertEqual(out, {"ok": True})
        self.assertEqual(captured["timeout"], 123)
        self.assertEqual(captured["body"]["options"]["num_ctx"], 4096)
        self.assertEqual(captured["body"]["options"]["num_predict"], 777)

    def test_rule_only_recommendations_cover_cache_env_move_duplicate_and_blocked_scan(self) -> None:
        payload = build_advisor_response(
            synthetic_snapshot(),
            host_id="hinton",
            exclusions=[],
            config=AdvisorConfig(enabled=False, provider="mock", model="qwen2.5:14b"),
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

    def test_evidence_pack_deduplicates_repeated_candidate_evidence(self) -> None:
        snapshot = synthetic_snapshot()
        duplicate_item = {"path": "/data/archive/repeated.tar", "bytes": 10 * GiB, "uid": 1002, "owner": "carol", "mtime": 1600000000}
        snapshot["top_files"].append(dict(duplicate_item))
        snapshot["stale"].append(dict(duplicate_item))

        pack = build_evidence_pack(snapshot, AdvisorConfig(max_context_items=50), [])
        target = next(c for c in pack["candidates"] if c["target_path"] == "/data/archive/repeated.tar")
        evidence_keys = [(ev.get("type"), ev.get("label"), ev.get("value")) for ev in target["evidence"]]

        self.assertEqual(len(evidence_keys), len(set(evidence_keys)))

    def test_evidence_pack_strips_heavy_mount_trees_before_llm_prompting(self) -> None:
        pack = build_evidence_pack(synthetic_snapshot(), AdvisorConfig(max_context_items=10), [])

        self.assertTrue(pack["mounts"])
        self.assertNotIn("tree", pack["mounts"][0])

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
            config=AdvisorConfig(enabled=False, provider="mock", model="qwen2.5:14b"),
        )

        blocked_scan_targets = [rec["target_path"] for rec in payload["recommendations"] if rec["category"] == "blocked-scan"]
        self.assertEqual(blocked_scan_targets, ["/home/private"])
        self.assertTrue(all(is_safe_target_path(target, mount_roots=["/", "/ssd", "/data"]) for target in blocked_scan_targets))


    def test_rule_only_fallback_returns_korean_user_facing_output(self) -> None:
        payload = build_advisor_response(
            synthetic_snapshot(),
            host_id="hinton",
            exclusions=[],
            config=AdvisorConfig(enabled=False, provider="ollama", model="qwen2.5:14b"),
            max_items=3,
        )
        self.assertEqual(payload["output_language"], "ko")
        self.assertIn("추천", payload["summary"]["headline"])
        self.assertTrue(payload["recommendations"])
        for rec in payload["recommendations"]:
            self.assertRegex(rec["badge"], r"AI: ")
            self.assertRegex(rec["reason_short"], r"[가-힣]")
            self.assertRegex(rec["reason_detail"], r"[가-힣]")

    def test_korean_output_guard_fills_untranslated_llm_badges(self) -> None:
        payload = {
            "schema_version": 1,
            "host_id": "hinton",
            "summary": {"health": "warning", "headline": "Advisor found review candidates", "top_drivers": []},
            "recommendations": [valid_recommendation(category="stale-large", target_path="/data/archive/old.tar")],
        }
        payload["recommendations"][0]["badge"] = "AI: archive review"
        payload["recommendations"][0]["reason_short"] = "Large archive may be worth review."
        payload["recommendations"][0]["reason_detail"] = "Review owner before moving or deleting."

        out = ensure_korean_user_text(payload)

        self.assertRegex(out["summary"]["headline"], r"[가-힣]")
        self.assertRegex(out["recommendations"][0]["badge"], r"[가-힣]")
        self.assertRegex(out["recommendations"][0]["reason_short"], r"[가-힣]")

    def test_llm_prompt_pipeline_is_two_pass_english_analysis_then_korean_translation(self) -> None:
        pack = build_evidence_pack(synthetic_snapshot(), AdvisorConfig(max_context_items=3), [])
        rule_payload = rule_recommendations(pack)
        analysis_prompt = advisor_analysis_prompt(pack, rule_payload)
        translation_prompt = advisor_translation_prompt(rule_payload)
        self.assertIn("Think in English", analysis_prompt)
        self.assertIn("Do not translate", analysis_prompt)
        self.assertIn("Do NOT return full recommendations", analysis_prompt)
        self.assertIn("base_payload", analysis_prompt)
        self.assertNotIn('"evidence_pack"', analysis_prompt)
        self.assertIn("Translate", translation_prompt)
        self.assertIn("Korean", translation_prompt)
        self.assertNotEqual(analysis_prompt, translation_prompt)

    def test_two_pass_provider_limits_llm_work_and_marks_success_mode(self) -> None:
        class EchoProvider(TwoPassProviderMixin):
            def __init__(self) -> None:
                self.prompts: list[dict] = []

            def _complete_json(self, prompt: str, config: AdvisorConfig) -> dict:
                parsed = json.loads(prompt)
                self.prompts.append(parsed)
                if "base_payload" in parsed:
                    out = json.loads(json.dumps(parsed["base_payload"]))
                    out["mode"] = "rule-only"
                    return out
                out = json.loads(json.dumps(parsed["analysis_payload"]))
                out["mode"] = "rule-only"
                return out

        pack = build_evidence_pack(synthetic_snapshot(), AdvisorConfig(max_context_items=10), [])
        rule_payload = rule_recommendations(pack)
        provider = EchoProvider()

        payload = provider.synthesize(pack, rule_payload, AdvisorConfig(max_llm_recommendations=2, max_recommendations=50))

        self.assertEqual(payload["mode"], "rule+llm")
        self.assertEqual(len(payload["recommendations"]), 2)
        self.assertEqual(len(provider.prompts[0]["base_payload"]["recommendations"]), 2)

    def test_two_pass_provider_accepts_compact_llm_patches_and_preserves_safe_schema(self) -> None:
        class PatchProvider(TwoPassProviderMixin):
            def _complete_json(self, prompt: str, config: AdvisorConfig) -> dict:
                parsed = json.loads(prompt)
                if "base_payload" in parsed:
                    rec = parsed["base_payload"]["recommendations"][0]
                    return {
                        "schema_version": 1,
                        "host_id": parsed["base_payload"]["host_id"],
                        "summary": {"health": "warning", "headline": "English headline", "top_drivers": []},
                        "items": [
                            {
                                "id": rec["id"],
                                "action": "investigate",
                                "priority": rec["priority"],
                                "confidence": 0.91,
                                "risk": rec["risk"],
                                "badge": "AI: archive review",
                                "reason_short": "English short reason.",
                                "reason_detail": "English detailed reason.",
                                "suggested_next_step": rec["suggested_next_step"],
                            }
                        ],
                    }
                return {
                    "schema_version": 1,
                    "host_id": parsed["analysis_payload"]["host_id"],
                    "summary": {"headline": "한국어 제목", "top_drivers": []},
                    "items": [
                        {
                            "id": parsed["analysis_payload"]["items"][0]["id"],
                            "badge": "AI: 보관 검토",
                            "reason_short": "한국어 짧은 이유입니다.",
                            "reason_detail": "한국어 상세 이유입니다.",
                        }
                    ],
                }

        pack = build_evidence_pack(synthetic_snapshot(), AdvisorConfig(max_context_items=10), [])
        rule_payload = rule_recommendations(pack)

        payload = PatchProvider().synthesize(pack, rule_payload, AdvisorConfig(max_llm_recommendations=1))

        rec = payload["recommendations"][0]
        self.assertEqual(payload["mode"], "rule+llm")
        self.assertEqual(rec["action"], "investigate")
        self.assertEqual(rec["target_path"], rule_payload["recommendations"][0]["target_path"])
        self.assertEqual(rec["evidence"], rule_payload["recommendations"][0]["evidence"])
        self.assertRegex(rec["badge"], r"[가-힣]")
        self.assertRegex(payload["summary"]["headline"], r"[가-힣]")

    def test_analysis_prompt_requests_compact_items_not_full_schema_regeneration(self) -> None:
        pack = build_evidence_pack(synthetic_snapshot(), AdvisorConfig(max_context_items=3), [])
        prompt = advisor_analysis_prompt(pack, rule_recommendations(pack))

        self.assertIn('"items"', prompt)
        self.assertIn("compact", prompt.lower())
        self.assertNotIn("recommendation_required_fields", prompt)

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
