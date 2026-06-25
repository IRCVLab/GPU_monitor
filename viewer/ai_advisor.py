#!/usr/bin/env python3
"""Optional local AI cleanup advisor for storage-viz.

The advisor is deliberately split into deterministic rule analysis plus optional
local model synthesis.  The model receives compact evidence only; it never gets
filesystem, shell, delete, move, or sudo authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import posixpath
import time
from typing import Any
from urllib import request as urlrequest
from urllib.error import URLError

DEFAULT_MODEL = "qwen3.6:27b"
ANALYZER_VERSION = "ai-advisor-v1"
GiB = 1024 ** 3

ACTIONS = {"delete", "move", "dedupe", "archive", "investigate", "keep"}
CATEGORIES = {
    "pip-cache",
    "package-cache",
    "env",
    "log",
    "checkpoint",
    "duplicate",
    "ssd-misplacement",
    "stale-large",
    "blocked-scan",
    "archive",
    "other",
}
PRIORITIES = {"critical", "high", "medium", "low"}
RISKS = {"low", "medium", "high"}
SYSTEM_PREFIXES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
)


class AdvisorValidationError(ValueError):
    """Raised when LLM/advisor output fails the strict recommendation schema."""


@dataclass(frozen=True)
class AdvisorConfig:
    enabled: bool = False
    provider: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = DEFAULT_MODEL
    timeout_sec: float = 20.0
    max_context_items: int = 80
    cache_dir: str = ""
    readonly_inspection: bool = False
    allowed_roots: list[str] = field(default_factory=list)
    max_inspect_paths: int = 20
    max_inspect_depth: int = 1
    max_inspect_entries: int = 200
    inspect_timeout_sec: float = 5.0
    max_recommendations: int = 50
    output_language: str = "ko"

    @classmethod
    def from_env(cls) -> "AdvisorConfig":
        return cls(
            enabled=_truthy(os.environ.get("STORAGE_VIZ_AI_ENABLED")),
            provider=os.environ.get("STORAGE_VIZ_AI_PROVIDER", "ollama").strip().lower() or "ollama",
            endpoint=os.environ.get("STORAGE_VIZ_AI_ENDPOINT", "http://127.0.0.1:11434").strip(),
            model=os.environ.get("STORAGE_VIZ_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout_sec=_float_env("STORAGE_VIZ_AI_TIMEOUT_SEC", 20.0),
            max_context_items=_int_env("STORAGE_VIZ_AI_MAX_CONTEXT_ITEMS", 80),
            cache_dir=os.environ.get("STORAGE_VIZ_AI_CACHE_DIR", "").strip(),
            readonly_inspection=_truthy(os.environ.get("STORAGE_VIZ_AI_READONLY_INSPECTION")),
            allowed_roots=_split_csv(os.environ.get("STORAGE_VIZ_AI_ALLOWED_ROOTS", "")),
            max_inspect_paths=_int_env("STORAGE_VIZ_AI_MAX_INSPECT_PATHS", 20),
            max_inspect_depth=_int_env("STORAGE_VIZ_AI_MAX_INSPECT_DEPTH", 1),
            max_inspect_entries=_int_env("STORAGE_VIZ_AI_MAX_INSPECT_ENTRIES", 200),
            inspect_timeout_sec=_float_env("STORAGE_VIZ_AI_INSPECT_TIMEOUT_SEC", 5.0),
            output_language=os.environ.get("STORAGE_VIZ_AI_OUTPUT_LANGUAGE", "ko").strip().lower() or "ko",
        )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_snapshot(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("snapshot must be a JSON object")
    return data


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    body = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:20]


def normalize_posix_path(path: str) -> str:
    normalized = posixpath.normpath(str(path or ""))
    return normalized if normalized != "." else ""


def path_depth(path: str) -> int:
    return len([part for part in normalize_posix_path(path).split("/") if part])


def is_safe_target_path(path: str, mount_roots: list[str] | None = None) -> bool:
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        return False
    normalized = normalize_posix_path(path)
    if normalized == "/" or path_depth(normalized) <= 1:
        return False
    for prefix in SYSTEM_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return False
    for root in mount_roots or []:
        root_norm = normalize_posix_path(root)
        if root_norm != "/" and normalized == root_norm:
            return False
    return True


def _mount_roots(snapshot: dict[str, Any]) -> list[str]:
    return [str(m.get("path")) for m in snapshot.get("mounts", []) if isinstance(m, dict) and m.get("path")]


def _mount_for(path: str, mounts: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] = {"path": "/"}
    best_len = 1
    for m in mounts:
        mp = str(m.get("path") or "/")
        if mp == "/":
            if best_len <= 1:
                best = m
            continue
        if (path == mp or path.startswith(mp + "/")) and len(mp) > best_len:
            best, best_len = m, len(mp)
    return best


def _owner_for(item: dict[str, Any], users_by_uid: dict[Any, str]) -> str:
    if item.get("owner"):
        return str(item.get("owner"))
    uid = item.get("uid")
    return users_by_uid.get(uid, f"uid {uid}" if uid is not None else "unknown")


def _parts(path: str) -> list[str]:
    return [part for part in normalize_posix_path(path).lower().split("/") if part]


def classify_path(path: str) -> list[str]:
    lower = normalize_posix_path(path).lower()
    parts = _parts(path)
    categories: list[str] = []
    if "/.cache/pip" in lower or "/pip/cache" in lower:
        categories.append("pip-cache")
    if any(token in lower for token in ("/.cache/huggingface", "/.cache/torch", "/npm/_cacache", "/conda/pkgs")):
        categories.append("package-cache")
    if (
        "/conda/envs/" in lower
        or "/miniconda3/envs/" in lower
        or "/anaconda3/envs/" in lower
        or any(part in {".venv", "venv"} for part in parts)
    ):
        categories.append("env")
    if any(part in {"logs", "log", "wandb", "tensorboard"} for part in parts) or "events.out.tfevents" in lower or lower.endswith(".log"):
        categories.append("log")
    if any("checkpoint" in part or part == "ckpt" for part in parts) or lower.endswith((".pt", ".pth", ".ckpt", ".safetensors")):
        categories.append("checkpoint")
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".zip", ".tmp")) or "__pycache__" in parts:
        categories.append("archive")
    return categories or ["other"]


def _target_for(path: str, category: str) -> str:
    normalized = normalize_posix_path(path)
    parts = [part for part in normalized.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    if category == "pip-cache":
        for i in range(len(lower_parts) - 1):
            if lower_parts[i] == ".cache" and lower_parts[i + 1] == "pip":
                return "/" + "/".join(parts[: i + 2])
    if category == "package-cache":
        for marker in ((".cache", "huggingface"), (".cache", "torch"), ("npm", "_cacache"), ("conda", "pkgs")):
            for i in range(len(lower_parts) - len(marker) + 1):
                if tuple(lower_parts[i : i + len(marker)]) == marker:
                    return "/" + "/".join(parts[: i + len(marker)])
    if category == "env":
        for marker in ("envs", ".venv", "venv"):
            if marker in lower_parts:
                idx = lower_parts.index(marker)
                keep = idx + (2 if marker == "envs" and idx + 1 < len(parts) else 1)
                return "/" + "/".join(parts[:keep])
    return normalized


def _evidence(label: str, value: Any, typ: str = "path_pattern") -> dict[str, Any]:
    return {"type": typ, "label": label, "value": value}


def build_evidence_pack(snapshot: dict[str, Any], config: AdvisorConfig | None = None, exclusions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    config = config or AdvisorConfig()
    mounts = [m for m in snapshot.get("mounts", []) if isinstance(m, dict)]
    mount_roots = _mount_roots(snapshot)
    users_by_uid = {u.get("uid"): str(u.get("name")) for u in snapshot.get("users", []) if isinstance(u, dict) and u.get("name")}
    raw_items = []
    for source in ("top_files", "stale"):
        for item in snapshot.get(source, []) or []:
            if isinstance(item, dict) and item.get("path"):
                raw_items.append((source, item))
    # Add visible tree nodes as directory candidates, without expensive traversal.
    for mount in mounts:
        tree = mount.get("tree")
        if isinstance(tree, dict):
            for node in _walk_tree(tree, str(mount.get("path") or "/"), max_nodes=config.max_context_items):
                raw_items.append(("tree", node))

    candidates_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_groups: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for source, item in raw_items:
        path = normalize_posix_path(str(item.get("path") or ""))
        if not is_safe_target_path(path, mount_roots=mount_roots):
            continue
        bytes_value = int(item.get("bytes") or 0)
        if bytes_value <= 0:
            continue
        owner = _owner_for(item, users_by_uid)
        mount = _mount_for(path, mounts)
        for category in classify_path(path):
            target = _target_for(path, category)
            if not is_safe_target_path(target, mount_roots=mount_roots):
                continue
            key = (category, target)
            row = candidates_by_key.setdefault(
                key,
                {
                    "category": category,
                    "target_path": target,
                    "paths": [],
                    "bytes": 0,
                    "owner": owner,
                    "mount": mount.get("path", "/"),
                    "mount_use_pct": mount.get("df_use_pct"),
                    "source": source,
                    "evidence": [],
                },
            )
            row["bytes"] += bytes_value
            row["paths"].append(path)
            ev = _evidence(f"matched {category}", path)
            ev_key = (ev.get("type"), ev.get("label"), ev.get("value"))
            if ev_key not in {(old.get("type"), old.get("label"), old.get("value")) for old in row["evidence"]}:
                row["evidence"].append(ev)
        base = posixpath.basename(path)
        if base and bytes_value >= GiB:
            duplicate_groups.setdefault((base, bytes_value, owner), []).append(
                {"path": path, "bytes": bytes_value, "owner": owner, "mount": mount.get("path", "/")}
            )

    duplicates = []
    for (base, bytes_value, owner), rows in duplicate_groups.items():
        unique_paths = sorted({r["path"] for r in rows})
        if len(unique_paths) < 2:
            continue
        duplicates.append({"basename": base, "bytes": bytes_value, "owner": owner, "paths": unique_paths})

    blocked = []
    for item in snapshot.get("blocked", []) or snapshot.get("blocked_paths", []) or []:
        if isinstance(item, dict) and item.get("path"):
            blocked.append({"path": str(item.get("path")), "reason": str(item.get("reason") or "blocked")})

    return {
        "analyzer_version": ANALYZER_VERSION,
        "snapshot_fingerprint": snapshot_fingerprint(snapshot),
        "host_id": str(snapshot.get("hostname") or "unknown"),
        "mounts": mounts,
        "mount_roots": mount_roots,
        "candidates": list(candidates_by_key.values())[: config.max_context_items],
        "duplicates": duplicates[:20],
        "blocked": blocked[:20],
        "exclusions": exclusions or [],
    }


def _walk_tree(node: dict[str, Any], mount_path: str, max_nodes: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def visit(cur: dict[str, Any], parent_path: str) -> None:
        if len(out) >= max_nodes:
            return
        name = str(cur.get("name") or "")
        if name == parent_path or name == "/":
            path = normalize_posix_path(parent_path)
        else:
            path = normalize_posix_path(parent_path.rstrip("/") + "/" + name)
        row = dict(cur)
        row["path"] = path
        out.append(row)
        for child in cur.get("children", []) or []:
            if isinstance(child, dict):
                visit(child, path)

    visit(node, mount_path)
    return out


def _priority(bytes_value: int, mount_use_pct: Any) -> str:
    use = int(mount_use_pct or 0)
    if use >= 95 or bytes_value >= 50 * GiB:
        return "critical"
    if use >= 90 or bytes_value >= 10 * GiB:
        return "high"
    if bytes_value >= GiB:
        return "medium"
    return "low"


def _rec_id(host_id: str, action: str, category: str, target: str, related: list[str] | None = None) -> str:
    key = json.dumps([host_id, action, category, target, sorted(related or [])], separators=(",", ":"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _make_rec(
    *,
    host_id: str,
    action: str,
    category: str,
    target_path: str,
    related_paths: list[str] | None = None,
    mount: str = "",
    owner: str = "unknown",
    bytes_value: int = 0,
    priority: str = "medium",
    confidence: float = 0.7,
    risk: str = "medium",
    badge: str = "AI: review",
    reason_short: str,
    reason_detail: str,
    evidence: list[dict[str, Any]] | None = None,
    suggested_next_step: str = "inspect-owner",
) -> dict[str, Any]:
    related_paths = related_paths or []
    return {
        "id": _rec_id(host_id, action, category, target_path, related_paths),
        "action": action,
        "category": category,
        "target_path": target_path,
        "related_paths": related_paths,
        "mount": mount,
        "owner": owner,
        "bytes": int(bytes_value or 0),
        "priority": priority,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "risk": risk,
        "badge": badge,
        "reason_short": reason_short,
        "reason_detail": reason_detail,
        "evidence": evidence or [],
        "suggested_next_step": suggested_next_step,
    }


def rule_recommendations(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    host_id = str(evidence_pack.get("host_id") or "unknown")
    recommendations: list[dict[str, Any]] = []
    for cand in evidence_pack.get("candidates", []):
        category = cand["category"]
        target = cand["target_path"]
        bytes_value = int(cand.get("bytes") or 0)
        mount = str(cand.get("mount") or "")
        owner = str(cand.get("owner") or "unknown")
        use_pct = cand.get("mount_use_pct")
        priority = _priority(bytes_value, use_pct)
        ev = [_evidence("candidate size", bytes_value, "size"), _evidence("mount use percent", use_pct, "mount_pressure")]
        ev.extend(cand.get("evidence", [])[:4])
        if category == "pip-cache":
            recommendations.append(
                _make_rec(
                    host_id=host_id,
                    action="delete",
                    category="pip-cache",
                    target_path=target,
                    mount=mount,
                    owner=owner,
                    bytes_value=bytes_value,
                    priority=priority,
                    confidence=0.9,
                    risk="low",
                    badge="AI: cache cleanup",
                    reason_short="pip cache is rebuildable and unusually large.",
                    reason_detail="The path matches .cache/pip and the evidence is size/path-pattern only. Review active jobs, then use the copy-only delete command if appropriate.",
                    evidence=ev,
                    suggested_next_step="review-delete-command",
                )
            )
        elif category == "package-cache":
            recommendations.append(
                _make_rec(
                    host_id=host_id,
                    action="delete",
                    category="package-cache",
                    target_path=target,
                    mount=mount,
                    owner=owner,
                    bytes_value=bytes_value,
                    priority=priority,
                    confidence=0.82,
                    risk="low",
                    badge="AI: package cache",
                    reason_short="Package/model cache is a likely rebuildable cleanup candidate.",
                    reason_detail="The path matches a known package or model-cache pattern. Delete only after confirming no active job relies on the local cache.",
                    evidence=ev,
                    suggested_next_step="review-delete-command",
                )
            )
        elif category == "env":
            recommendations.append(
                _make_rec(
                    host_id=host_id,
                    action="investigate",
                    category="env",
                    target_path=target,
                    mount=mount,
                    owner=owner,
                    bytes_value=bytes_value,
                    priority=priority,
                    confidence=0.78,
                    risk="high",
                    badge="AI: env review",
                    reason_short="Large environment directory; verify ownership and last use before cleanup.",
                    reason_detail="Conda/venv environments can be expensive but may contain active project dependencies. This advisor intentionally recommends investigation, not blind deletion.",
                    evidence=ev,
                    suggested_next_step="inspect-owner",
                )
            )
        elif category in {"checkpoint", "log"}:
            pressured = int(use_pct or 0) >= 85 or _looks_like_ssd_mount(mount)
            action = "move" if pressured else "archive"
            recommendations.append(
                _make_rec(
                    host_id=host_id,
                    action=action,
                    category=category,
                    target_path=target,
                    mount=mount,
                    owner=owner,
                    bytes_value=bytes_value,
                    priority=priority,
                    confidence=0.8,
                    risk="medium",
                    badge="AI: move to HDD" if action == "move" else "AI: archive",
                    reason_short=("Logs/checkpoints are on a high-pressure or fast storage mount." if action == "move" else "Historical logs/checkpoints may be archive candidates."),
                    reason_detail="Keep active/latest artifacts on fast storage, but move historical logs/checkpoints to cheaper HDD/archive storage after owner review. This never generates rm commands.",
                    evidence=ev,
                    suggested_next_step="move-to-hdd" if action == "move" else "inspect-owner",
                )
            )
        elif category == "archive":
            recommendations.append(
                _make_rec(
                    host_id=host_id,
                    action="archive",
                    category="stale-large",
                    target_path=target,
                    mount=mount,
                    owner=owner,
                    bytes_value=bytes_value,
                    priority=priority,
                    confidence=0.65,
                    risk="medium",
                    badge="AI: archive review",
                    reason_short="Large archive/temp artifact may be worth moving or pruning.",
                    reason_detail="The file extension suggests an archive or temporary artifact. Review project ownership before moving or deleting.",
                    evidence=ev,
                    suggested_next_step="inspect-owner",
                )
            )
    for dup in evidence_pack.get("duplicates", []):
        paths = [p for p in dup.get("paths", []) if is_safe_target_path(p, evidence_pack.get("mount_roots", []))]
        if len(paths) < 2:
            continue
        recommendations.append(
            _make_rec(
                host_id=host_id,
                action="dedupe",
                category="duplicate",
                target_path=paths[0],
                related_paths=paths[1:],
                mount="",
                owner=str(dup.get("owner") or "unknown"),
                bytes_value=int(dup.get("bytes") or 0) * len(paths),
                priority="medium",
                confidence=0.62,
                risk="medium",
                badge="AI: duplicate?",
                reason_short="Files share basename, size, and owner across multiple paths.",
                reason_detail="This is a lightweight duplicate heuristic without hashing. Compare contents or future scanner hashes before deleting any copy.",
                evidence=[_evidence("duplicate basename", dup.get("basename")), _evidence("same size", dup.get("bytes"), "size")],
                suggested_next_step="compare-duplicates",
            )
        )
    for blocked in evidence_pack.get("blocked", []):
        path = str(blocked.get("path") or "")
        if not is_safe_target_path(path, mount_roots=evidence_pack.get("mount_roots", [])):
            continue
        recommendations.append(
            _make_rec(
                host_id=host_id,
                action="investigate",
                category="blocked-scan",
                target_path=path,
                mount="",
                owner="unknown",
                bytes_value=0,
                priority="medium",
                confidence=0.7,
                risk="medium",
                badge="AI: scan gap",
                reason_short="The scan skipped at least one path, so totals may be incomplete.",
                reason_detail="Blocked paths reduce evidence quality. Investigate permissions or run a privileged scan before relying on cleanup recommendations for that area.",
                evidence=[_evidence("blocked path", path), _evidence("reason", blocked.get("reason"))],
                suggested_next_step="inspect-owner",
            )
        )
    return _payload(host_id, evidence_pack, "rule-only", recommendations)


def _looks_like_ssd_mount(mount: str) -> bool:
    lower = str(mount or "").lower()
    return any(token in lower for token in ("ssd", "nvme", "scratch", "fast"))


def _payload(host_id: str, evidence_pack: dict[str, Any], mode: str, recommendations: list[dict[str, Any]], error: str | None = None) -> dict[str, Any]:
    critical = sum(1 for rec in recommendations if rec.get("priority") == "critical")
    high = sum(1 for rec in recommendations if rec.get("priority") == "high")
    health = "critical" if critical else "warning" if high or recommendations else "ok"
    top_drivers = []
    for rec in recommendations[:3]:
        top_drivers.append(f"{rec.get('badge')}: {rec.get('target_path')}")
    payload = {
        "schema_version": 1,
        "host_id": host_id,
        "snapshot_fingerprint": evidence_pack.get("snapshot_fingerprint", ""),
        "generated_at_unix": int(time.time()),
        "mode": mode,
        "summary": {
            "health": health,
            "headline": "Advisor found review candidates" if recommendations else "No advisor findings",
            "top_drivers": top_drivers,
        },
        "recommendations": recommendations,
    }
    if error:
        payload["advisor_error"] = error
    return payload


def validate_recommendations(payload: dict[str, Any], mount_roots: list[str] | None = None, max_items: int | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdvisorValidationError("advisor payload must be an object")
    if payload.get("schema_version") != 1:
        raise AdvisorValidationError("schema_version must be 1")
    if not isinstance(payload.get("host_id"), str) or not payload["host_id"]:
        raise AdvisorValidationError("host_id is required")
    if not isinstance(payload.get("summary"), dict):
        raise AdvisorValidationError("summary is required")
    recs = payload.get("recommendations")
    if not isinstance(recs, list):
        raise AdvisorValidationError("recommendations must be a list")
    checked = []
    for idx, rec in enumerate(recs):
        if not isinstance(rec, dict):
            raise AdvisorValidationError(f"recommendation {idx} must be an object")
        _validate_rec(rec, idx, mount_roots or [])
        checked.append(rec)
        if max_items and len(checked) >= max_items:
            break
    out = dict(payload)
    out["recommendations"] = checked
    return out


def _validate_rec(rec: dict[str, Any], idx: int, mount_roots: list[str]) -> None:
    required = [
        "id",
        "action",
        "category",
        "target_path",
        "related_paths",
        "mount",
        "owner",
        "bytes",
        "priority",
        "confidence",
        "risk",
        "badge",
        "reason_short",
        "reason_detail",
        "evidence",
        "suggested_next_step",
    ]
    for key in required:
        if key not in rec:
            raise AdvisorValidationError(f"recommendation {idx} missing {key}")
    if rec["action"] not in ACTIONS:
        raise AdvisorValidationError(f"recommendation {idx} has invalid action")
    if rec["category"] not in CATEGORIES:
        raise AdvisorValidationError(f"recommendation {idx} has invalid category")
    if rec["priority"] not in PRIORITIES:
        raise AdvisorValidationError(f"recommendation {idx} has invalid priority")
    if rec["risk"] not in RISKS:
        raise AdvisorValidationError(f"recommendation {idx} has invalid risk")
    if not is_safe_target_path(str(rec["target_path"]), mount_roots=mount_roots):
        raise AdvisorValidationError(f"recommendation {idx} target_path is unsafe")
    try:
        confidence = float(rec["confidence"])
    except (TypeError, ValueError):
        raise AdvisorValidationError(f"recommendation {idx} confidence is not numeric") from None
    if not 0 <= confidence <= 1:
        raise AdvisorValidationError(f"recommendation {idx} confidence out of range")
    if int(rec.get("bytes") or 0) < 0:
        raise AdvisorValidationError(f"recommendation {idx} bytes out of range")
    if not isinstance(rec.get("related_paths"), list):
        raise AdvisorValidationError(f"recommendation {idx} related_paths must be a list")
    for path in rec.get("related_paths") or []:
        if not is_safe_target_path(str(path), mount_roots=mount_roots):
            raise AdvisorValidationError(f"recommendation {idx} related path is unsafe")
    if not isinstance(rec.get("evidence"), list) or not rec["evidence"]:
        raise AdvisorValidationError(f"recommendation {idx} evidence is required")
    for text_key in ("id", "badge", "reason_short", "reason_detail", "suggested_next_step"):
        if not isinstance(rec.get(text_key), str) or not rec[text_key]:
            raise AdvisorValidationError(f"recommendation {idx} {text_key} is required")


def filter_excluded(payload: dict[str, Any], exclusions: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not exclusions:
        return payload
    out = dict(payload)
    out["recommendations"] = [rec for rec in payload.get("recommendations", []) if not _is_excluded(rec, exclusions)]
    out["summary"] = dict(payload.get("summary") or {})
    out["summary"]["top_drivers"] = [f"{rec.get('badge')}: {rec.get('target_path')}" for rec in out["recommendations"][:3]]
    out["summary"]["headline"] = "Advisor found review candidates" if out["recommendations"] else "All advisor findings are excluded"
    out["summary"]["health"] = "warning" if out["recommendations"] else "ok"
    return out


def _is_excluded(rec: dict[str, Any], exclusions: list[dict[str, Any]]) -> bool:
    paths = [str(rec.get("target_path") or "")] + [str(p) for p in rec.get("related_paths") or []]
    for ex in exclusions:
        if not isinstance(ex, dict):
            continue
        typ = ex.get("type")
        if typ == "recommendation" and ex.get("id") == rec.get("id"):
            return True
        if typ == "action" and ex.get("action") == rec.get("action"):
            return True
        if typ == "category" and ex.get("category") == rec.get("category"):
            return True
        if typ == "owner" and ex.get("owner") == rec.get("owner"):
            return True
        if typ == "path" and ex.get("path"):
            excluded = normalize_posix_path(str(ex.get("path")))
            if any(path == excluded or path.startswith(excluded.rstrip("/") + "/") for path in paths):
                return True
        if typ == "pattern" and ex.get("pattern"):
            pattern = str(ex.get("pattern"))
            if any(fnmatch.fnmatch(path, pattern) for path in paths):
                return True
    return False


def _ko_action(action: str) -> str:
    return {"delete": "삭제 검토", "move": "HDD 이동", "dedupe": "중복 확인", "archive": "보관 검토", "investigate": "확인 필요", "keep": "유지"}.get(action, "검토")


def _ko_category(category: str) -> str:
    return {
        "pip-cache": "pip 캐시",
        "package-cache": "패키지 캐시",
        "env": "환경 디렉터리",
        "log": "로그/결과",
        "checkpoint": "체크포인트",
        "duplicate": "중복 후보",
        "ssd-misplacement": "SSD 위치 비효율",
        "stale-large": "오래된 대용량 파일",
        "blocked-scan": "스캔 누락",
        "archive": "압축/아카이브",
    }.get(category, "스토리지 항목")


def localize_payload_ko(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    recs = []
    for rec in payload.get("recommendations", []) or []:
        row = dict(rec)
        action = str(row.get("action") or "investigate")
        category = str(row.get("category") or "other")
        target = str(row.get("target_path") or "")
        if category in {"pip-cache", "package-cache"}:
            row["badge"] = "AI: 캐시 정리"
            row["reason_short"] = "다시 만들 수 있는 캐시가 비정상적으로 큽니다."
            row["reason_detail"] = "경로 패턴과 크기 근거만으로 제안합니다. 실행 중인 작업이 해당 캐시에 의존하지 않는지 확인한 뒤 삭제 명령 후보로 검토하세요."
        elif category == "env":
            row["badge"] = "AI: 환경 검토"
            row["reason_short"] = "큰 conda/venv 환경입니다. 소유자와 최근 사용 여부 확인이 필요합니다."
            row["reason_detail"] = "환경 디렉터리는 용량이 크지만 프로젝트 의존성을 포함할 수 있어 바로 삭제하면 위험합니다. 소유자와 활성 프로젝트 여부를 먼저 확인하세요."
        elif category in {"checkpoint", "log"} and action == "move":
            row["badge"] = "AI: HDD 이동"
            row["reason_short"] = "로그/체크포인트가 압박이 큰 빠른 저장소에 있습니다."
            row["reason_detail"] = "활성 최신 산출물은 유지하되, 과거 로그와 체크포인트는 소유자 확인 후 HDD/아카이브 저장소로 이동하는 편이 좋습니다. 이 추천은 삭제 명령을 만들지 않습니다."
        elif category == "duplicate":
            row["badge"] = "AI: 중복 확인"
            row["reason_short"] = "파일명, 크기, 소유자가 같은 중복 후보입니다."
            row["reason_detail"] = "해시 비교 전의 가벼운 중복 휴리스틱입니다. 내용을 비교하거나 향후 scanner hash 근거를 확인하기 전에는 삭제하지 마세요."
        elif category == "blocked-scan":
            row["badge"] = "AI: 스캔 누락"
            row["reason_short"] = "권한 문제로 스캔되지 않은 경로가 있어 전체 판단이 불완전합니다."
            row["reason_detail"] = "스캔 누락은 용량 근거 품질을 낮춥니다. 정리 추천을 신뢰하기 전에 권한이나 privileged scan 필요 여부를 확인하세요."
        else:
            row["badge"] = "AI: " + _ko_action(action)
            row["reason_short"] = _ko_category(category) + " 항목입니다. 삭제가 아니라 검토 대상으로 보세요."
            row["reason_detail"] = "크기, 위치, 경로 패턴 기반 추천입니다. 소유자와 활성 작업 여부를 확인한 뒤 이동/보관/삭제 여부를 결정하세요."
        recs.append(row)
    out["recommendations"] = recs
    out["summary"] = dict(payload.get("summary") or {})
    out["summary"]["headline"] = "AI 정리 추천이 있습니다" if recs else "AI 정리 추천이 없습니다"
    out["summary"]["top_drivers"] = [f"{r.get('badge')}: {r.get('target_path')}" for r in recs[:3]]
    out["output_language"] = "ko"
    return out


class MockProvider:
    def synthesize(self, evidence_pack: dict[str, Any], rule_payload: dict[str, Any], config: AdvisorConfig) -> dict[str, Any]:
        payload = localize_payload_ko(dict(rule_payload))
        payload["mode"] = "mock"
        payload["summary"] = dict(payload.get("summary") or {})
        payload["summary"]["headline"] = "테스트용 mock 추천입니다"
        return payload


class TwoPassProviderMixin:
    def _complete_json(self, prompt: str, config: AdvisorConfig) -> dict[str, Any]:
        raise NotImplementedError

    def synthesize(self, evidence_pack: dict[str, Any], rule_payload: dict[str, Any], config: AdvisorConfig) -> dict[str, Any]:
        analysis = self._complete_json(advisor_analysis_prompt(evidence_pack, rule_payload), config)
        analysis.setdefault("schema_version", 1)
        analysis.setdefault("host_id", evidence_pack.get("host_id"))
        analysis.setdefault("snapshot_fingerprint", evidence_pack.get("snapshot_fingerprint"))
        analysis.setdefault("generated_at_unix", int(time.time()))
        analysis.setdefault("mode", "rule+llm-analysis")
        analysis = validate_recommendations(analysis, mount_roots=evidence_pack.get("mount_roots"), max_items=config.max_recommendations)
        translated = self._complete_json(advisor_translation_prompt(analysis), config)
        translated.setdefault("schema_version", 1)
        translated.setdefault("host_id", analysis.get("host_id"))
        translated.setdefault("snapshot_fingerprint", analysis.get("snapshot_fingerprint"))
        translated.setdefault("generated_at_unix", int(time.time()))
        translated.setdefault("mode", "rule+llm")
        translated["output_language"] = "ko"
        return validate_recommendations(translated, mount_roots=evidence_pack.get("mount_roots"), max_items=config.max_recommendations)


class OllamaProvider(TwoPassProviderMixin):
    def _complete_json(self, prompt: str, config: AdvisorConfig) -> dict[str, Any]:
        body = json.dumps({"model": config.model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
        req = urlrequest.Request(config.endpoint.rstrip("/") + "/api/generate", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlrequest.urlopen(req, timeout=config.timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data.get("response")
        if not isinstance(text, str):
            raise AdvisorValidationError("ollama response did not include JSON text")
        return json.loads(text)


class OpenAICompatibleProvider(TwoPassProviderMixin):
    def _complete_json(self, prompt: str, config: AdvisorConfig) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": "Return strict JSON only for the storage-viz advisor schema."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        req = urlrequest.Request(config.endpoint.rstrip("/") + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlrequest.urlopen(req, timeout=config.timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(text, str):
            raise AdvisorValidationError("openai-compatible response did not include content")
        return json.loads(text)


def advisor_analysis_prompt(evidence_pack: dict[str, Any], rule_payload: dict[str, Any]) -> str:
    compact = {
        "task": "Think in English. Analyze storage cleanup recommendations from bounded evidence. Do not translate final user-facing text in this pass. Do not invent filesystem facts. Prefer move/archive/investigate over delete unless evidence is low-risk rebuildable cache.",
        "schema": "Return storage-viz advisor schema_version 1 JSON only. Keep target_path/action/category/id stable when evidence is weak.",
        "evidence_pack": evidence_pack,
        "rule_payload": rule_payload,
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def advisor_translation_prompt(analysis_payload: dict[str, Any]) -> str:
    compact = {
        "task": "Translate the final user-facing output to Korean. Preserve all ids, actions, categories, paths, byte counts, risk, confidence, evidence, and suggested_next_step exactly. Only translate badge, reason_short, reason_detail, summary.headline, and summary.top_drivers. Return strict JSON only.",
        "language": "Korean",
        "analysis_payload": analysis_payload,
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _provider(config: AdvisorConfig):
    if config.provider == "mock":
        return MockProvider()
    if config.provider == "openai-compatible":
        return OpenAICompatibleProvider()
    return OllamaProvider()


def build_advisor_response(
    snapshot: dict[str, Any],
    *,
    host_id: str | None = None,
    exclusions: list[dict[str, Any]] | None = None,
    config: AdvisorConfig | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    config = config or AdvisorConfig.from_env()
    pack = build_evidence_pack(snapshot, config, exclusions)
    if host_id:
        pack["host_id"] = host_id
    rule_payload = validate_recommendations(rule_recommendations(pack), mount_roots=pack.get("mount_roots"), max_items=max_items or config.max_recommendations)
    payload = localize_payload_ko(rule_payload) if config.output_language == "ko" else rule_payload
    if config.enabled:
        try:
            payload = _provider(config).synthesize(pack, rule_payload, config)
            payload.setdefault("schema_version", 1)
            payload.setdefault("host_id", pack.get("host_id"))
            payload.setdefault("snapshot_fingerprint", pack.get("snapshot_fingerprint"))
            payload.setdefault("generated_at_unix", int(time.time()))
            payload = validate_recommendations(payload, mount_roots=pack.get("mount_roots"), max_items=max_items or config.max_recommendations)
        except (AdvisorValidationError, json.JSONDecodeError, OSError, URLError, TimeoutError) as exc:
            payload = localize_payload_ko(rule_payload) if config.output_language == "ko" else dict(rule_payload)
            payload["advisor_error"] = f"LLM synthesis unavailable; rule-only recommendations shown: {exc}"
            payload["mode"] = "rule-only"
    payload = filter_excluded(payload, exclusions or [])
    payload["recommendations"] = payload.get("recommendations", [])[: (max_items or config.max_recommendations)]
    return payload


def collect_readonly_metadata(paths: list[str], config: AdvisorConfig) -> list[dict[str, Any]]:
    if not config.readonly_inspection:
        raise PermissionError("read-only AI inspection is disabled")
    if not config.allowed_roots:
        raise PermissionError("read-only AI inspection requires STORAGE_VIZ_AI_ALLOWED_ROOTS")
    if len(paths) > config.max_inspect_paths:
        raise ValueError("too many paths requested for read-only inspection")
    allowed = []
    for root in config.allowed_roots:
        root_path = Path(root).resolve()
        if str(root_path) == "/":
            raise PermissionError("/ is not an allowed AI inspection root")
        allowed.append(root_path)
    deadline = time.monotonic() + config.inspect_timeout_sec
    evidence = []
    for raw in paths:
        if time.monotonic() > deadline:
            raise TimeoutError("read-only inspection timed out")
        if not isinstance(raw, str) or "\0" in raw or not raw.startswith("/"):
            raise PermissionError("inspection path must be absolute and contain no null bytes")
        if not is_safe_target_path(raw, mount_roots=[]):
            raise PermissionError(f"unsafe inspection path: {raw}")
        path = Path(raw).resolve()
        if not any(_is_relative_to(path, root) for root in allowed):
            raise PermissionError(f"inspection path is outside allowed roots: {raw}")
        evidence.append(_metadata_for(path, config, deadline))
    return evidence


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _metadata_for(path: Path, config: AdvisorConfig, deadline: float) -> dict[str, Any]:
    st = path.stat()
    item: dict[str, Any] = {
        "path": str(path),
        "type": "directory" if path.is_dir() else "file",
        "mode": oct(st.st_mode & 0o777),
        "size_bytes": int(st.st_size),
        "mtime_unix": int(st.st_mtime),
    }
    if path.is_dir():
        entry_count = 0
        shallow_bytes = 0
        extensions: dict[str, int] = {}
        for child in path.iterdir():
            if time.monotonic() > deadline:
                raise TimeoutError("read-only inspection timed out")
            entry_count += 1
            if entry_count > config.max_inspect_entries:
                item["truncated"] = True
                break
            try:
                cst = child.stat()
            except OSError:
                continue
            shallow_bytes += int(cst.st_size)
            if child.is_file():
                ext = child.suffix.lower() or "[no-extension]"
                extensions[ext] = extensions.get(ext, 0) + 1
        item.update(entry_count=entry_count, shallow_bytes=shallow_bytes, extensions=extensions)
    return item


__all__ = [
    "AdvisorConfig",
    "AdvisorValidationError",
    "DEFAULT_MODEL",
    "advisor_analysis_prompt",
    "advisor_translation_prompt",
    "build_advisor_response",
    "build_evidence_pack",
    "classify_path",
    "collect_readonly_metadata",
    "filter_excluded",
    "is_safe_target_path",
    "load_snapshot",
    "localize_payload_ko",
    "rule_recommendations",
    "snapshot_fingerprint",
    "validate_recommendations",
]
