#!/usr/bin/env python3.12
"""Validate GitHub Actions workflow trust policy without external YAML deps."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PINNED_ACTION_RE = re.compile(r"@[0-9a-f]{40}$")
PRODUCTION_LABELS = {"prod", "production", "prd", "prod-runner"}

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


def direct_job_value(job: JobBlock, key: str) -> str | None:
    if not job.lines:
        return None
    job_indent = job.lines[0].indent
    for line in job.lines[1:]:
        parsed = key_value(line.text)
        if parsed and line.indent == job_indent + 2 and parsed[0] == key:
            return unquote(parsed[1])
    return None


def direct_job_values(job: JobBlock, key: str) -> list[str]:
    if not job.lines:
        return []
    job_indent = job.lines[0].indent
    for index, line in enumerate(job.lines[1:], start=1):
        parsed = key_value(line.text)
        if parsed and line.indent == job_indent + 2 and parsed[0] == key:
            values = parse_scalar_list(parsed[1])
            if values:
                return values
            return child_list(job.lines, index, line.indent)
    return []


def direct_job_line(job: JobBlock, key: str) -> tuple[SourceLine, str] | None:
    if not job.lines:
        return None
    job_indent = job.lines[0].indent
    for line in job.lines[1:]:
        parsed = key_value(line.text)
        if parsed and line.indent == job_indent + 2 and parsed[0] == key:
            return line, unquote(parsed[1])
    return None


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


def is_deploy_job(job: JobBlock) -> bool:
    name = direct_job_value(job, "name") or ""
    haystack = f"{job.job_id} {name}".lower()
    return "deploy" in haystack


def has_main_guard(job: JobBlock) -> bool:
    condition = direct_job_value(job, "if") or ""
    normalized = condition.replace('"', "'").replace(" ", "")
    return normalized in {
        "github.ref=='refs/heads/main'",
        "github.ref_name=='main'",
    }


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
    for job in find_jobs(lines):
        job_indent = job.lines[0].indent
        violations.extend(permission_write_violations(path, job.lines[1:], owner_indent=job_indent + 2, job=job.job_id))

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
        if deploy_job and not has_main_guard(job):
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
