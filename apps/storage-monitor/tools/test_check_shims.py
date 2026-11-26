import pytest

from check_shims import DESIGN, PROGRESS, check


@pytest.fixture
def tree(tmp_path):
    (tmp_path / DESIGN).parent.mkdir(parents=True)
    (tmp_path / DESIGN).write_text("### 6.2 Temporary shims\n| S2 | transport | M2b | M3b |\n")
    (tmp_path / PROGRESS).write_text('{"schema":"storage-viz.redesign-progress/1","done":["M2b"]}')
    return tmp_path


@pytest.mark.parametrize("suffix", ["py", "js", "sh", "css", "html"])
def test_live_and_expired(tree, suffix):
    path = tree / f"example.{suffix}"
    path.write_text("SHIM" + "(M2b..M3b) S2")
    assert check(tree) == []
    (tree / PROGRESS).write_text((tree / PROGRESS).read_text().replace("M2b", "M3b"))
    assert "example." + suffix + ":1: remove expired S2" in check(tree)[0]


@pytest.mark.parametrize("body", ["(M2b..M3b) S9", "(M2b-M3b) S2", "(M2b..M3b)"])
def test_invalid_and_missing(tree, body):
    (tree / "bad.js").write_text("\n// SHIM" + body)
    invalid, missing = check(tree)
    assert "bad.js:2: use" in invalid
    assert "target-design.md:2: add the missing S2" in missing

def test_extensionless_shell_script(tree):
    (tree / "command").write_text("#!/bin/sh\n# SHIM" + "(M2b..M3b) S2")
    assert check(tree) == []
