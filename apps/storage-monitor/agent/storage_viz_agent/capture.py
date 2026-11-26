"""Translate scanner rows to per-volume captures at the publication boundary."""
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


def tree_node(node, depth=0):
    if depth > 128 or not isinstance(node["name"], str) or node["kind"] != "directory":
        raise ValueError("invalid scanner tree")
    result = {key: counter(node[key]) for key in ("bytes", "files", "uid", "other_bytes")}
    result.update(name=node["name"], kind="dir", mtime=int(node["mtime"]),
                  children=[tree_node(n, depth + 1) for n in node.get("children", [])])
    if "name_b64" in node:
        result["name_b64"] = node["name_b64"]
    return result


def split_group_output(raw, volumes, run_id, group, started, finished):
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
            if len(path.encode()) > 12288 or not path.startswith("/"):
                raise ValueError("invalid scanner path")
            owner = max((r for r in roots if r == path or path.startswith(r + "/")), key=len)
            row = {"path": path}
            if "path_b64" in item:
                row["path_b64"] = item["path_b64"]
            if target == "errors":
                row["reason"] = item["reason"]
            else:
                row.update(bytes=counter(item["bytes"]), uid=counter(item["uid"]), mtime=int(item["mtime"]))
                if target == "stale":
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
