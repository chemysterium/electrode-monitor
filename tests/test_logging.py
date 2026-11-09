import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from session_log import SessionLog


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "reading.csv"
        self.stamp = datetime(2026, 11, 9, 15, 30, 1, 123456,
                              tzinfo=timezone(timedelta(hours=1)))

    def make_log(self):
        log = SessionLog(self.path, "COM_TEST", "test-firmware", "1", 1)
        self.addCleanup(log.close)
        return log

    def test_live_csv_is_flushed_with_units_metadata_and_offset(self):
        log = self.make_log()
        log.write_sample(self.stamp, 1.25, -123.4)
        with self.path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "timestamp_iso": "2026-11-09T15:30:01.123456+01:00",
            "elapsed_s": "1.25",
            "millivolts": "-123.4",
            "electrode_input": "1",
            "port": "COM_TEST",
            "firmware": "test-firmware",
            "requested_interval_s": "1.0",
        })

    def test_raw_log_preserves_all_bytes_without_line_injection(self):
        log = self.make_log()
        raw = b'"-123.4"\r\r\n\x11\x13\x00\xff\\'
        log.write_traffic(self.stamp, "RX", raw)
        text = log.traffic_path.read_text(encoding="utf-8")
        records = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(len(records), 1)
        self.assertIn("\tRX\t", records[0])
        self.assertIn("hex=" + raw.hex(" "), records[0])
        self.assertIn("text=" + repr(raw), records[0])
        self.assertIn("+01:00", records[0])

    def test_csv_collision_never_overwrites_existing_data(self):
        self.path.write_text("valuable measurements", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.make_log()
        self.assertEqual(self.path.read_text(), "valuable measurements")
        self.assertFalse(self.path.with_suffix(".rs232.log").exists())

    def test_raw_collision_closes_new_csv_preserves_existing_log(self):
        raw_path = self.path.with_suffix(".rs232.log")
        raw_path.write_text("valuable traffic", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.make_log()
        self.assertEqual(raw_path.read_text(), "valuable traffic")
        # Reopening for writing also verifies no leaked Windows file handle.
        with self.path.open("a", encoding="utf-8") as file:
            file.write("closed")
        self.assertEqual(self.path.read_text(), "closed")

    def test_invalid_values_do_not_create_fake_samples(self):
        log = self.make_log()
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    log.write_sample(self.stamp, 0, value)
        with self.assertRaises(ValueError):
            log.write_sample(datetime(2026, 11, 9), 0, 5)
        with self.assertRaises(ValueError):
            log.write_sample(self.stamp, -1, 5)
        with self.path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.DictReader(file)), [])

    def test_close_is_idempotent_and_writes_after_close_fail(self):
        log = self.make_log()
        log.close()
        log.close()
        with self.assertRaises(ValueError):
            log.write_sample(self.stamp, 0, 5)
        with self.assertRaises(ValueError):
            log.write_traffic(self.stamp, "RX", b"test")

    def test_invalid_configuration_does_not_create_output(self):
        for interval in (0, -1, float("nan"), float("inf")):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    SessionLog(self.path, "COM_TEST", "test-firmware", "1", interval)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
