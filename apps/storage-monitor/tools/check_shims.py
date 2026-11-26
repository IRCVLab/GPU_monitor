"""Check source annotations against the redesign's shim retirement table."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DESIGN = Path("docs/redesign/target-design.md")
PROGRESS = Path("docs/redesign/progress.json")
UNIT = r"M\d+[a-z]?"
IDS = r"[A-Z]\d+(?:,\s*[A-Z]\d+)*"
PREFIX = re.compile(r"\bSHIM\s*(?=\(|\bM\d)")
MARKER = re.compile(rf"SHIM\(({UNIT})\.\.({UNIT})\)\s+(?:old store import \()?({IDS})\b")
EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".sh", ".bash", ".html", ".htm", ".css"}


def root_arg():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args().root


def sources(root):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if {".git", "node_modules", ".venv"} & set(relative.parts) or not path.is_file():
            continue
        if path.suffix in EXTENSIONS:
            yield relative
        elif not path.suffix:
            with path.open("rb") as stream:
                header = stream.readline(160)
            if header.startswith(b"#!") and re.search(rb"\b(?:python[0-9.]*|bash|sh|node)\b", header):
                yield relative


def markers(line):
    for prefix in PREFIX.finditer(line):
        yield MARKER.match(line, prefix.start())


def done_units(root):
    try:
        progress = json.loads((root / PROGRESS).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{PROGRESS}:1: restore the redesign progress file") from exc
    if (not isinstance(progress, dict) or progress.get("schema") != "storage-viz.redesign-progress/1"
            or not isinstance(progress.get("done"), list)
            or not all(isinstance(unit, str) and re.fullmatch(UNIT, unit) for unit in progress["done"])):
        raise ValueError(f"{PROGRESS}:1: restore the progress schema and list of completed units")
    return set(progress["done"])


def shim_table(root):
    table = {}
    section = False
    for number, line in enumerate((root / DESIGN).read_text().splitlines(), 1):
        if re.match(r"^#{1,3} ", line):
            section = line.startswith("### 6.2 ")
        if section and re.match(r"^\|\s*[A-Z]\d+\s*\|", line):
            shim, _, added, deleted = map(str.strip, line.strip("|").split("|"))
            table[shim] = (number, added, re.findall(rf"\b{UNIT}\b", deleted))
    if not table:
        raise ValueError(f"{DESIGN}:1: restore the §6.2 shim table")
    return table


def check(root):
    done = done_units(root)
    table = shim_table(root)
    known_units = done | set(re.findall(rf"\b{UNIT}\b", (root / DESIGN).read_text()))
    counts = Counter()
    errors = []
    for path in sources(root):
        for number, line in enumerate((root / path).read_text().splitlines(), 1):
            for marker in markers(line):
                location = f"{path}:{number}"
                if not marker:
                    errors.append(f"{location}: use a unit range and shim ID from §6.2")
                    continue
                start, end, ids = marker.groups()
                for shim in re.split(r",\s*", ids):
                    if start not in known_units or shim not in table or end not in table[shim][2]:
                        errors.append(f"{location}: use the shim ID and deletion unit from §6.2")
                    elif end in done:
                        errors.append(f"{location}: remove expired {shim} ({end} is done)")
                    else:
                        counts[shim, start, end] += 1
    for shim, (number, _, ends) in table.items():
        if shim != "R1" and not set(ends) <= done and not any(key[0] == shim for key in counts):
            errors.append(f"{DESIGN}:{number}: add the missing {shim} marker to its implementation")
    print("Shim  Added  Deleted  Markers")
    for (shim, start, end), count in sorted(counts.items()):
        print(f"{shim:<5} {start:<6} {end:<8} {count}")
    return errors


if __name__ == "__main__":
    errors = check(root_arg())
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
