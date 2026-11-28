import ast
import re
import runpy
import shutil

import pytest

from check_shims import PROGRESS, ROOT
from lint_ids import CENTRAL, DEPLOY, GATE, GATE_NAMES, HOST, INSTALLER, LAUNCHER, NAMES, check


SAMPLES = ["", "host.a-1", ".", "..", "...", "a" * 128, "a" * 129, "a/b", "한",
           "b-" + "a" * 16, "v-" + "f" * 16, "x-" + "0" * 16] + [
               "a" * size for size in (31, 32, 33, 39, 40, 41, 63, 64, 65)]
SAMPLES += [s.upper() for s in SAMPLES]
SAMPLES += [s + "\n" for s in SAMPLES]


@pytest.mark.parametrize("owner,names", [(HOST, {n: n for n in NAMES}), (GATE, GATE_NAMES)])
def test_identity_languages_accept_the_same_samples(owner, names):
    central = runpy.run_path(str(ROOT / CENTRAL))
    side = runpy.run_path(str(ROOT / owner))
    for local, shared in names.items():
        for value in SAMPLES:
            assert bool(side[local].fullmatch(value)) == bool(central[shared].fullmatch(value)), (local, value)


def test_planted_duplicate_and_expired_shim(tmp_path):
    for path in [CENTRAL, HOST, GATE, PROGRESS]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / path, tmp_path / path)
    path = tmp_path / "collector/actor.py"
    path.write_text("value = row.scan_generation  # SHIM" + "(M2b..M5c) S3\n")
    assert any("collector/actor.py:1: use" in error for error in check(tmp_path))
    path.write_text(path.read_text() + 'import re\nBAD = re.compile(r"^[0-9a-f]{64}$")\n')
    assert "collector/actor.py:3: import" in check(tmp_path)[0]
    (tmp_path / PROGRESS).write_text((tmp_path / PROGRESS).read_text().replace('"M7"', '"M5c"'))
    assert any("collector/actor.py:1: use" in e for e in check(tmp_path))

@pytest.mark.parametrize("path,defs", DEPLOY.items())
def test_standalone_deploy_patterns(tmp_path, monkeypatch, path, defs):
    # Run in the install layout without a checkout or any collector package beside it.
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    shutil.copy(ROOT / "deploy/server/storage_dashboard_release.py", libexec)
    program = libexec / (ROOT / path).name
    shutil.copy(ROOT / path, program)
    monkeypatch.syspath_prepend(str(libexec))
    namespace = runpy.run_path(str(program))
    central = runpy.run_path(str(ROOT / CENTRAL))
    types = {"SHA_RE": "RELEASE_SHA_RE", "DIGEST_RE": "SHA256_RE",
             "SERVER_ID_RE": "SERVER_ID_RE", "RESCAN_PATH": "SERVER_ID_RE"}
    for var in defs:
        for sample in SAMPLES:
            candidate = f"/api/v2/servers/{sample}/rescan" if var == "RESCAN_PATH" else sample
            # The dashboard builder has historically accepted mixed-case CLI hashes.
            canonical = sample.lower() if path == "deploy/build-dashboard-release.py" else sample
            assert bool(namespace[var].fullmatch(candidate)) == bool(central[types[var]].fullmatch(canonical))


@pytest.mark.parametrize("path", [INSTALLER, LAUNCHER])
def test_standalone_rollback_digest_fullmatch(path):
    source = (ROOT / path).read_text()
    if path == INSTALLER:
        line = next(line for line in source.splitlines() if "re.fullmatch" in line and "managed_legacy_proxy_sha256" in line)
        tree = ast.parse(line + "\n    pass")
    else:
        tree = next(fn for fn in ast.parse(source).body if isinstance(fn, ast.FunctionDef) and fn.name == "_legacy_target")
    call, = (n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "re.fullmatch")
    pattern = ast.literal_eval(call.args[0])
    central = runpy.run_path(str(ROOT / CENTRAL))["SHA256_RE"]
    for sample in SAMPLES:
        assert bool(re.fullmatch(pattern, sample)) == bool(central.fullmatch(sample))


def test_extra_deploy_copy_fails(tmp_path):
    for path in [CENTRAL, HOST, GATE, PROGRESS, LAUNCHER]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / path, tmp_path / path)
    with (tmp_path / LAUNCHER).open("a") as stream:
        stream.write('\nCOPY = re.compile(r"^[0-9a-f]{40}$")\n')
    assert any(LAUNCHER in error and "import the identity regex" in error for error in check(tmp_path))
