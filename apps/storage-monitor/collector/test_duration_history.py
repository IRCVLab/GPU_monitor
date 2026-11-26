import pathlib
import tempfile
import unittest

from collector import duration_history


class DurationHistoryFileTests(unittest.TestCase):
    def test_round_trip_keeps_valid_history(self):
        with tempfile.TemporaryDirectory() as td:
            path = duration_history.path_for(pathlib.Path(td), "hinton")
            history = {"full": [[1000, 900], [2000, 24]], "ssd": [[1500, 18]]}
            duration_history.save(path, history)
            self.assertEqual(duration_history.load(path), history)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["hinton.json"])

    def test_missing_or_malformed_files_read_as_empty(self):
        bad = [
            [], {"weekly": [[1, 1]]}, {"full": [[1, 1]] * 6}, {"full": [[2, 1], [2, 1]]},
            {"full": [[0, 1]]}, {"full": [[1, -1]]}, {"full": [[True, 1]]}, {"full": [[1, 1.5]]}, {"full": [[1]]},
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(duration_history.parse(value), {})
        with tempfile.TemporaryDirectory() as td:
            path = duration_history.path_for(pathlib.Path(td), "hinton")
            self.assertEqual(duration_history.load(path), {})
            path.parent.mkdir()
            path.write_text("{not json")
            self.assertEqual(duration_history.load(path), {})

    def test_server_id_cannot_escape_the_history_directory(self):
        self.assertEqual(duration_history.path_for(pathlib.Path("/tmp"), "_gpu").name, "_gpu.json")
        for server_id in ("../state", "a/b", ""):
            with self.subTest(server_id=server_id), self.assertRaises(ValueError):
                duration_history.path_for(pathlib.Path("/tmp"), server_id)


class DurationHistorySymlinkTests(unittest.TestCase):
    def test_symlinked_history_directory_is_neither_read_nor_written(self):
        with tempfile.TemporaryDirectory() as td:
            root, elsewhere = pathlib.Path(td) / "state", pathlib.Path(td) / "elsewhere"
            root.mkdir(); elsewhere.mkdir()
            (root / "scan-duration-history").symlink_to(elsewhere)
            path = duration_history.path_for(root, "hinton")
            with self.assertRaises(OSError):
                duration_history.save(path, {"full": [[1, 1]]})
            self.assertEqual(list(elsewhere.iterdir()), [])
            (elsewhere / "hinton.json").write_text('{"full": [[1, 900]]}')
            self.assertEqual(duration_history.load(path), {})


if __name__ == "__main__":
    unittest.main()
