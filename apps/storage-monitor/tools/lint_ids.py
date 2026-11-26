"""Enforce the identity definitions and the remaining v1 wire-name sites."""
import ast
from collections import Counter
import re

from check_shims import done_units, markers, root_arg, sources

CENTRAL = "collector/ids.py"
HOST = "agent/storage_viz_agent/ids.py"
GATE = "agent/bootstrap/gate.py"
NAMES = {"SERVER_ID_RE", "VOLUME_ID_RE", "BINDING_RE", "SHA256_RE", "REQUEST_ID_RE", "RUN_ID_RE", "RELEASE_SHA_RE"}
GATE_NAMES = {"SHA40": "RELEASE_SHA_RE", "SHA256": "SHA256_RE", "VOLUME_ID": "VOLUME_ID_RE", "REQUEST_ID": "REQUEST_ID_RE"}
GENERATION = re.compile(r"\b(?:published|snapshot|scan)_generation\b")
# Each occurrence in these v1 adapters must also carry the corresponding live marker.
V1 = {
    "api/routes.py": "S5", "collector/view.py": "S5", "collector/legacy.py": "S2",
    "collector/actor.py": "S3", "collector/snapshot.py": "S3", "collector/store.py": "S3",
    "viewer/api.js": "S5", "viewer/effects.js": "S5", "viewer/views/scan-status.js": "S5",
    "tools/gen_sample.py": "S5", "tools/scrub_fixture.py": "S5",
}


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

    done = done_units(root)
    for path in sources(root):
        name = path.as_posix()
        if not runtime(path) or name == "tools/lint_ids.py":
            continue
        source = (root / path).read_text()
        if path.suffix == ".py" and name not in {CENTRAL, HOST, GATE}:
            tree = ast.parse(source)
            for node, pattern in regex_calls(tree):
                if pattern in canonical or pattern == "[A-Za-z0-9_.-]{1,128}":
                    errors.append(f"{name}:{node.lineno}: import the identity regex from the tier's ids module")
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and (
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
        for first, snippet in snippets:
            for offset, pattern in text_patterns(snippet):
                if pattern in canonical:
                    line = first + snippet.count("\n", 0, offset)
                    errors.append(f"{name}:{line}: reuse the canonical identity at the boundary")
        for n, line in enumerate(source.splitlines(), 1):
            if GENERATION.search(line) and not any(
                m and m[2] not in done and m[3] == V1.get(name) for m in markers(line)
            ):
                errors.append(f"{name}:{n}: use the canonical identity or annotate the listed v1 site with its live shim")
    return errors


if __name__ == "__main__":
    errors = check(root_arg())
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
