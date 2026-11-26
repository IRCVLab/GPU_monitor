"""Keep wall-clock readings and capacity thresholds out of viewer judgments."""
import re

from check_shims import root_arg

CLOCK = re.compile(r"\b(?:Date\s*\.\s*now|new\s+Date)\s*\(\s*\)")
FIELD = r"\b[\w$]*(?:usage|used|percent|pct|severity|capacity|utilization)[\w$]*\b"
NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
OP = r"(?:[<>]=?|={2,3})"
THRESHOLD = re.compile(rf"(?:{FIELD}\s*{OP}\s*{NUMBER}\b|(?<![\w.]){NUMBER}\s*{OP}\s*(?:\w+\.)*{FIELD})", re.I)
# The browser smoke test reads its local deadline while waiting for Chromium.
SMOKE = "viewer/browser/stale-select-hit.test.js"
DEADLINES = {
    "const deadline = Date.now() + timeoutMs;",
    'if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}: ${err.message}`);',
    'if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);',
}


def check(root):
    errors = []
    for path in sorted((root / "viewer").rglob("*")):
        name = path.relative_to(root).as_posix()
        # Vendored ECharts handles animation clocks and chart geometry.
        if name in {"viewer/format.js", "viewer/echarts.min.js"} or path.suffix not in {".js", ".mjs", ".html"}:
            continue
        source = path.read_text()
        for pattern, fix in [(CLOCK, "use the API age, freshness or ETA field"),
                             (THRESHOLD, "render the API severity or used_pct field")]:
            for hit in pattern.finditer(source):
                number = source.count("\n", 0, hit.start()) + 1
                line = source[source.rfind("\n", 0, hit.start()) + 1:].split("\n", 1)[0].strip()
                if pattern is CLOCK and name == SMOKE and line in DEADLINES:
                    continue
                # The overview fixture supplies a timestamp as input to a rendering test.
                if (pattern is CLOCK and name == "viewer/test/overview.test.js"
                        and line == "const observed = Date.now() / 1000;"):
                    continue
                errors.append(f"{name}:{number}: {fix}")
    return errors


if __name__ == "__main__":
    errors = check(root_arg())
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
