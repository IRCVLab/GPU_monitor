"""Translate scanner rows to per-volume captures at the publication boundary."""
import base64
import json


def read(path, default=None):
    try:
        with path.open("rb") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def counter(value):
    if type(value) is not int or not 0 <= value < 10**18:
        raise ValueError("invalid scanner counter")
    return value


def timestamp(value):
    if type(value) is not int or not 0 <= value <= 4102444800:
        raise ValueError("invalid scanner timestamp")
    return value


def tree_node(node, depth=0):
    if depth > 128 or not isinstance(node["name"], str) or node["kind"] != "directory":
        raise ValueError("invalid scanner tree")
    result = {key: counter(node[key]) for key in ("bytes", "files", "uid", "other_bytes")}
    result.update(name=node["name"], kind="dir", mtime=timestamp(node["mtime"]),
                  children=[tree_node(n, depth + 1) for n in node.get("children", [])])
    children = result["children"]
    if (children or result["other_bytes"]) and result["bytes"] != result["other_bytes"] + sum(c["bytes"] for c in children):
        raise ValueError("tree byte rollup mismatch")
    if result["files"] < sum(c["files"] for c in children):
        raise ValueError("tree file rollup mismatch")
    if "name_b64" in node:
        result["name_b64"] = node["name_b64"]
    return result


def split_group_output(raw, volumes, run_id, group, started, finished):
    if finished < started:
        raise ValueError("scan finished before it started")
    mounts = {m["path"]: m for m in raw["mounts"]}
    roots = {r.path for members in volumes.values() for r in members}
    if set(mounts) - roots or len(mounts) != len(raw["mounts"]):
        raise ValueError("unexpected scanner target")
    lists = {p: dict(users=[], stale=[], top_files=[], errors=[]) for p in roots}
    for user in raw["users"]:
        for path, size in user["by_mount"].items():
            lists[path]["users"].append(dict(uid=counter(user["uid"]), name=user["name"],
                bytes=counter(size), files=counter(user["files_by_mount"][path])))
    for source, target in (("top_files", "top_files"), ("stale", "stale"), ("blocked", "errors")):
        for item in raw[source]:
            path = item["path"]
            raw_path = base64.b64decode(item["path_b64"], validate=True) if "path_b64" in item else path.encode()
            if len(raw_path) > 4096 or not path.startswith("/") or ".." in path.split("/") or "\0" in path:
                raise ValueError("invalid scanner path")
            owner = max((r for r in roots if r == path or path.startswith(r + "/")), key=len)
            row = {"path": path}
            if "path_b64" in item:
                row["path_b64"] = item["path_b64"]
            if target == "errors":
                if item["reason"] not in {"EACCES", "VANISHED", "REPLACED", "UNREPRESENTABLE", "FD_BUDGET_REOPEN_FAILED"}:
                    raise ValueError("unknown scanner error reason")
                row["reason"] = item["reason"]
            else:
                row.update(bytes=counter(item["bytes"]), uid=counter(item["uid"]), mtime=timestamp(item["mtime"]))
                if target == "stale":
                    if item["kind"] not in {"dir", "file"}:
                        raise ValueError("invalid stale row kind")
                    row["kind"] = item["kind"]
            lists[owner][target].append(row)

    captures = {}
    for vid, members in volumes.items():
        if any(r.path not in mounts for r in members):
            continue
        rows = []
        for root in members:
            m = mounts[root.path]
            counts = {k: counter(m["scanned_" + k]) for k in ("bytes", "files", "dirs")}
            counts.update(errors=counter(m["errors"]), vanished_dirs=counter(m["vanished_dirs"]))
            rows.append(dict(root.descriptor(), counts=counts, tree=tree_node(m["tree"]),
                             outcome="partial" if counts["errors"] else "complete", **lists[root.path]))
        outcome = "partial" if any(r["outcome"] == "partial" for r in rows) else "complete"
        captures[vid] = dict(schema="storage-viz.capture/2", run_id=run_id, group=group,
                             volume_id=vid, scan_started_unix=started, scan_finished_unix=finished,
                             outcome=outcome, roots=rows)
    return captures
