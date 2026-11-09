"""Explicit live test: read five mV samples and stop only measurement we start.

Run from the app folder: py -3 tests/hardware_check.py
Never included in unit-test discovery. Requires an idle instrument.
"""
import argparse
import sys
from pathlib import Path
import queue
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from electrode_monitor import InstrumentWorker, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5", help="Serial device, e.g. COM5 or /dev/ttyUSB0")
    args = parser.parse_args()
    events = queue.Queue()
    worker = InstrumentWorker(events)
    worker.start()
    samples = []
    path = ROOT / "logs" / f"hardware_check_{now():%Y%m%d_%H%M%S}.csv"
    stage = "connect"
    worker.commands.put(("connect", {"port": args.port}))
    deadline = time.monotonic() + 35
    try:
        while time.monotonic() < deadline:
            kind, data = events.get(timeout=5)
            if kind == "traffic":
                print(data["direction"], repr(data["data"]))
            elif kind == "error":
                raise RuntimeError(data["message"])
            else:
                print(kind, data)
            if kind == "ready":
                if stage == "connect":
                    worker.commands.put(("start", {"interval": 1.0}))
                    stage = "start"
                elif stage == "start":
                    worker.commands.put(("log_start", {"path": path}))
                    stage = "record"
            if kind == "sample":
                samples.append(data["mv"])
                if len(samples) == 5:
                    break
        if len(samples) != 5:
            raise RuntimeError(f"Only received {len(samples)} of 5 samples")
    finally:
        worker.commands.put(("quit", {}))
        worker.join(timeout=12)
        if worker.is_alive():
            raise RuntimeError("Serial worker failed to exit")
        errors = []
        while not events.empty():
            kind, data = events.get_nowait()
            print(kind, data)
            if kind == "error":
                errors.append(data["message"])
        if errors:
            raise RuntimeError("; ".join(errors))
    print("PASS: five live mV samples, CSV/raw logs, clean stop and released port", path)


if __name__ == "__main__":
    main()
