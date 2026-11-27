import pytest

from check_shims import PROGRESS
from lint_owners import check


@pytest.fixture
def tree(tmp_path):
    (tmp_path / PROGRESS).parent.mkdir(parents=True)
    (tmp_path / PROGRESS).write_text('{"schema":"storage-viz.redesign-progress/1","done":[]}')
    (tmp_path / "collector").mkdir()
    (tmp_path / "collector/contract.py").write_text("import json\ndef parse(body): return json.loads(body)")
    return tmp_path


@pytest.mark.parametrize(("body", "fix"), [
    ('path = root / "manifest.json"\npath.write_text(data)', ":3: write manifest.json through"),
    ('open("update/state.json", mode="a")', ":2: write update/state.json through"),
    ('os.replace(temp, root / "progress.json")', ":2: write progress.json through"),
    ('atomic_write(root / "servers" / sid / "state.json", data)', ":2: write servers/*/* through"),
    ('from json import loads as parse\nparse(payload)', ":3: parse external JSON"),
])
def test_wrong_writer_or_parser(tree, body, fix):
    path = tree / "collector/stray.py"
    path.write_text('value = (root / "manifest.json").read_bytes()\n')
    assert check(tree) == []
    path.write_text(path.read_text() + body)
    assert "collector/stray.py" + fix in check(tree)[0]


def test_existing_allowance_cannot_cover_new_parse(tree):
    path = tree / "collector/inventory.py"
    source = "def load_inventory(cfg_path):\n    raw = json.loads(cfg_path.read_text(encoding='utf-8'))\n"
    path.write_text(source)
    assert check(tree) == []
    path.write_text(source + source.splitlines(True)[1])
    assert "collector/inventory.py:3: parse external JSON" in check(tree)[0]
