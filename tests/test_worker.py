"""Worker lifecycle tests; all instrument access is mocked."""

import queue
import time
import unittest
from unittest.mock import Mock, patch

from electrode_monitor import InstrumentWorker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.worker = InstrumentWorker(self.events)
        self.device = Mock()
        self.device.identify.return_value = "716.0022"
        self.device.measurement_input.return_value = "1"
        self.device.status.return_value = "$R.Mode.SET.Inac"
        self.device.start_measurement.return_value = True
        self.device.read_mv.return_value = -51.2
        self.recorded = []
        self.factory = patch("electrode_monitor.InstrumentClient", return_value=self.device)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.addCleanup(self.finish_worker)

    def finish_worker(self):
        if self.worker.is_alive():
            self.worker.commands.put(("quit", {}))
            self.worker.join(timeout=3)
            self.assertFalse(self.worker.is_alive(), "Worker did not close promptly")

    def drain_events(self):
        while True:
            try:
                self.recorded.append(self.events.get_nowait())
            except queue.Empty:
                return

    def wait_event(self, kind, predicate=lambda values: True):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty:
                break
            self.recorded.append(event)
            if event[0] == kind and predicate(event[1]):
                return event[1]
        self.fail(f"Did not receive expected {kind} event; received {self.recorded!r}")

    def connect(self):
        self.worker.handle("connect", {"port": "COM5"})

    def start_monitor(self):
        self.connect()
        self.worker.handle("start", {"interval": 0.5})

    def test_stop_failure_still_closes_device_and_log(self):
        self.start_monitor()
        logger = self.worker.logger = Mock()
        self.device.stop_measurement.side_effect = OSError("cable lost")
        self.worker.disconnect()
        self.device.close.assert_called_once_with()
        logger.close.assert_called_once_with()
        self.assertIsNone(self.worker.device)
        self.assertIsNone(self.worker.logger)
        self.assertFalse(self.worker.monitoring)
        self.drain_events()
        self.assertTrue(any(kind == "error" and "Could not verify" in v["message"]
                            for kind, v in self.recorded))
        self.assertIn(("connection", {"connected": False}), self.recorded)

    def test_traffic_log_failure_preserves_live_monitoring_and_bytes(self):
        self.start_monitor()
        logger = self.worker.logger = Mock()
        logger.write_traffic.side_effect = OSError("disk full")
        data = b'"-51.2"\r\r\n'
        self.worker.traffic("RX", data)
        self.assertTrue(self.worker.monitoring)
        self.assertIs(self.worker.device, self.device)
        self.assertIsNone(self.worker.logger)
        logger.close.assert_called_once_with()
        self.drain_events()
        self.assertTrue(any(kind == "traffic" and v["data"] == data
                            for kind, v in self.recorded))
        self.assertTrue(any(kind == "error" and "File logging stopped" in v["message"]
                            for kind, v in self.recorded))
        self.worker.disconnect()

    def test_invalid_read_is_never_emitted_or_logged_as_sample(self):
        self.start_monitor()
        logger = self.worker.logger = Mock()
        self.device.read_mv.side_effect = ValueError("measurement is OFF")
        self.worker.next_poll = 0
        self.worker.start()
        self.wait_event("connection", lambda v: not v["connected"])
        logger.write_sample.assert_not_called()
        logger.close.assert_called_once_with()
        self.assertFalse(any(kind == "sample" for kind, _ in self.recorded))
        self.device.close.assert_called_once_with()

    def test_valid_sample_is_logged_once_before_later_error(self):
        self.start_monitor()
        logger = self.worker.logger = Mock()
        self.device.read_mv.side_effect = [-51.2, ValueError("input changed")]
        self.worker.next_poll = 0
        self.worker.start()
        self.wait_event("connection", lambda v: not v["connected"])
        samples = [values for kind, values in self.recorded if kind == "sample"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["mv"], -51.2)
        logger.write_sample.assert_called_once_with(
            samples[0]["timestamp"], samples[0]["elapsed"], -51.2
        )
        self.assertIsNotNone(samples[0]["timestamp"].utcoffset())
        self.assertGreaterEqual(samples[0]["elapsed"], 0)
        logger.close.assert_called_once_with()

    def test_quit_does_not_take_an_additional_due_sample(self):
        self.start_monitor()
        self.worker.next_poll = 0
        self.worker.commands.put(("quit", {}))
        self.worker.start()
        self.wait_event("closed")
        self.worker.join(timeout=1)
        self.device.read_mv.assert_not_called()
        self.device.close.assert_called_once_with()

    def test_log_start_after_disconnect_cannot_use_stale_session(self):
        self.start_monitor()
        self.worker.disconnect()
        with patch("electrode_monitor.SessionLog") as factory:
            with self.assertRaises(ValueError):
                self.worker.handle("log_start", {"path": "unused.csv"})
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
