"""Translate scanner rows to per-volume captures at the publication boundary."""
import base64
import json
import logging
import os


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


def text(value, limit):
    if not isinstance(value, str) or not value or len(value) > limit or "\0" in value:
        raise ValueError("invalid scanner string")
    return value


def rows(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("invalid scanner array")
    return value


def timestamp(value):
    return min(4102444800, max(0, value)) if type(value) is int else 0


def tree_node(node, depth=0):
    if depth > 128 or not isinstance(node, dict) or node["kind"] != "directory":
        raise ValueError("invalid scanner tree")
    result = {key: counter(node[key]) for key in ("bytes", "files", "uid", "other_bytes")}
    result.update(name=text(node["name"], 4096), kind="dir", mtime=timestamp(node["mtime"]),
                  children=[tree_node(n, depth + 1) for n in rows(node.get("children", []), 16384)])
    children = result["children"]
    if (children or result["other_bytes"]) and result["bytes"] != result["other_bytes"] + sum(c["bytes"] for c in children):
        raise ValueError("tree byte rollup mismatch")
    if result["files"] < sum(c["files"] for c in children):
        raise ValueError("tree file rollup mismatch")
    if "name_b64" in node:
        result["name_b64"] = text(node["name_b64"], 8192)
        base64.b64decode(result["name_b64"], validate=True)
    return result


def raw_path(row):
    if "path_b64" in row:
        return base64.b64decode(row["path_b64"], validate=True)
    return os.fsencode(row["path"])


def file_row(item, target):
    path = text(item["path"], 4096)
    if not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid scanner path")
    row = {"path": path}
    if "path_b64" in item:
        row["path_b64"] = item["path_b64"]
    if target == "errors":
        # Capture/2 has five wire codes. Preserve precise errno in scanner
        # output and classify access/read and resource failures at this boundary.
        reason = item["reason"]
        if reason in {"EMFILE", "ENFILE", "ENOMEM"}:
            reason = "FD_BUDGET_REOPEN_FAILED"
        if reason not in {"VANISHED", "REPLACED", "FD_BUDGET_REOPEN_FAILED", "UNREPRESENTABLE"}:
            reason = "EACCES"
        row["reason"] = reason
    else:
        row.update(bytes=counter(item["bytes"]), uid=counter(item["uid"]), mtime=timestamp(item["mtime"]))
        if target == "stale":
            row["kind"] = item["kind"] if item["kind"] in {"dir", "file"} else "file"
    return row


BAD_ROW = (ValueError, KeyError, TypeError, AttributeError, RecursionError)


def optional_rows(raw, key):
    value = raw.get(key, [])
    if not isinstance(value, list):
        logging.warning("Skipping invalid scanner %s array", key)
        return ()
    return value


def split_group_output(raw, volumes, run_id, group, started, finished):
    mounts = {}
    for mount in rows(raw["mounts"], 16384):
        try:
            mounts[raw_path(mount)] = mount
        except BAD_ROW as exc:
            logging.warning("Ignoring unidentifiable scanner mount: %s", exc)
    roots = {os.fsencode(r.path) for members in volumes.values() for r in members}
    lists = {p: dict(users=[], stale=[], top_files=[], errors=[]) for p in roots}
    for user in optional_rows(raw, "users"):
        try:
            uid = counter(user["uid"])
            name = user.get("name")
            if not isinstance(name, str) or not name or len(name) > 128 or "\0" in name:
                name = str(uid)
            usage_rows = user.get("root_usage")
            if usage_rows is None:
                usage_rows = [dict(path=p, bytes=size, files=user["files_by_mount"][p])
                              for p, size in user["by_mount"].items()]
            for usage in usage_rows:
                try:
                    lists[raw_path(usage)]["users"].append(dict(uid=uid, name=name, bytes=counter(usage["bytes"]),
                                                               files=counter(usage["files"])))
                except BAD_ROW as exc:
                    logging.warning("Skipping malformed scanner ownership row: %s", exc)
        except BAD_ROW as exc:
            logging.warning("Skipping malformed scanner user row: %s", exc)
    for source, target in (("top_files", "top_files"), ("stale", "stale"), ("blocked", "errors")):
        for item in optional_rows(raw, source):
            try:
                path = raw_path(item)
                if len(path) > 4096:
                    raise ValueError("oversized scanner path")
                owner = max((r for r in roots if r == path or path.startswith(r + b"/")), key=len)
                lists[owner][target].append(file_row(item, target))
            except BAD_ROW as exc:
                logging.warning("Skipping malformed scanner %s row: %s", source, exc)

    captures = {}
    for vid, members in volumes.items():
        if any(os.fsencode(r.path) not in mounts for r in members):
            continue
        root_rows = []
        for root in members:
            path = os.fsencode(root.path)
            m = mounts[path]
            counts = {}
            incomplete = False
            for key in ("bytes", "files", "dirs", "errors", "vanished_dirs"):
                field = "scanned_" + key if key in ("bytes", "files", "dirs") else key
                try:
                    counts[key] = counter(m[field])
                except BAD_ROW:
                    counts[key] = 0
                    incomplete = True
            try:
                tree = tree_node(m["tree"])
                if tree["bytes"] != counts["bytes"] or tree["files"] != counts["files"]:
                    counts.update(bytes=tree["bytes"], files=tree["files"])
                    incomplete = True
            except BAD_ROW as exc:
                logging.warning("Folding malformed scanner tree for %r: %s", root.path, exc)
                tree = dict(name=root.descriptor()["scan_root"], kind="dir", bytes=counts["bytes"], files=counts["files"],
                            uid=0, mtime=0, other_bytes=counts["bytes"], children=[])
                incomplete = True
            if incomplete:
                counts["errors"] += 1
                lists[path]["errors"].append(dict(path=root.descriptor()["scan_root"], reason="UNREPRESENTABLE"))
            root_rows.append(dict(root.descriptor(), counts=counts, tree=tree,
                                  outcome="partial" if counts["errors"] else "complete", **lists[path]))
        outcome = "partial" if any(r["outcome"] == "partial" for r in root_rows) else "complete"
        captures[vid] = dict(schema="storage-viz.capture/2", run_id=run_id, group=group,
                             volume_id=vid, scan_started_unix=started, scan_finished_unix=max(started, finished),
                             outcome=outcome, roots=root_rows)
    return captures


def relabel(captures, prefixes):
    """Rename host paths in published captures to the paths users see (display_prefixes)."""
    if not prefixes:
        return captures
    from agent.storage_viz_agent.identity import shown
    byte_prefixes = {os.fsencode(k): os.fsencode(v) for k, v in prefixes.items()}

    def raw(path_bytes):
        for real in sorted(byte_prefixes, key=len, reverse=True):
            if path_bytes == real or path_bytes.startswith(real + b"/"):
                return byte_prefixes[real] + path_bytes[len(real):]
        return path_bytes

    for cap in captures.values():
        for root in cap["roots"]:
            if root["tree"].get("name", "").startswith("/"):
                root["tree"]["name"] = shown(root["tree"]["name"], prefixes)
            for key in ("top_files", "stale", "errors"):
                for row in root.get(key, []):
                    row["path"] = shown(row["path"], prefixes)
                    if "path_b64" in row:
                        row["path_b64"] = base64.b64encode(raw(base64.b64decode(row["path_b64"]))).decode("ascii")
    return captures
