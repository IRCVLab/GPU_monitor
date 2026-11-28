"""Enforce the identity definitions and the remaining v1 wire-name sites."""
import ast
from collections import Counter
import re

from check_shims import root_arg, sources

CENTRAL = "collector/ids.py"
HOST = "agent/storage_viz_agent/ids.py"
GATE = "agent/bootstrap/gate.py"
NAMES = {"SERVER_ID_RE", "VOLUME_ID_RE", "BINDING_RE", "SHA256_RE", "REQUEST_ID_RE", "RUN_ID_RE", "RELEASE_SHA_RE"}
GATE_NAMES = {"SHA40": "RELEASE_SHA_RE", "SHA256": "SHA256_RE", "VOLUME_ID": "VOLUME_ID_RE", "REQUEST_ID": "REQUEST_ID_RE"}
# These programs also run from /usr/local/libexec or a standalone rollback copy.
# Pin each assignment and its spelling. Parity tests exercise the actual modules.
DEPLOY_SHA = r"^[0-9a-f]{40}$"
DEPLOY_DIGEST = r"^[0-9a-f]{64}$"
DEPLOY = {
    "deploy/server/activate-dashboard-release.py": {"SHA_RE": DEPLOY_SHA},
    "deploy/server/storage-monitor-release-puller.py": {"SHA_RE": DEPLOY_SHA, "DIGEST_RE": DEPLOY_DIGEST},
    "deploy/server/storage_dashboard_release.py": {"DIGEST_RE": DEPLOY_DIGEST},
    "deploy/server/storage-viz-proxy-launcher.py": {"SHA_RE": DEPLOY_SHA},
    "deploy/server/health-check-dashboard.py": {
        "SERVER_ID_RE": r"\A(?!\.{1,2}\Z)[A-Za-z0-9_.-]{1,128}\Z", "SHA_RE": DEPLOY_SHA,
    },
    "deploy/direct_proxy.py": {"RESCAN_PATH": r"^/api/v2/servers/((?!\.{1,2}/)[A-Za-z0-9_.-]{1,128})/rescan$"},
    # Installed as storage-dashboard-build-release.py as well as run from a checkout.
    "deploy/build-dashboard-release.py": {"SHA_RE": r"^[0-9a-fA-F]{40}$"},
}
LAUNCHER = "deploy/server/storage-viz-proxy-launcher.py"
INSTALLER = "deploy/server/install-dashboard-deployer.sh"
INSTALLER_DIGEST_CHECK = 'if not re.fullmatch(r"[0-9a-f]{64}", payload["managed_legacy_proxy_sha256"]):'


def deploy_calls(name, tree, errors):
    expected = DEPLOY.get(name, {})
    allowed = set()
    seen = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        key = stmt.targets[0].id
        if key not in expected:
            continue
        call = stmt.value
        if (key not in seen and isinstance(call, ast.Call) and ast.unparse(call.func) == "re.compile"
                and len(call.args) == 1 and not call.keywords and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == expected[key]):
            allowed.add(call)
            seen.add(key)
        else:
            errors.append(f"{name}:{stmt.lineno}: restore the standalone {key} definition")
    for key in sorted(expected.keys() - seen):
        errors.append(f"{name}:1: restore the standalone {key} definition")
    if name == LAUNCHER:
        calls = [node for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name == "_legacy_target"
                 for node in ast.walk(fn) if isinstance(node, ast.Call)
                 and ast.unparse(node) == "re.fullmatch('[0-9a-f]{64}', managed_digest)"]
        if len(calls) == 1:
            allowed.add(calls[0])
        else:
            errors.append(f"{name}:1: restore the standalone rollback digest check")
    return allowed


GENERATION = re.compile(r"\b(?:published|snapshot|scan)_generation\b")
def runtime(path):
    return not ({"tests", "test", "node_modules"} & set(path.parts) or path.name.startswith("test_")
                or path.name.endswith(".test.js") or path.name == "conftest.py")


def unanchor(pattern):
    return pattern.removeprefix(r"\A").removeprefix("^").removesuffix(r"\Z").removesuffix("$")


def regex_calls(tree):
    imports = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == "re" for a in n.names if a.name in {"compile", "fullmatch", "match"}}
    modules = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
               for a in n.names if a.name == "re"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = ast.unparse(node.func)
        if name not in imports and name not in {m + "." + f for m in modules for f in ("compile", "match", "fullmatch")}:
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            yield node, unanchor(value.value)


def text_patterns(source):
    # Python snippets in shell and remote command strings, plus JS regex literals.
    literal = r"""(?:re\.(?:compile|fullmatch|match)|RegExp)\s*\(\s*r?(["'])(.*?)\1"""
    for hit in re.finditer(literal, source):
        yield hit.start(), unanchor(hit[2])
    for hit in re.finditer(r"/(\^?\[[^/\s]+?\}\$?)/", source):
        yield hit.start(), unanchor(hit[1])


def check(root):
    errors = []
    canonical = set()
    for name, required in [(CENTRAL, NAMES), (HOST, NAMES), (GATE, GATE_NAMES)]:
        path = root / name
        tree = ast.parse(path.read_text()) if path.exists() else ast.Module(body=[], type_ignores=[])
        calls = dict(regex_calls(tree))
        counts = Counter()
        owned = set()
        for node in tree.body:
            if not isinstance(node, ast.Assign) or node.value not in calls:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in required:
                    counts[target.id] += 1
                    owned.add(node.value)
                    canonical.add(calls[node.value])
        for key in sorted(required):
            if counts[key] != 1:
                errors.append(f"{name}:1: define {key} exactly once here")
        for call, pattern in calls.items():
            if call not in owned and pattern in canonical:
                errors.append(f"{name}:{call.lineno}: reuse the existing identity definition")

    for path in sources(root):
        name = path.as_posix()
        if not runtime(path) or name == "tools/lint_ids.py":
            continue
        source = (root / path).read_text()
        if path.suffix == ".py" and name not in {CENTRAL, HOST, GATE}:
            tree = ast.parse(source)
            allowed = deploy_calls(name, tree, errors)
            for node, pattern in regex_calls(tree):
                if node in allowed:
                    continue
                if pattern in canonical or pattern == "[A-Za-z0-9_.-]{1,128}":
                    errors.append(f"{name}:{node.lineno}: import the identity regex from the tier's ids module")
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value not in allowed and (
                    ast.unparse(node.value.func).endswith(".compile") and
                    any(isinstance(t, ast.Name) and t.id in NAMES for t in node.targets)
                ):
                    message = f"{name}:{node.lineno}: import the identity regex from the tier's ids module"
                    if message not in errors:
                        errors.append(message)
            snippets = [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant)
                        and isinstance(n.value, str) and "\n" in n.value]
        else:
            snippets = [(1, source)] if path.suffix in {".js", ".mjs", ".html", ".sh"} else []
        if name == INSTALLER:
            # Exactly once in the activator-result parser embedded in this shell function.
            start = source.find("prepare_legacy_proxy_recovery() {")
            end = source.find("\n}", start)
            section = source[start:end]
            if start < 0 or end < 0 or section.splitlines().count(INSTALLER_DIGEST_CHECK) != 1:
                errors.append(f"{name}:1: restore the standalone recovery digest check")
            else:
                offset = start + section.index(INSTALLER_DIGEST_CHECK)
                snippets = [(1, source[:offset] + (" " * len(INSTALLER_DIGEST_CHECK))
                             + source[offset + len(INSTALLER_DIGEST_CHECK):])]
        for first, snippet in snippets:
            for offset, pattern in text_patterns(snippet):
                if pattern in canonical:
                    line = first + snippet.count("\n", 0, offset)
                    errors.append(f"{name}:{line}: reuse the canonical identity at the boundary")
        for n, line in enumerate(source.splitlines(), 1):
            if GENERATION.search(line):
                errors.append(f"{name}:{n}: use the canonical identity")
    return errors


if __name__ == "__main__":
    errors = check(root_arg())
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
