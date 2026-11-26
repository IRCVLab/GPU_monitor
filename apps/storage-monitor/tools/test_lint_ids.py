import runpy
import shutil

import pytest

from check_shims import PROGRESS, ROOT
from lint_ids import CENTRAL, GATE, GATE_NAMES, HOST, NAMES, check


@pytest.mark.parametrize("owner,names", [(HOST, {n: n for n in NAMES}), (GATE, GATE_NAMES)])
def test_identity_languages_accept_the_same_samples(owner, names):
    central = runpy.run_path(str(ROOT / CENTRAL))
    side = runpy.run_path(str(ROOT / owner))
    samples = ["", "host.a-1", ".", "..", "a" * 129, "a/b", "한", "b-" + "a" * 16,
               "v-" + "f" * 16, "x-" + "0" * 16] + ["a" * size for size in (31, 32, 33, 40, 64)]
    for local, shared in names.items():
        for value in samples + [s + "\n" for s in samples] + [s.upper() for s in samples]:
            assert bool(side[local].fullmatch(value)) == bool(central[shared].fullmatch(value)), (local, value)


def test_planted_duplicate_and_expired_shim(tmp_path):
    for path in [CENTRAL, HOST, GATE, PROGRESS]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / path, tmp_path / path)
    path = tmp_path / "collector/view.py"
    path.write_text("value = row.scan_generation  # SHIM" + "(M2b..M8) S5\n")
    assert check(tmp_path) == []
    path.write_text(path.read_text() + 'import re\nBAD = re.compile(r"^[0-9a-f]{64}$")\n')
    assert "collector/view.py:3: import" in check(tmp_path)[0]
    (tmp_path / PROGRESS).write_text((tmp_path / PROGRESS).read_text().replace('"M7"', '"M8"'))
    assert any("collector/view.py:1: use" in e for e in check(tmp_path))
