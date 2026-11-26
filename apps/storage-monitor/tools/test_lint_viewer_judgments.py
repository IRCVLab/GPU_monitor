import pytest

from lint_viewer_judgments import check


@pytest.mark.parametrize(("comparison", "fix"), [
    ("const stale = Date.now() - updated > day;", "use the API"),
    ("const age = new Date() - scanTime;", "use the API"),
    ("row.used_pct\n >= 80", "render the API"),
    ("0.9 < row.capacity", "render the API"),
    ("severity === 3", "render the API"),
])
def test_view_judgments(tmp_path, comparison, fix):
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    path = viewer / "panel.js"
    path.write_text("const label = row.severity;\nnew Date(row.finished_unix);")
    assert check(tmp_path) == []
    path.write_text(path.read_text() + "\n" + comparison)
    assert f"viewer/panel.js:3: {fix}" in check(tmp_path)[0]
