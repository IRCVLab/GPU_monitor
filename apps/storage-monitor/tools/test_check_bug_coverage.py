#!/usr/bin/env python3
"""Fixture tests for check_bug_coverage.py.

The fixtures use made-up X, Y and Z ids so that this file, itself a test file
under apps/storage-monitor, never counts as coverage for a real catalog id.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "check_bug_coverage.py"

CATALOG = textwrap.dedent("""\
    # Fixture catalog

    Shipped earlier: #11 and #12 are not ids.

    | ID | Bug | Cause | Where |
    |---|---|---|---|
    | X1 | First x bug | S1 | a.py |
    | X2 | Second x bug | S2 | b.py |
    | Y1 | First y bug | S3 | c.js |
    | Y2 | Second y bug | S3 | d.js |
    """)


class CoverageFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.write("docs/redesign/bug-catalog.md", CATALOG)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(TOOL), "--root", str(self.root), *args],
                              capture_output=True, text=True, check=False)

    def cited(self, relative: str, text: str) -> set[str]:
        """Write one test file and return the catalog ids the tool counts as cited."""
        self.write(relative, text)
        result = self.run_tool()
        return {line.split()[1] for line in result.stdout.splitlines() if line.startswith("ok ")}


class CatalogIdsTest(CoverageFixture):
    def test_reads_ids_and_descriptions_from_table_rows_only(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING  X1   First x bug", result.stdout)
        self.assertIn("4 of 4 bug ids have no test citing them: X1, X2, Y1, Y2", result.stdout)


class CitationFormsTest(CoverageFixture):
    def test_pytest_xfail_reason_cites(self):
        self.assertEqual(self.cited("collector/test_a.py", """\
            @pytest.mark.xfail(
                strict=True,
                reason="X1 first x bug still reproduces",
            )
            def test_something():
                pass
            """), {"X1"})

    def test_python_test_name_cites(self):
        self.assertEqual(self.cited("collector/test_a.py", "def test_x2_keeps_link():\n    pass\n"), {"X2"})

    def test_shell_case_name_cites(self):
        self.assertEqual(self.cited("scanner/test_scan.sh", "run_case x1_rename_during_scan\n"), {"X1"})

    def test_node_expected_failure_tags_cite(self):
        self.assertEqual(self.cited("viewer/viewer_regression_test.js",
                                    "await expectedFailure(['Y1', 'Y2'], 'msg', testSomething);\n"), {"Y1", "Y2"})

    def test_node_test_title_cites(self):
        self.assertEqual(self.cited("viewer/test/state.test.js", "test('y1 refresh keeps drill', () => {});\n"), {"Y1"})

    def test_camel_case_test_name_cites(self):
        self.assertEqual(self.cited("viewer/a_test.js", "function testY2SortsByShownValue() {}\n"), {"Y2"})


class NonCitationsTest(CoverageFixture):
    def test_prose_and_embedded_names_do_not_cite(self):
        self.assertEqual(self.cited("viewer/viewer_regression_test.js", """\
            assert.equal(plan, null, 'paths with X1 characters get no command');
            // X1 is mentioned in a comment
            assert_schema_x1_contract(self, payload)
            def test_job_x2_contract(): pass
            """), set())

    def test_longer_id_does_not_cite_its_prefix(self):
        self.assertEqual(self.cited("collector/test_a.py", 'reason="X10 other bug"\ndef test_x10_other(): pass\n'), set())

    def test_files_that_are_not_tests_do_not_cite(self):
        self.write("collector/helper.py", 'reason = "X1 helper"\n')
        self.write("docs/notes.md", "test_x1_documented_only\n")
        self.assertEqual(self.cited("node_modules/pkg/test_vendor.py", "def test_x2_vendored(): pass\n"), set())


class ExitAndFilterTest(CoverageFixture):
    def test_only_filter_checks_one_prefix(self):
        self.write("viewer/a_test.js", "await expectedFailure(['Y1'], 'm', t);\nawait expectedFailure(['Y2'], 'm', t);\n")
        whole = self.run_tool()
        only_y = self.run_tool("--only", "Y")
        only_x = self.run_tool("--only", "X")
        self.assertEqual(whole.returncode, 1)
        self.assertIn("2 of 4 bug ids have no test citing them: X1, X2", whole.stdout)
        self.assertEqual(only_y.returncode, 0, only_y.stdout)
        self.assertIn("all 2 bug ids have a test citing them", only_y.stdout)
        self.assertNotIn("X1", only_y.stdout)
        self.assertEqual(only_x.returncode, 1)
        self.assertNotIn("Y1", only_x.stdout)

    def test_every_id_cited_exits_zero_and_names_locations(self):
        self.write("collector/test_a.py", "def test_x1_a(): pass\ndef test_x2_b(): pass\n")
        self.write("viewer/b.test.js", "test('y1 a', () => {});\ntest('y2 b', () => {});\n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ok       X1   collector/test_a.py:1", result.stdout)
        self.assertIn("all 4 bug ids have a test citing them", result.stdout)

    def test_unknown_prefix_is_a_usage_error(self):
        result = self.run_tool("--only", "Q")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no bug ids to check", result.stderr)


if __name__ == "__main__":
    unittest.main()
