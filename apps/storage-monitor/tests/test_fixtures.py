"""Exercise the checked-in fixture publications through the production parsers."""
from pathlib import Path
import tempfile
import unittest

from api.fixtures import FixtureFleet
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

    def test_manifest_cases(self):
        fleet = FixtureFleet(DATA_DIR)
        volumes = [v for view in fleet.views() for v in view.data.manifest.volumes]
        self.assertTrue(any(len(v.roots) > 1 for v in volumes))
        self.assertTrue(any(v.identity == "ambiguous" for v in volumes))
        self.assertTrue(any(v.last_attempt and v.last_attempt.outcome == "failed" and v.capture for v in volumes))
        self.assertTrue(any(v.capacity.status == "failed" for v in volumes))
