#!/usr/bin/env python3
"""Fail when a bug in docs/redesign/bug-catalog.md has no test that cites it.

Catalog ids are the first cells of the catalog's table rows, such as C1 or V3.
A test file is any file under the root whose name looks like a test:
test_*.py, *_test.py, test_*.sh, *_test.sh, *.test.js, *_test.js, *.spec.js
and the .mjs forms. A test file cites C1 when it contains one of:

- a string that starts with the id, as a tag or an xfail reason:
  reason="C1 late failure ignored", expectedFailure(['C1'], ...);
- a test or case name that starts with the id in lower case followed by
  "_" or a space, right after "test_" or at the start of the name:
  test_c1_late_failure_ignored, c1_rename_case, 'c1 keeps ...';
- a camelCase test name: testC1KeepsLink.

Prose such as "C1 characters" and names such as schema_v1_contract do not count.

Exits 1 and lists every uncited id, 0 when every id is cited.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "redesign" / "bug-catalog.md"
TEST_FILE_PATTERNS = (
    "test_*.py", "*_test.py", "test_*.sh", "*_test.sh",
    "*.test.js", "*_test.js", "*.spec.js", "*.test.mjs", "*.spec.mjs",
)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".storage-viz-state"}
CATALOG_ROW = re.compile(r"^\|\s*([A-Z]+[0-9]+)\s*\|\s*([^|]*?)\s*\|", re.MULTILINE)


def catalog_ids(text: str) -> dict[str, str]:
    """Map each catalog id to its one-line bug description, in catalog order."""
    return {match[1]: match[2] for match in CATALOG_ROW.finditer(text)}


def citation_pattern(bug_id: str) -> re.Pattern[str]:
    tag = rf"(?<=['\"`]){re.escape(bug_id)}(?![A-Za-z0-9])"
    name = rf"(?:(?<=test_)|(?<![A-Za-z0-9_])){re.escape(bug_id.lower())}(?=[_ ])"
    camel = rf"test{re.escape(bug_id)}(?![0-9])"
    return re.compile(f"{tag}|{name}|{camel}")


def test_files(root: Path):
    for path in sorted(root.rglob("*")):
        if SKIP_DIRS.intersection(path.relative_to(root).parts) or not path.is_file():
            continue
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in TEST_FILE_PATTERNS):
            yield path


def find_citations(root: Path, ids) -> dict[str, list[str]]:
    """Map each id to the "file:line" locations that cite it."""
    patterns = {bug_id: citation_pattern(bug_id) for bug_id in ids}
    found: dict[str, list[str]] = {bug_id: [] for bug_id in ids}
    for path in test_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for bug_id, pattern in patterns.items():
                if pattern.search(line):
                    found[bug_id].append(f"{path.relative_to(root)}:{lineno}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", metavar="PREFIX",
                        help="check only ids with this letter prefix, such as V; repeatable")
    parser.add_argument("--root", type=Path, default=ROOT, help="tree to search for tests")
    parser.add_argument("--catalog", type=Path, default=None, help="bug catalog (default: ROOT/docs/redesign/bug-catalog.md)")
    args = parser.parse_args(argv)
    catalog = args.catalog or args.root / "docs" / "redesign" / "bug-catalog.md"

    bugs = catalog_ids(catalog.read_text(encoding="utf-8"))
    if args.only:
        bugs = {bug_id: text for bug_id, text in bugs.items() if re.match(r"[A-Z]+", bug_id)[0] in args.only}
    if not bugs:
        print(f"no bug ids to check in {catalog}", file=sys.stderr)
        return 2

    citations = find_citations(args.root, bugs)
    missing = [bug_id for bug_id in bugs if not citations[bug_id]]
    for bug_id in bugs:
        if citations[bug_id]:
            print(f"ok       {bug_id:<4} {', '.join(citations[bug_id])}")
    for bug_id in missing:
        print(f"MISSING  {bug_id:<4} {bugs[bug_id]}")
    if missing:
        print(f"{len(missing)} of {len(bugs)} bug ids have no test citing them: {', '.join(missing)}")
        return 1
    print(f"all {len(bugs)} bug ids have a test citing them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
