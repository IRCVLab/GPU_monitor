"""Exercise the checked-in fixture publications through the production parsers."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.fixtures import FixtureFleet
from collector.inventory import CapacityThresholds
from collector.view import server_bytes
from tools.gen_sample import SNAPSHOTS, write_server

DATA_DIR = Path(__file__).parent / "fixtures/fleet"


class FixtureTest(unittest.TestCase):
    def test_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for _, factory in SNAPSHOTS:
                write_server(directory, factory())
            for path in directory.iterdir():
                self.assertEqual(path.read_bytes(), (DATA_DIR / path.name).read_bytes())

    def test_hinton_unreadable_directories(self):
        fleet = FixtureFleet(DATA_DIR)
        view = fleet.view("hinton")
        detail = json.loads(server_bytes(view, fleet.clock.monotonic(), CapacityThresholds(), detail=True))
        expected = {"/home": "/home/jusung", "/data": "/data/private_collab"}
        reported = []
        for volume in detail["volumes"]:
            vid = volume["volume_id"]
            raw = fleet.read_capture("hinton", vid, volume["capture"]["sha256"])
            capture = json.loads(gzip.decompress(raw))
            for root in capture["roots"]:
                path = expected.get(root["scan_root"])
                self.assertEqual(root["counts"]["errors"], int(path is not None))
                if path:
                    self.assertEqual(root["outcome"], "partial")
                    self.assertEqual(volume["capture"]["outcome"], "partial")
                    self.assertEqual(root["errors"], [{"path": path, "reason": "EACCES"}])
                    reported.append({**root["errors"][0], "volume_id": vid, "binding": root["binding"]})
        self.assertEqual(len(reported), 2)
        self.assertCountEqual(detail["errors"], reported)

    def test_manifest_cases(self):
        fleet = FixtureFleet(DATA_DIR)
        volumes = [v for view in fleet.views() for v in view.data.manifest.volumes]
        self.assertTrue(any(len(v.roots) > 1 for v in volumes))
        self.assertTrue(any(v.identity == "ambiguous" for v in volumes))
        self.assertTrue(any(v.last_attempt and v.last_attempt.outcome == "failed" and v.capture for v in volumes))
        self.assertTrue(any(v.capacity.status == "failed" for v in volumes))


if __name__ == "__main__":
    unittest.main()
