"""Durable, append-only session output for Electrode Monitor."""

from __future__ import annotations

import csv
from datetime import datetime
import math
from pathlib import Path
from typing import TextIO


class SessionLog:
    """Create a new CSV and adjacent raw serial log without overwriting files.

    This object belongs to the serial worker thread. All timestamps must carry
    a UTC offset. Each completed sample and each serial chunk is flushed so
    other programs can inspect a session while it is running.
    """

    columns = (
        "timestamp_iso",
        "elapsed_s",
        "millivolts",
        "electrode_input",
        "port",
        "firmware",
        "requested_interval_s",
    )

    def __init__(
        self,
        path: str | Path,
        port: str,
        firmware: str,
        input_name: str,
        interval_s: float,
    ) -> None:
        self.path = Path(path)
        self.traffic_path = self.path.with_suffix(".rs232.log")
        if self.path == self.traffic_path:
            raise ValueError("Choose a CSV filename, not an .rs232.log filename")
        self.port = str(port)
        self.firmware = str(firmware)
        self.input_name = str(input_name)
        self.interval_s = self._finite(interval_s, "interval_s")
        if self.interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self._csv_file: TextIO | None = None
        self._traffic_file: TextIO | None = None
        self._writer = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._csv_file = self.path.open("x", encoding="utf-8", newline="")
            self._traffic_file = self.traffic_path.open("x", encoding="utf-8", newline="")
            self._writer = csv.writer(self._csv_file)
            self._writer.writerow(self.columns)
            self._csv_file.flush()
            self._traffic_file.write(
                "# Electrode Monitor serial traffic; timestamps include UTC offset.\n"
                "# hex is the exact byte stream; text uses Python byte escapes.\n"
            )
            self._traffic_file.flush()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _finite(value: float, name: str) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value

    @staticmethod
    def _timestamp(timestamp: datetime) -> str:
        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return timestamp.isoformat(timespec="microseconds")

    def _ensure_open(self) -> None:
        if self._csv_file is None or self._traffic_file is None:
            raise ValueError("Session log is closed")

    def write_sample(self, timestamp: datetime, elapsed_s: float, mv: float) -> None:
        self._ensure_open()
        stamp = self._timestamp(timestamp)
        elapsed = self._finite(elapsed_s, "elapsed_s")
        if elapsed < 0:
            raise ValueError("elapsed_s must not be negative")
        voltage = self._finite(mv, "mv")
        self._writer.writerow(
            (
                stamp,
                repr(elapsed),
                repr(voltage),
                self.input_name,
                self.port,
                self.firmware,
                repr(self.interval_s),
            )
        )
        self._csv_file.flush()

    def write_traffic(self, timestamp: datetime, direction: str, data: bytes) -> None:
        self._ensure_open()
        stamp = self._timestamp(timestamp)
        if direction not in {"TX", "RX"}:
            raise ValueError("direction must be TX or RX")
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        self._traffic_file.write(
            f"{stamp}\t{direction}\thex={data.hex(' ')}\ttext={data!r}\n"
        )
        self._traffic_file.flush()

    def close(self) -> None:
        """Close both streams, even if flushing one of them fails."""
        csv_file, traffic_file = self._csv_file, self._traffic_file
        self._csv_file = self._traffic_file = None
        self._writer = None
        try:
            if csv_file is not None:
                csv_file.close()
        finally:
            if traffic_file is not None:
                traffic_file.close()

    def __enter__(self) -> SessionLog:
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
