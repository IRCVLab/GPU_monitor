"""Check §2.7 writers and explicit JSON ingress sites, including standalone commands."""
import ast
from collections import Counter
from fnmatch import fnmatchcase
import re

from check_shims import root_arg, sources
from lint_ids import runtime

PUBLISH = "agent/storage_viz_agent/publish.py"
RUNNER = "agent/storage_viz_agent/runner.py"
GATE = "agent/bootstrap/gate.py"
UPDATER = "agent/bootstrap/update.py"
# A suffix identifies the owned resource even when its installation root varies.
FILE_OWNERS = {
    "captures/*/*.json.gz": {PUBLISH, "collector/store.py"},  # host and central copies
    "manifest.json": {PUBLISH},
    "run.json": {RUNNER},
    "scan.lock": {RUNNER, UPDATER},
    "requests/*": {GATE, RUNNER},
    "progress.json": {"scanner/hstscan.c"},
    "update/state.json": {UPDATER},
    "opt/storage-viz/current": {UPDATER},
    "record.json": {"collector/store.py"},
    "servers/*/*": {"collector/store.py"},
    "rollout.json": {"collector/fleet.py"},
    "agent/storage-viz-agent.*": {"deploy/server/storage-monitor-release-puller.py"},
}
FIELD_OWNERS = {
    "actor.observation": "collector/actor.py", "actor.view": "collector/actor.py",
    "fleet.rollout_view": "collector/fleet.py", "session.csrf": "api/auth.py",
    "state.route": "viewer/router.js", "state.selection": "viewer/state.js",
    "state.sorts": "viewer/state.js", "state.theme": "viewer/state.js",
    "state.data": "viewer/effects.js",
}
BOUNDARIES = {"collector/contract.py", "agent/storage_viz_agent/capture.py", "viewer/api.js"}

# Key on the exact expression and its enclosing function. A second identical
# expression in the same function consumes another allowance and fails.
PARSE_SITES = {
    "agent/bootstrap/gate.py": {
        ('_read_json', 'json.loads(data)'): 'Host-local telemetry files for gate observation.',
        ('observe', 'json.loads(manifest_bytes)'): 'Sequence from the local published manifest.',
    },
    "agent/bootstrap/update.py": {
        ('read_bundle', 'json.loads(members.pop(MANIFEST)[0])'): 'Signed manifest at the bundle ingestion boundary.',
        ('read_state', "json.loads(self.paths.state.read_text(encoding='utf-8'))"): 'Updater activation ledger.',
        ('release_manifest', "json.loads((self.paths.releases / sha / MANIFEST).read_text(encoding='utf-8'))"): 'Installed release manifest.',
    },
    "agent/release_cli.py": {
        ('selftest', "json.loads(out.read_text(encoding='utf-8'))"): 'Private scanner output from the release self-test.',
    },
    "agent/storage_viz_agent/config.py": {
        ('load_config', "json.loads(cfg_path.read_text(encoding='utf-8'))"): 'Agent configuration.',
    },
    "api/auth.py": {
        ('_validate_session_cookie', "json.loads(_unb64(encoded).decode('utf-8'))"): 'Authenticated session state issued by this process.',
    },
    "api/fixtures.py": {
        ('_read', 'json.loads(path.read_bytes())'): 'Offline fixture inventory and snapshots.',
    },
    "collector/fleet.py": {
        ('load_desired_agent', 'json.loads(_read_bounded(path, MAX_METADATA_BYTES))'): 'Agent bundle metadata the release puller verified into the active dashboard release.',
    },
    "collector/rollout.py": {
        ('parse', 'json.loads(data)'): 'rollout.json, written only by fleet.Rollout.',
    },
    "deploy/e2e/collector_gate_e2e.py": {
        ('seed', "json.loads(self.docker('cat', '/var/lib/storage-viz/manifest.json'))"): 'E2E harness: the disposable test host.',
        ('update_fail', "json.loads(self.docker('cat', '/var/lib/storage-viz/update/state.json'))"): 'E2E harness: the disposable test host.',
        ('update_fail', "json.loads((self.args.work / 'central-state' / 'rollout.json').read_text())"): 'E2E harness: its own central state.',
    },
    "collector/inventory.py": {
        ('load_inventory', "json.loads(cfg_path.read_text(encoding='utf-8'))"): 'Collector inventory configuration.',
    },
    "collector/store.py": {
        ('load', 'json.loads(self.record_path.read_bytes())'): 'Local persisted record.',
    },
    "collector/switch_traffic.py": {
        ('_load', 'json.loads(self._path.read_bytes())'): 'network.json, written only by SwitchTraffic.',
    },
    "tools/check_shims.py": {
        ('done_units', 'json.loads((root / PROGRESS).read_text())'): 'Lint progress configuration.',
    },
    "deploy/bootstrap_host.py": {
        ('revert', 'json.loads(manifest_path.read_text())'): 'Installation rollback manifest.',
        ('preflight_bundle', "json.loads((self.payload / 'agent/storage-viz-agent.json').read_text())"): 'Bootstrap payload metadata.',
        ('agent_config', 'json.loads(agent_yaml.read_text())'): 'Agent and scanner configuration.',
        ('agent_config', 'json.loads(scanner_yaml.read_text())'): 'Agent and scanner configuration.',
        ('activate_bundle', "json.loads((self.data / 'update/state.json').read_text())"): 'Updater activation ledger.',
    },
    "deploy/bootstrap-truenas.py": {
        ('midclt', 'json.loads(text)'): 'TrueNAS middleware replies to the admin setup session.',
        ('read_bundle', 'json.loads((bundle_dir / METADATA).read_text())'): 'Agent bundle metadata in the active dashboard release.',
        ('install_release', 'json.loads(state_reply.stdout)'): 'Updater activation ledger on the TrueNAS host.',
        ('observe', 'json.loads(body)'): 'The gate observe reply the setup checks once.',
    },
    "deploy/build-agent-release.py": {
        ('build', "json.loads(git(repo, 'show', f'{source_sha}:{FLOOR_FILE}'))"): 'Release-floor build configuration.',
    },
    "deploy/server/activate-dashboard-release.py": {
        ('_read_json_file', "json.loads(raw.decode('utf-8'))"): 'Local release metadata.',
        ('_read_state', 'json.load(handle)'): 'Activator state.',
    },
    "deploy/server/health-check-dashboard.py": {
        ('_load_enabled_server_ids', "json.loads(path.read_text(encoding='utf-8'))"): 'Local inventory.',
        ('_json_response', "json.loads(raw.decode('utf-8') if raw else '{}')"): 'Health probe response at the standalone command boundary.',
    },
    "deploy/server/smoke-dashboard-release.py": {
        ('smoke', "json.loads(fetch(proxy_port, '/api/v2/fleet'))"): 'Smoke probe response.',
        ('main', "json.loads(metadata.read_text(encoding='utf-8'))"): 'Smoke candidate metadata and activation result.',
        ('main', 'json.loads(prepared)'): 'Smoke candidate metadata and activation result.',
    },
    "deploy/server/storage-monitor-release-puller.py": {
        ('default_get_json', "json.loads(response.read().decode('utf-8'))"): 'Remote CI release metadata at puller ingress.',
        ('read_puller_state', "json.loads(state_path(config).read_text(encoding='utf-8'))"): 'Local release ledger.',
        ('read_agent_pending', "json.loads(agent_pending_path(config).read_text(encoding='utf-8'))"): 'Pending agent release.',
        ('read_failed_release', "json.loads(failed_release_path(config).read_text(encoding='utf-8'))"): 'Failure ledger.',
        ('authorize', 'json.loads(result.stdout)'): 'Authorization helper result.',
        ('verify_agent_files', "json.loads(files[AGENT_METADATA].decode('utf-8'))"): 'Agent artifact metadata and manifest.',
        ('verify_agent_files', "json.loads(member.read(1024 * 1024).decode('utf-8'))"): 'Agent artifact metadata and manifest.',
        ('previous_agent_bundle', "json.loads(config.activation_state_path.read_text(encoding='utf-8'))"): 'Prior activation record.',
        ('validate_artifact', "json.loads(metadata.read_text(encoding='utf-8'))"): 'Local candidate metadata.',
        ('read_activation_state', "json.loads(config.activation_state_path.read_text(encoding='utf-8'))"): 'Activation ledger.',
        ('_parse_activation_stdout', 'json.loads(stdout)'): 'Activator result.',
        ('_bounded_activation_error', 'json.loads(stderr)'): 'Activator error.',
    },
    "deploy/server/storage_dashboard_release.py": {
        ('inspect_archive', "json.loads(data.decode('utf-8'))"): 'Release archive manifest.',
    },
    "deploy/server/storage-viz-proxy-launcher.py": {
        ('_state', "json.loads(config.state_path.read_text(encoding='utf-8'))"): 'Local activation state.',
    },
}

# Each installer snippet decodes one result from its own activator.
INSTALLER_READS = {"parse_status_path", "prepare_legacy_proxy_recovery",
                   "is_no_state_rollback_error", "validate_recovery_status"}
PARSE_TEXT = re.compile(r"\b(?:json\s*\.\s*loads?|JSON\s*\.\s*parse)\s*\(")
WRITE_HELPERS = {"write_json", "atomic", "atomic_write", "write_atomic", "atomic_write_json", "write_json_atomic"}


def write_target(call):
    func = call.func
    name = ast.unparse(func).split(".")[-1]
    args = call.args
    keywords = {kw.arg: kw.value for kw in call.keywords}
    if name in {"write_text", "write_bytes"} and isinstance(func, ast.Attribute):
        return func.value
    if name in {"rename", "replace"}:
        return keywords.get("dst") or keywords.get("target") or (args[-1] if args else None)
    if name.strip("_") in WRITE_HELPERS or name.lstrip("_").startswith(("atomic_write_", "write_atomic_")):
        return keywords.get("path") or (args[0] if args else None)
    if name == "open":
        method = isinstance(func, ast.Attribute) and ast.unparse(func.value) not in {"io", "builtins", "os"}
        pos = 0 if method else 1
        mode = keywords.get("mode") or (args[pos] if len(args) > pos else None)
        if mode and any(c in ast.unparse(mode) for c in ("O_CREAT", "O_WRONLY", "O_RDWR")):
            return args[0]
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and set("wax+") & set(mode.value):
            return func.value if method else keywords.get("file") or (args[0] if args else None)
    return None


def path_value(node, bindings, seen=frozenset()):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Name, ast.Attribute)):
        key = ast.unparse(node)
        if key in bindings and key not in seen:
            return path_value(bindings[key], bindings, seen | {key})
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        join = "/" if isinstance(node.op, ast.Div) else ""
        return path_value(node.left, bindings, seen) + join + path_value(node.right, bindings, seen)
    if isinstance(node, ast.JoinedStr):
        return "".join(path_value(value, bindings, seen) for value in node.values)
    if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in {"Path", "str", "join", "joinpath"}:
        args = node.args
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            args = [node.func.value, *args]
        return "/".join(path_value(arg, bindings, seen) for arg in args)
    return "*"


def python_errors(name, source):
    errors = []
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname or a.name: "json" for a in node.names if a.name == "json"})
        if isinstance(node, ast.ImportFrom) and node.module == "json":
            aliases.update({a.asname or a.name: "json." + a.name for a in node.names})
    used = Counter()
    def visit(node, scope, bindings):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope, bindings = node.name, dict(bindings)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                key = ast.unparse(target)
                if key in FIELD_OWNERS and name != FIELD_OWNERS[key]:
                    errors.append(f"{name}:{node.lineno}: move {key} assignment to {FIELD_OWNERS[key]}")
                if node.value:
                    bindings[key] = node.value
        if isinstance(node, ast.Call):
            parts = ast.unparse(node.func).split(".", 1)
            callee = ".".join([aliases.get(parts[0], parts[0]), *parts[1:]])
            if callee in {"json.loads", "json.load"} and name not in BOUNDARIES:
                key = scope, ast.unparse(node)
                reason = PARSE_SITES.get(name, {}).get(key, "")
                used[key] += 1
                if not (name == "api/routes.py" and scope == "rescan") and (
                        not reason or used[key] > 1):
                    errors.append(f"{name}:{node.lineno}: parse external JSON in the contract or request boundary")
            target = write_target(node)
            if target is not None:
                dest = path_value(target, bindings).strip("/")
                for pattern, owners in FILE_OWNERS.items():
                    if fnmatchcase(dest, pattern) or fnmatchcase(dest, "*/" + pattern):
                        if name not in owners:
                            errors.append(f"{name}:{node.lineno}: write {pattern} through {' or '.join(sorted(owners))}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" in node.value:
            for offset, line in enumerate(node.value.splitlines()):
                if PARSE_TEXT.search(line):
                    errors.append(f"{name}:{node.lineno + offset}: move the embedded JSON parse to its boundary")
        for child in ast.iter_child_nodes(node):
            visit(child, scope, bindings)
    visit(tree, "", {})
    return errors


def check(root):
    errors = []
    for path in sources(root):
        name = path.as_posix()
        if not runtime(path) or name in {"tools/lint_owners.py", "viewer/echarts.min.js", "deploy/e2e/run-agent-channel-e2e.sh"}:
            continue
        source = (root / path).read_text()
        if path.suffix == ".py":
            errors.extend(python_errors(name, source))
        elif path.suffix in {".js", ".mjs", ".html", ".sh"}:
            if path.suffix != ".sh" and name not in BOUNDARIES:
                for match in PARSE_TEXT.finditer(source):
                    number = source.count("\n", 0, match.start()) + 1
                    errors.append(f"{name}:{number}: parse external JSON at the process boundary")
            scope = ""
            used = set()
            for number, line in enumerate(source.splitlines(), 1):
                function = re.match(r"^(\w+)\(\)\s*\{", line)
                if function:
                    scope = function[1]
                if name == "deploy/server/install-dashboard-deployer.sh" and scope in INSTALLER_READS and (
                        line.strip() == "payload = json.load(sys.stdin)" and scope not in used):
                    used.add(scope)
                    continue
                if path.suffix == ".sh" and PARSE_TEXT.search(line):
                    errors.append(f"{name}:{number}: parse external JSON at the process boundary")
                for key, owner in FIELD_OWNERS.items():
                    if name != owner and re.search(rf"\b{re.escape(key)}\s*=(?!=|>)", line):
                        errors.append(f"{name}:{number}: move {key} assignment to {owner}")
    return errors


if __name__ == "__main__":
    errors = check(root_arg())
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
