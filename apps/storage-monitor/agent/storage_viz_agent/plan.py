"""Pure cadence and partition decisions."""
from collections import defaultdict

CADENCE = {"fast": 3600, "slow": 10800}
SLACK = 60


def validate_partition(roots):
    volumes = defaultdict(list)
    for root in sorted(roots, key=lambda r: r.path):
        volumes[root.volume_id].append(root)
    groups = defaultdict(dict)
    for vid, members in volumes.items():
        if members[0].identity == "ambiguous":
            group = vid
        elif all(r.media == "ssd" and r.media_confidence == "resolved" for r in members):
            group = "fast"
        else:
            group = "slow"
        groups[group][vid] = members
    return dict(sorted(groups.items(), key=lambda item: (item[0] != "fast", item[0])))


def due_groups(manifest, roots, now, trigger, requests):
    previous = {v["volume_id"]: v for v in manifest.get("volumes", [])}

    def due(group, vid, members):
        old = previous.get(vid, {})
        return (group == vid or not old.get("capture")
                or old.get("roots") != [r.descriptor() for r in members]
                or now >= old["capture"]["scan_finished_unix"] + CADENCE[group] - SLACK)

    return {g: vs for g, vs in validate_partition(roots).items()
            if trigger == "manual" or requests or any(due(g, v, rs) for v, rs in vs.items())}
