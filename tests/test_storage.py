import csv
import os
import tempfile
import unittest

from crwb.storage import CSV_COLUMNS, StateFile, Storage
from tests.fakes import test_config


class StorageTests(unittest.TestCase):
    def test_csv_header_and_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = test_config()
            cfg["logging"]["directory"] = d
            cfg["logging"]["csv_max_bytes"] = 600
            cfg["logging"]["retention_files"] = 2
            s = Storage(cfg)
            for i in range(12):
                self.assertTrue(s.write_result_row({"cycle_id": f"c{i}", "status": "SUCCESS", "error": "x" * 60}))
            files = sorted(os.listdir(d))
            rotated = [f for f in files if f.startswith("results.") and f != "results.csv"]
            self.assertLessEqual(len(rotated), 2)
            with open(s.csv_path, newline="") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0], CSV_COLUMNS)
            for f in rotated:
                with open(os.path.join(d, f), newline="") as fh:
                    self.assertEqual(next(csv.reader(fh)), CSV_COLUMNS)

    def test_write_error_reported(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = test_config()
            cfg["logging"]["directory"] = os.path.join(d, "nope")
            s = Storage(cfg)
            with open(cfg["logging"]["directory"], "w") as fh:
                fh.write("blocker")
            self.assertFalse(s.write_event("x"))
            self.assertIn("cannot create", s.status.last_error)

    def test_recovery_clears_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = test_config()
            cfg["logging"]["directory"] = d
            s = Storage(cfg)
            s.status.last_error = "old"
            self.assertTrue(s.write_event("ok"))
            self.assertEqual(s.status.last_error, "")

    def test_state_and_control_file(self):
        with tempfile.TemporaryDirectory() as d:
            sf = StateFile(d)
            sf.write({"a": 1})
            self.assertEqual(sf.read()["a"], 1)
            self.assertEqual(sf.pop_control(), "")
            sf.push_control("start")
            self.assertEqual(sf.pop_control(), "start")
            self.assertEqual(sf.pop_control(), "")


if __name__ == "__main__":
    unittest.main()
