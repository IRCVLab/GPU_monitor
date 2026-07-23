#!/usr/bin/env python3.12
"""Validate GitHub Actions workflow trust policy without external YAML deps."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

PINNED_ACTION_RE = re.compile(r"@[0-9a-f]{40}$")
PRODUCTION_LABELS = {"prod", "production", "prd", "prod-runner"}
DEPLOYMENT_JOB_IDS = {"deploy", "release", "activate", "rollback"}
DEPLOYMENT_TOKENS = ("deploy", "release", "activate", "rollback")
NON_DEPLOYMENT_NAMES = {"release-notes", "activate-venv"}
ALLOWED_DEPLOYMENT_RUNNERS = {"ubuntu-24.04"}

YAML_TOKEN_BOUNDARIES = set(" \t[{,:")


def without_quoted_strings_and_comments(value: str) -> str:
    chars: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'" and not in_double:
            chars.append(" ")
            if in_single and index + 1 < len(value) and value[index + 1] == "'":
                chars.append(" ")
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            chars.append(" ")
            if in_double and index > 0 and value[index - 1] == "\\":
                pass
            else:
                in_double = not in_double
        elif char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            break
        elif in_single or in_double:
            chars.append(" ")
        else:
            chars.append(char)
        index += 1
    return "".join(chars).rstrip()


def without_github_expressions(value: str) -> str:
    """Blank GitHub expression bodies so policy token scanning allows normal ${{ }} syntax."""
    chars = list(value)
    index = 0
    while index < len(chars):
        if value.startswith("${{", index):
            end = value.find("}}", index + 3)
            if end == -1:
                break
            for expr_index in range(index, end + 2):
                chars[expr_index] = " "
            index = end + 2
            continue
        index += 1
    return "".join(chars)


def has_unquoted_yaml_anchor_or_alias(value: str) -> bool:
    text = without_github_expressions(without_quoted_strings_and_comments(value))
    for index, char in enumerate(text):
        if char == "&" and index + 1 < len(text) and text[index + 1] == "&":
            continue
        if char not in {"&", "*"}:
            continue
        if index > 0 and text[index - 1] not in YAML_TOKEN_BOUNDARIES:
            continue
        if index + 1 >= len(text) or text[index + 1].isspace():
            continue
        return True
    return False


def has_unquoted_yaml_tag(value: str) -> bool:
    text = without_github_expressions(without_quoted_strings_and_comments(value))
    for index, char in enumerate(text):
        if char != "!":
            continue
        if index > 0 and text[index - 1] not in YAML_TOKEN_BOUNDARIES:
            continue
        if index + 1 >= len(text) or text[index + 1].isspace():
            continue
        return True
    return False


@dataclass(frozen=True)
class SourceLine:
    number: int
    indent: int
    text: str


@dataclass(frozen=True)
class JobBlock:
    job_id: str
    lines: list[SourceLine]


@dataclass(frozen=True)
class Violation:
    path: Path
    rule: str
    message: str
    job: str | None = None

    def format(self) -> str:
        job = f" job {self.job}:" if self.job else ""
        return f"{self.path}:{job} {self.rule}: {self.message}"


def strip_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.rstrip()


def unquote(value: str) -> str:
    value = strip_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_lines(path: Path) -> list[SourceLine]:
    parsed: list[SourceLine] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed.append(SourceLine(number, len(raw) - len(raw.lstrip(" ")), stripped))
    return parsed


def key_value(text: str) -> tuple[str, str] | None:
    if text.startswith("-"):
        text = text[1:].strip()
    if ":" not in text:
        return None
    key, value = text.split(":", 1)
    key = unquote(key)
    if not key:
        return None
    return key, value.strip()


def parse_inline_mapping_keys(value: str) -> list[str]:
    value = unquote(value)
    if not (value.startswith("{") and value.endswith("}")):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    keys: list[str] = []
    for part in inner.split(","):
        if ":" in part:
            key, _value = part.split(":", 1)
            keys.append(unquote(key))
    return keys


def parse_scalar_list(value: str) -> list[str]:
    value = unquote(value)
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [unquote(part) for part in inner.split(",")]
    if value.startswith("{") and value.endswith("}"):
        return parse_inline_mapping_keys(value)
    return [value]


def child_list(lines: list[SourceLine], start_index: int, parent_indent: int) -> list[str]:
    values: list[str] = []
    for line in lines[start_index + 1 :]:
        if line.indent <= parent_indent:
            break
        if line.text.startswith("-"):
            values.append(unquote(line.text[1:].strip()))
    return values


def yaml_anchor_or_alias(value: str) -> bool:
    return has_unquoted_yaml_anchor_or_alias(value)


def yaml_tag(value: str) -> bool:
    return has_unquoted_yaml_tag(value)


def job_for_line(jobs: list[JobBlock], line_number: int) -> str | None:
    for job in jobs:
        if any(line.number == line_number for line in job.lines):
            return job.job_id
    return None


def is_run_scalar_line(line: SourceLine) -> bool:
    parsed = key_value(line.text)
    return parsed is not None and parsed[0] == "run"


def is_block_scalar_start(line: SourceLine) -> bool:
    parsed = key_value(line.text)
    if parsed is None:
        return False
    return parsed[0] == "run" and strip_comment(parsed[1]).strip() in {"|", ">", "|-", ">-", "|+", ">+"}


def unsupported_yaml_violations(path: Path, lines: list[SourceLine]) -> list[Violation]:
    if not lines:
        return [Violation(path, "unsupported-yaml", "workflow file is empty")]
    violations: list[Violation] = []
    jobs = find_jobs(lines)
    run_block_indent: int | None = None
    for line in lines:
        if run_block_indent is not None:
            if line.indent > run_block_indent:
                continue
            run_block_indent = None

        if line.indent == 0 and line.text.startswith("-"):
            violations.append(Violation(path, "unsupported-yaml", f"top-level list item at line {line.number} is not supported"))
        if not line.text.startswith("-") and ":" not in line.text:
            violations.append(Violation(path, "unsupported-yaml", f"line {line.number} is not a supported key/value mapping"))

        if is_block_scalar_start(line):
            run_block_indent = line.indent
            continue
        if is_run_scalar_line(line):
            continue

        job = job_for_line(jobs, line.number)
        if yaml_anchor_or_alias(line.text):
            violations.append(
                Violation(
                    path,
                    "unsupported-yaml-anchor-alias",
                    f"YAML anchors and aliases are not supported at line {line.number}",
                    job,
                )
            )
        if yaml_tag(line.text):
            violations.append(
                Violation(
                    path,
                    "unsupported-yaml-tag",
                    f"YAML tags are not supported at line {line.number}",
                    job,
                )
            )
    if not jobs:
        violations.append(Violation(path, "unsupported-yaml", "workflow must contain at least one jobs entry"))
    return violations


def collect_events(lines: list[SourceLine]) -> set[str]:
    events: set[str] = set()
    for index, line in enumerate(lines):
        parsed = key_value(line.text)
        if not parsed:
            continue
        key, value = parsed
        if key != "on":
            continue
        events.update(parse_scalar_list(value))
        if value:
            continue
        for child in lines[index + 1 :]:
            if child.indent <= line.indent:
                break
            if child.text.startswith("-"):
                events.add(unquote(child.text[1:].strip()))
                continue
            child_parsed = key_value(child.text)
            if child_parsed:
                events.add(child_parsed[0])
        break
    return events


def find_jobs(lines: list[SourceLine]) -> list[JobBlock]:
    jobs_index = None
    jobs_indent = -1
    for index, line in enumerate(lines):
        parsed = key_value(line.text)
        if parsed and parsed[0] == "jobs" and not parsed[1]:
            jobs_index = index
            jobs_indent = line.indent
            break
    if jobs_index is None:
        return []

    jobs: list[JobBlock] = []
    job_id: str | None = None
    job_indent: int | None = None
    job_lines: list[SourceLine] = []

    for line in lines[jobs_index + 1 :]:
        if line.indent <= jobs_indent:
            break
        parsed = key_value(line.text)
        is_job_header = (
            parsed is not None
            and not parsed[1]
            and line.indent > jobs_indent
            and not line.text.startswith("-")
            and (job_indent is None or line.indent <= job_indent)
        )
        if is_job_header:
            if job_id is not None:
                jobs.append(JobBlock(job_id, job_lines))
            job_id = parsed[0]
            job_indent = line.indent
            job_lines = [line]
        elif job_id is not None:
            job_lines.append(line)

    if job_id is not None:
        jobs.append(JobBlock(job_id, job_lines))
    return jobs


def direct_child_indent(block_lines: list[SourceLine], parent_indent: int) -> int | None:
    direct_indent: int | None = None
    for line in block_lines[1:]:
        if line.indent <= parent_indent or line.text.startswith("-"):
            continue
        parsed = key_value(line.text)
        if parsed is None:
            continue
        if direct_indent is None or line.indent < direct_indent:
            direct_indent = line.indent
    return direct_indent


def direct_block_line(block_lines: list[SourceLine], parent_indent: int, key: str) -> tuple[int, SourceLine, str] | None:
    child_indent = direct_child_indent(block_lines, parent_indent)
    if child_indent is None:
        return None
    for index, line in enumerate(block_lines[1:], start=1):
        parsed = key_value(line.text)
        if parsed and line.indent == child_indent and parsed[0] == key:
            return index, line, unquote(parsed[1])
    return None


def direct_job_value(job: JobBlock, key: str) -> str | None:
    if not job.lines:
        return None
    found = direct_block_line(job.lines, job.lines[0].indent, key)
    if found is None:
        return None
    _index, _line, value = found
    return value


def direct_job_values(job: JobBlock, key: str) -> list[str]:
    if not job.lines:
        return []
    found = direct_block_line(job.lines, job.lines[0].indent, key)
    if found is None:
        return []
    index, line, value = found
    values = parse_scalar_list(value)
    if values:
        return values
    return child_list(job.lines, index, line.indent)


def direct_job_line(job: JobBlock, key: str) -> tuple[SourceLine, str] | None:
    if not job.lines:
        return None
    found = direct_block_line(job.lines, job.lines[0].indent, key)
    if found is None:
        return None
    _index, line, value = found
    return line, value



def has_runs_on_mapping(job: JobBlock) -> bool:
    runs_on = direct_job_line(job, "runs-on")
    if runs_on is None:
        return False
    line, value = runs_on
    if value.startswith("{") and value.endswith("}"):
        return True
    if value:
        return False
    for child in job.lines:
        if child.number <= line.number:
            continue
        if child.indent <= line.indent:
            break
        if not child.text.startswith("-") and key_value(child.text):
            return True
    return False


def uses_values(job: JobBlock) -> list[str]:
    values: list[str] = []
    for line in job.lines[1:]:
        parsed = key_value(line.text)
        if parsed and parsed[0] == "uses":
            values.append(unquote(parsed[1]))
    return values


def permission_write_violations(
    path: Path,
    lines: list[SourceLine],
    *,
    owner_indent: int,
    job: str | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    for index, line in enumerate(lines):
        parsed = key_value(line.text)
        if not parsed or line.indent != owner_indent or parsed[0] != "permissions":
            continue
        value = unquote(parsed[1])
        if value == "write-all":
            violations.append(Violation(path, "permissions-write-all", "permissions: write-all is not allowed", job))
        elif value.startswith("{") and value.endswith("}"):
            for part in value[1:-1].split(","):
                if ":" not in part:
                    continue
                scope, permission = part.split(":", 1)
                if unquote(permission) == "write":
                    violations.append(
                        Violation(path, "permissions-write-scope", f"permissions scope {unquote(scope)!r} grants write", job)
                    )
        elif not value:
            for child in lines[index + 1 :]:
                if child.indent <= line.indent:
                    break
                child_parsed = key_value(child.text)
                if child_parsed and unquote(child_parsed[1]) == "write":
                    violations.append(
                        Violation(path, "permissions-write-scope", f"permissions scope {child_parsed[0]!r} grants write", job)
                    )
    return violations


def normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def identifier_segments(value: str) -> list[str]:
    normalized = normalized_identifier(value)
    if not normalized:
        return []
    return normalized.split("-")


def segment_has_deployment_stem(segment: str) -> bool:
    return (
        segment.startswith("deploy")
        or segment.startswith("releas")
        or segment.startswith("activat")
        or segment.startswith("rollback")
    )


def has_deployment_segment(value: str) -> bool:
    return any(segment_has_deployment_stem(segment) for segment in identifier_segments(value))


def is_deploy_job(job: JobBlock) -> bool:
    name = direct_job_value(job, "name") or ""
    normalized_job_id = normalized_identifier(job.job_id)
    normalized_name = normalized_identifier(name)
    strong_signal = normalized_job_id in DEPLOYMENT_JOB_IDS or direct_job_value(job, "environment") is not None
    if strong_signal:
        return True
    job_id_is_benign = normalized_job_id in NON_DEPLOYMENT_NAMES
    name_is_benign = normalized_name in NON_DEPLOYMENT_NAMES
    return (not job_id_is_benign and has_deployment_segment(job.job_id)) or (
        not name_is_benign and has_deployment_segment(name)
    )


def normalized_condition(job: JobBlock) -> str:
    condition = direct_job_value(job, "if") or ""
    return normalize_expression(condition)


def normalize_expression(condition: str) -> str:
    normalized = unquote(condition).strip()
    if normalized.startswith("${{") and normalized.endswith("}}"):
        normalized = normalized[3:-2].strip()
    normalized = normalized.replace('"', "'")
    return re.sub(r"\s+", "", normalized)


def has_main_guard(job: JobBlock) -> bool:
    normalized = normalized_condition(job)
    return normalized in {
        "github.ref=='refs/heads/main'",
        "github.ref_name=='main'",
    }


def contains_literal(values: list[str], expected: str) -> bool:
    return any(unquote(value).lower() == expected for value in values)


def top_level_event_block(lines: list[SourceLine], event: str) -> tuple[int, SourceLine] | None:
    for index, line in enumerate(lines):
        parsed = key_value(line.text)
        if not parsed or parsed[0] != "on":
            continue
        if parsed[1]:
            return None
        for event_index, event_line in enumerate(lines[index + 1 :], start=index + 1):
            if event_line.indent <= line.indent:
                break
            event_parsed = key_value(event_line.text)
            if event_parsed and event_parsed[0] == event:
                return event_index, event_line
        break
    return None


def workflow_run_values(lines: list[SourceLine], key: str) -> list[str]:
    event = top_level_event_block(lines, "workflow_run")
    if event is None:
        return []
    event_index, event_line = event
    if key_value(event_line.text) and key_value(event_line.text)[1]:
        return []
    block_lines = [event_line]
    for child in lines[event_index + 1 :]:
        if child.indent <= event_line.indent:
            break
        block_lines.append(child)
    found = direct_block_line(block_lines, event_line.indent, key)
    if found is None:
        return []
    index, line, value = found
    values = parse_scalar_list(value)
    if values:
        return values
    return child_list(block_lines, index, line.indent)


def has_completed_ci_workflow_run_trigger(lines: list[SourceLine]) -> bool:
    return contains_literal(workflow_run_values(lines, "workflows"), "ci") and contains_literal(
        workflow_run_values(lines, "types"), "completed"
    )


def top_level_block(lines: list[SourceLine], key: str) -> list[SourceLine]:
    for index, line in enumerate(lines):
        parsed = key_value(line.text)
        if parsed and line.indent == 0 and parsed[0] == key and not parsed[1]:
            block = [line]
            for child in lines[index + 1 :]:
                if child.indent <= line.indent:
                    break
                block.append(child)
            return block
    return []




def workflow_dispatch_input_names(lines: list[SourceLine]) -> set[str]:
    event = top_level_event_block(lines, "workflow_dispatch")
    if event is None:
        return set()
    event_index, event_line = event
    in_inputs = False
    inputs_indent: int | None = None
    names: set[str] = set()
    for line in lines[event_index + 1 :]:
        if line.indent <= event_line.indent:
            break
        parsed = key_value(line.text)
        if parsed and parsed[0] == "inputs":
            in_inputs = True
            inputs_indent = line.indent
            continue
        if in_inputs and inputs_indent is not None:
            if line.indent <= inputs_indent:
                break
            if parsed and line.indent == inputs_indent + 2:
                names.add(parsed[0])
    return names


def has_workflow_dispatch_pr_number_input(lines: list[SourceLine]) -> bool:
    event = top_level_event_block(lines, "workflow_dispatch")
    if event is None:
        return False
    event_index, event_line = event
    for line in lines[event_index + 1 :]:
        if line.indent <= event_line.indent:
            break
        parsed = key_value(line.text)
        if parsed and parsed[0] == "pr_number":
            return True
    return False


def has_top_level_concurrency(lines: list[SourceLine], *, group: str, cancel_in_progress: str) -> bool:
    block = top_level_block(lines, "concurrency")
    if not block:
        return False
    found_group = False
    found_cancel = False
    for line in block[1:]:
        parsed = key_value(line.text)
        if not parsed:
            continue
        key, value = parsed
        if key == "group" and unquote(value) == group:
            found_group = True
        if key == "cancel-in-progress" and unquote(value).lower() == cancel_in_progress:
            found_cancel = True
    return found_group and found_cancel


def job_text(job: JobBlock) -> str:
    return "\n".join(line.text for line in job.lines)


def workflow_text_from_lines(lines: list[SourceLine]) -> str:
    return "\n".join(line.text for line in lines)


def workflow_mentions_storage(lines: list[SourceLine]) -> bool:
    return "storage" in workflow_text_from_lines(lines).lower()



def executable_lines(command: str | None) -> list[str]:
    if command is None:
        return []
    return [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]


def line_contains(lines: list[str], *needles: str) -> bool:
    return any(all(needle in line for needle in needles) for line in lines)


def step_nested_value(block: list[SourceLine], parent_key: str, child_key: str) -> str | None:
    found = step_property(block, parent_key)
    if found is None:
        return None
    index, line, value = found
    if value:
        return None
    for child in block[index + 1 :]:
        if child.indent <= line.indent:
            break
        parsed = key_value(child.text)
        if parsed and parsed[0] == child_key:
            return unquote(parsed[1])
    return None


def step_has_env(block: list[SourceLine], key: str, expected: str) -> bool:
    found = step_property(block, "env")
    if found is None:
        return False
    index, line, value = found
    if value:
        return False
    for child in block[index + 1 :]:
        if child.indent <= line.indent:
            break
        parsed = key_value(child.text)
        if parsed and parsed[0] == key and unquote(parsed[1]) == expected:
            return True
    return False


def has_checkout_ref(job: JobBlock, expected_ref: str) -> bool:
    for block in step_blocks(job):
        uses = step_property_value(block, "uses")
        if uses is None or not PINNED_ACTION_RE.search(uses) or not uses.startswith("actions/checkout@"):
            continue
        if step_nested_value(block, "with", "ref") == expected_ref:
            return True
    return False


def has_dev_resolve_step(job: JobBlock) -> bool:
    for block in step_blocks(job):
        if step_property_value(block, "id") != "resolve":
            continue
        lines = executable_lines(run_command_value(block))
        return all(
            (
                line_contains(lines, "pr_number=", "PR_NUMBER"),
                line_contains(lines, '[[ "$pr_number"', "^[1-9][0-9]*$"),
                line_contains(lines, "pr_json=", "gh api", "pulls/$pr_number"),
                line_contains(lines, "state=", "json.load", "state"),
                line_contains(lines, '[[ "$state" == open ]]'),
                line_contains(lines, "base_repo=", "base", "repo", "full_name"),
                line_contains(lines, '[[ "$base_repo" == "$GITHUB_REPOSITORY" ]]'),
                line_contains(lines, "head_repo=", "head", "repo", "full_name"),
                line_contains(lines, '[[ "$head_repo" == "$GITHUB_REPOSITORY" ]]'),
                line_contains(lines, "sha=", "head", "sha"),
                line_contains(lines, '[[ "$sha"', "^[0-9a-f]{40}$"),
                line_contains(lines, "checks_json=", "gh api", "--paginate", "--slurp", "check-runs"),
                line_contains(lines, "completed_at"),
                line_contains(lines, "check_id"),
                line_contains(lines, "latest", "max"),
                line_contains(lines, "status", "completed"),
                line_contains(lines, "conclusion", "success"),
                line_contains(lines, "ci/required"),
            )
        )
    return False


def has_build_step(job: JobBlock, expected_sha_source: str) -> bool:
    for block in step_blocks(job):
        if step_property_value(block, "id") != "build":
            continue
        if not step_has_env(block, "SHA", expected_sha_source):
            continue
        lines = executable_lines(run_command_value(block))
        if line_contains(lines, 'sha="$SHA"') and line_contains(lines, '[[ "$sha"', "^[0-9a-f]{40}$"):
            return True
    return False


def has_forced_deploy_step(job: JobBlock, lane: str, expected_sha_source: str) -> bool:
    upload = f'"upload {lane} $sha $digest"'
    activate = f'"activate {lane} $sha $digest"'
    status = f'"status {lane}"'
    for block in step_blocks(job):
        if not all(
            step_has_env(block, name, value)
            for name, value in (
                ("SHA", expected_sha_source),
                ("DIGEST", "${{ steps.build.outputs.digest }}"),
                ("ARTIFACT", "${{ steps.build.outputs.artifact }}"),
                ("GPU_DEPLOY_HOST", "${{ secrets.GPU_DEPLOY_HOST }}"),
                ("GPU_DEPLOY_PORT", "${{ secrets.GPU_DEPLOY_PORT }}"),
                ("GPU_DEPLOY_USER", "${{ secrets.GPU_DEPLOY_USER }}"),
            )
        ):
            continue
        lines = executable_lines(run_command_value(block))
        ssh_lines = [line for line in lines if line.startswith('ssh ')]
        expected_ssh_lines = [
            f'ssh "${{ssh_opts[@]}}" "$target" {upload} < "$artifact"',
            f'ssh "${{ssh_opts[@]}}" "$target" {activate}',
            f'ssh "${{ssh_opts[@]}}" "$target" {status}',
        ]
        if not all(
            (
                line_contains(lines, 'sha="$SHA"'),
                line_contains(lines, 'digest="$DIGEST"'),
                line_contains(lines, 'artifact="$ARTIFACT"'),
                line_contains(lines, '[[ "$sha"', "^[0-9a-f]{40}$"),
                line_contains(lines, '[[ "$digest"', "^[0-9a-f]{64}$"),
                line_contains(lines, '[[ "$GPU_DEPLOY_HOST"', "^[A-Za-z0-9._-]+$"),
                line_contains(lines, '[[ "$GPU_DEPLOY_USER"', "^[A-Za-z0-9._-]+$"),
                line_contains(lines, '[[ "$GPU_DEPLOY_PORT"', "^[0-9]+$"),
                line_contains(lines, 'target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"'),
                line_contains(lines, '[[ "$target"', "^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$"),
                line_contains(lines, "ssh_opts=", "StrictHostKeyChecking=yes", "UserKnownHostsFile", "IdentitiesOnly=yes", '-p "$GPU_DEPLOY_PORT"'),
                ssh_lines == expected_ssh_lines,
            )
        ):
            continue
        return True
    return False

def has_gpu_dev_dispatch_guard(lines: list[SourceLine], job: JobBlock, events: set[str], runner_labels: set[str]) -> bool:
    return (
        "workflow_dispatch" in events
        and not ({"push", "pull_request", "pull_request_target", "workflow_run"} & events)
        and direct_job_value(job, "environment") == "gpu-dev"
        and runner_labels == {"ubuntu-24.04"}
        and workflow_dispatch_input_names(lines) == {"pr_number"}
        and has_workflow_dispatch_pr_number_input(lines)
        and has_top_level_concurrency(lines, group="gpu-dev", cancel_in_progress="true")
        and has_dev_resolve_step(job)
        and has_checkout_ref(job, "${{ steps.resolve.outputs.sha }}")
        and has_build_step(job, "${{ steps.resolve.outputs.sha }}")
        and has_forced_deploy_step(job, "dev", "${{ steps.resolve.outputs.sha }}")
        and not workflow_mentions_storage(lines)
    )


def has_workflow_run_head_sha_authorization(job: JobBlock) -> bool:
    for block in step_blocks(job):
        command = run_command_value(block)
        if command is None or not run_executes_authorize_gpu_release(command):
            continue
        if "github.event.workflow_run.head_sha" not in command and "github.event_path" not in command:
            continue
        return True
    return False


def has_split_live_authorization(lines: list[SourceLine], jobs: list[JobBlock], deploy_job: JobBlock) -> bool:
    needed = set(direct_job_values(deploy_job, "needs"))
    if needed != {"authorize"}:
        return False
    auth_jobs = [job for job in jobs if job.job_id == "authorize"]
    if len(auth_jobs) != 1:
        return False
    for auth_job in auth_jobs:
        runner_labels = {label.lower() for label in direct_job_values(auth_job, "runs-on")}
        if runner_labels != {"ubuntu-24.04"}:
            continue
        if direct_job_value(auth_job, "environment") is not None:
            continue
        if job_references_secrets(auth_job):
            continue
        if not has_workflow_run_provenance_guard(auth_job):
            continue
        if has_authorization_step(auth_job) and has_workflow_run_head_sha_authorization(auth_job):
            return True
    return False


def live_deploy_job_uses_forced_protocol(job: JobBlock) -> bool:
    return (
        has_checkout_ref(job, "${{ github.event.workflow_run.head_sha }}")
        and has_build_step(job, "${{ github.event.workflow_run.head_sha }}")
        and has_forced_deploy_step(job, "live", "${{ github.event.workflow_run.head_sha }}")
    )


def split_top_level_conjunction(expression: str) -> list[str] | None:
    if "||" in expression:
        return None
    clauses: list[str] = []
    start = 0
    in_single = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            in_single = not in_single
            index += 1
            continue
        if not in_single and expression.startswith("&&", index):
            clauses.append(expression[start:index])
            index += 2
            start = index
            continue
        index += 1
    clauses.append(expression[start:])
    if in_single or any(not clause for clause in clauses):
        return None
    return clauses


def has_workflow_run_provenance_guard(job: JobBlock) -> bool:
    clauses = split_top_level_conjunction(normalized_condition(job))
    if clauses is None:
        return False
    required = {
        "github.event.workflow_run.event=='push'",
        "github.event.workflow_run.head_branch=='main'",
        "github.event.workflow_run.conclusion=='success'",
        "github.event.workflow_run.head_repository.full_name==github.repository",
    }
    return set(clauses) == required


def step_blocks(job: JobBlock) -> list[list[SourceLine]]:
    steps_line = direct_job_line(job, "steps")
    if steps_line is None:
        return []
    parent_line, _value = steps_line
    children: list[SourceLine] = []
    for line in job.lines:
        if line.number <= parent_line.number:
            continue
        if line.indent <= parent_line.indent:
            break
        children.append(line)
    blocks: list[list[SourceLine]] = []
    current: list[SourceLine] = []
    current_indent: int | None = None
    for line in children:
        if line.text.startswith("-") and current_indent is None:
            current = [line]
            current_indent = line.indent
            continue
        if line.text.startswith("-") and current_indent is not None and line.indent <= current_indent:
            blocks.append(current)
            current = [line]
            current_indent = line.indent
            continue
        if current_indent is not None:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def shell_words(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return []


def step_direct_indent(block: list[SourceLine]) -> int | None:
    direct_indent: int | None = None
    if not block:
        return None
    item_indent = block[0].indent
    for line in block[1:]:
        parsed = key_value(line.text)
        if not parsed or line.indent <= item_indent:
            continue
        if direct_indent is None or line.indent < direct_indent:
            direct_indent = line.indent
    return direct_indent


def step_property(block: list[SourceLine], key: str) -> tuple[int, SourceLine, str] | None:
    direct_indent = step_direct_indent(block)
    for index, line in enumerate(block):
        parsed = key_value(line.text)
        if not parsed or parsed[0] != key:
            continue
        if line is block[0] and line.text.startswith("-"):
            return index, line, unquote(parsed[1])
        if direct_indent is not None and line.indent == direct_indent:
            return index, line, unquote(parsed[1])
    return None


def step_property_value(block: list[SourceLine], key: str) -> str | None:
    found = step_property(block, key)
    if found is None:
        return None
    _index, _line, value = found
    return value


def run_command_value(block: list[SourceLine]) -> str | None:
    found = step_property(block, "run")
    if found is None:
        return None
    index, line, value = found
    if strip_comment(value).strip() in {"|", ">", "|-", ">-", "|+", ">+"}:
        commands: list[str] = []
        for child in block[index + 1 :]:
            if child.indent <= line.indent:
                break
            commands.append(strip_comment(child.text).strip())
        return "\n".join(command for command in commands if command)
    return value


def command_has_shell_control(value: str) -> bool:
    in_single = False
    in_double = False
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not in_single:
            escaped = True
            index += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if in_single or in_double:
            index += 1
            continue
        if value.startswith("$(", index):
            return True
        if char in {"&", ";", "|", ">", "<", "`"}:
            return True
        index += 1
    return False


def run_executes_authorize_gpu_release(value: str) -> bool:
    commands = [line.strip() for line in value.splitlines() if line.strip()]
    if len(commands) != 1:
        return False
    command = commands[0]
    if command_has_shell_control(command):
        return False
    words = shell_words(command)
    return len(words) >= 2 and words[0] == "python3.12" and words[1] == "scripts/authorize_gpu_release.py"


def continue_on_error_allows_authorization(value: str | None) -> bool:
    if value is None or value == "":
        return True
    normalized = re.sub(r"\s+", "", unquote(value).lower())
    return normalized in {"false", "${{false}}"}


def has_authorization_step(job: JobBlock) -> bool:
    for block in step_blocks(job):
        command = run_command_value(block)
        if command is None or not run_executes_authorize_gpu_release(command):
            continue
        if step_property_value(block, "if") is not None:
            continue
        if not continue_on_error_allows_authorization(step_property_value(block, "continue-on-error")):
            continue
        working_directory = step_property_value(block, "working-directory")
        if working_directory not in {None, "", "."}:
            continue
        return True
    return False


SECRETS_CONTEXT_RE = re.compile(r"(?<![A-Za-z0-9_])secrets(?![A-Za-z0-9_])")


def github_expression_bodies(value: str) -> list[str]:
    bodies: list[str] = []
    index = 0
    while index < len(value):
        start = value.find("${{", index)
        if start == -1:
            break
        cursor = start + 3
        in_single = False
        while cursor < len(value):
            char = value[cursor]
            if char == "'":
                if in_single and cursor + 1 < len(value) and value[cursor + 1] == "'":
                    cursor += 2
                    continue
                in_single = not in_single
                cursor += 1
                continue
            if not in_single and value.startswith("}}", cursor):
                bodies.append(value[start + 3 : cursor])
                cursor += 2
                break
            cursor += 1
        else:
            break
        index = cursor
    return bodies


def strip_github_string_literals(value: str) -> str:
    chars: list[str] = []
    in_single = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            chars.append(" ")
            if in_single and index + 1 < len(value) and value[index + 1] == "'":
                chars.append(" ")
                index += 2
                continue
            in_single = not in_single
        elif in_single:
            chars.append(" ")
        else:
            chars.append(char)
        index += 1
    return "".join(chars)


def expression_references_secrets(value: str) -> bool:
    for expression in github_expression_bodies(value):
        if SECRETS_CONTEXT_RE.search(strip_github_string_literals(expression)):
            return True
    return False


def job_references_secrets(job: JobBlock) -> bool:
    for line in job.lines[1:]:
        parsed = key_value(line.text)
        if parsed and parsed[0] == "secrets":
            return True
        if expression_references_secrets(strip_comment(line.text)):
            return True
    return False


def top_level_references_secrets(lines: list[SourceLine]) -> bool:
    in_jobs = False
    for line in lines:
        parsed = key_value(line.text)
        if line.indent == 0 and parsed:
            in_jobs = parsed[0] == "jobs"
            if parsed[0] == "secrets":
                return True
        if in_jobs and line.indent > 0:
            continue
        if expression_references_secrets(strip_comment(line.text)):
            return True
    return False


def is_github_hosted_runner_label(label: str) -> bool:
    return label in ALLOWED_DEPLOYMENT_RUNNERS


def deployment_runner_violation(runner_labels: set[str]) -> bool:
    if not runner_labels:
        return True
    if any("${{" in label or "matrix." in label for label in runner_labels):
        return True
    return not all(is_github_hosted_runner_label(label) for label in runner_labels)


def validate_workflow(path: Path) -> list[Violation]:
    lines = parse_lines(path)
    violations: list[Violation] = []
    violations.extend(unsupported_yaml_violations(path, lines))
    events = collect_events(lines)

    if "pull_request_target" in events:
        violations.append(
            Violation(path, "pull-request-target", "pull_request_target workflows are not allowed")
        )
    violations.extend(permission_write_violations(path, lines, owner_indent=0))

    is_pull_request_workflow = "pull_request" in events
    workflow_has_pr_secrets = is_pull_request_workflow and top_level_references_secrets(lines)
    if workflow_has_pr_secrets:
        violations.append(Violation(path, "pr-secrets", "pull_request workflows must not reference GitHub secrets"))
    jobs = find_jobs(lines)
    for job in jobs:
        job_indent = job.lines[0].indent
        violations.extend(permission_write_violations(path, job.lines[1:], owner_indent=direct_child_indent(job.lines, job_indent) or job_indent + 2, job=job.job_id))

        for value in uses_values(job):
            if not PINNED_ACTION_RE.search(value):
                violations.append(
                    Violation(
                        path,
                        "pinned-action-sha",
                        f"uses value {value!r} must end in a 40-character lowercase hexadecimal SHA",
                        job.job_id,
                    )
                )

        runner_labels = {label.lower() for label in direct_job_values(job, "runs-on")}
        if has_runs_on_mapping(job):
            violations.append(
                Violation(path, "unsupported-runs-on-mapping", "runs-on mapping forms are not supported", job.job_id)
            )
        if is_pull_request_workflow:
            if any("${{" in label or "matrix." in label for label in runner_labels):
                violations.append(
                    Violation(path, "pr-runner-ambiguous", "pull_request jobs must not use dynamic or matrix-selected runners", job.job_id)
                )
            if "self-hosted" in runner_labels:
                violations.append(
                    Violation(path, "pr-self-hosted-runner", "pull_request jobs must use GitHub-hosted runners", job.job_id)
                )

        deploy_job = is_deploy_job(job)
        is_workflow_dispatch_dev_deploy = deploy_job and "workflow_dispatch" in events
        if deploy_job and workflow_mentions_storage(lines):
            violations.append(
                Violation(path, "gpu-deploy-storage-coupling", "GPU deployment workflows must not reference Storage paths or services", job.job_id)
            )
        if is_workflow_dispatch_dev_deploy and not has_gpu_dev_dispatch_guard(lines, job, events, runner_labels):
            violations.append(
                Violation(
                    path,
                    "workflow-dispatch-dev-deploy-guard",
                    "workflow_dispatch GPU development deployments must select an open same-repo PR, require ci/required on its exact head SHA, use gpu-dev concurrency/environment, and use the forced SSH protocol",
                    job.job_id,
                )
            )
        if is_pull_request_workflow and job_references_secrets(job) and not workflow_has_pr_secrets:
            violations.append(
                Violation(path, "pr-secrets", "pull_request jobs must not reference GitHub secrets", job.job_id)
            )
        if deploy_job and is_pull_request_workflow:
            violations.append(
                Violation(path, "deploy-pull-request-event", "deployment jobs must not run from pull_request workflows", job.job_id)
            )
        if deploy_job and "self-hosted" in runner_labels:
            violations.append(
                Violation(path, "deploy-self-hosted-runner", "deployment jobs must use GitHub-hosted runners", job.job_id)
            )
        if deploy_job and deployment_runner_violation(runner_labels):
            violations.append(
                Violation(path, "deploy-runner", "deployment jobs must use explicit GitHub-hosted runner labels", job.job_id)
            )
        if deploy_job and "workflow_run" in events:
            if not (
                has_completed_ci_workflow_run_trigger(lines)
                and has_top_level_concurrency(lines, group="gpu-live", cancel_in_progress="false")
                and has_workflow_run_provenance_guard(job)
                and direct_job_value(job, "environment") == "gpu-live"
                and has_split_live_authorization(lines, jobs, job)
                and live_deploy_job_uses_forced_protocol(job)
            ):
                violations.append(
                    Violation(
                        path,
                        "workflow-run-deploy-guard",
                        "workflow_run deployment jobs must require completed ci on main push from the same repo, success, separate non-secret authorization, gpu-live concurrency/environment, and forced SSH deployment",
                        job.job_id,
                    )
                )
        elif is_workflow_dispatch_dev_deploy:
            pass
        elif deploy_job and not has_main_guard(job):
            violations.append(
                Violation(path, "deploy-main-guard", "deploy jobs must have a main-branch if guard", job.job_id)
            )
        if not deploy_job and runner_labels & PRODUCTION_LABELS:
            labels = ", ".join(sorted(runner_labels & PRODUCTION_LABELS))
            violations.append(
                Violation(path, "production-label-non-deploy", f"non-deploy job uses production runner label(s): {labels}", job.job_id)
            )

    return violations


def workflow_files(workflow_dir: Path) -> list[Path]:
    return sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])


def validate_directory(workflow_dir: Path) -> list[Violation]:
    if not workflow_dir.exists():
        return []
    violations: list[Violation] = []
    for path in workflow_files(workflow_dir):
        violations.extend(validate_workflow(path))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_dir", nargs="?", default=".github/workflows")
    args = parser.parse_args(argv)

    workflow_dir = Path(args.workflow_dir)
    violations = validate_directory(workflow_dir)
    if violations:
        for violation in violations:
            print(violation.format(), file=sys.stderr)
        return 1

    if not workflow_dir.exists():
        print("OK: workflow policy validated 0 workflow file(s); directory missing")
    else:
        count = len(workflow_files(workflow_dir))
        suffix = "; directory empty" if count == 0 else ""
        print(f"OK: workflow policy validated {count} workflow file(s){suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
