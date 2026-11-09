"""Small, serialized RS232 client for a compatible electrode instrument.

Commands follow the documented instrument protocol.
No command in this module starts a titration.
Use this object on one worker thread; it is deliberately not thread safe.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

try:
    import serial
except ImportError:
    serial = None


class ProtocolError(Exception):
    """A communication error or an unexpected instrument response."""


class ResponseTimeout(ProtocolError):
    """The connection was closed because a reply did not finish in time."""


class MeasurementError(ProtocolError):
    """The current instrument state cannot provide a fresh voltage."""


class InstrumentClient:
    TERMINATOR = b"\r\r\n"
    VOLTAGE_INPUTS = frozenset(("1", "2", "Diff."))

    def __init__(
        self,
        port: str = "COM5",
        baudrate: int = 9600,
        callback: Callable[[str, bytes], None] | None = None,
        timeout: float = 2.5,
        *,
        serial_factory=None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.callback = callback
        self.timeout = timeout
        self._serial_factory = serial_factory
        self._serial = None
        self._pending = bytearray()
        self.owns_measurement = False

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def _traffic(self, direction: str, data: bytes) -> None:
        if data and self.callback is not None:
            self.callback(direction, data)

    def _abort(self) -> None:
        """Close without issuing another command after transport failure."""
        port, self._serial = self._serial, None
        self._pending.clear()
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def connect(self) -> None:
        if self.connected:
            return
        factory = self._serial_factory or (serial.Serial if serial else None)
        if factory is None:
            raise ProtocolError(
                "pyserial is missing. Install this package or run pip install -r requirements.txt."
            )
        try:
            # Set handshake lines before opening. HWs requires the PC to leave
            # RTS/DTR asserted; OS-controlled RTS/CTS did not work reliably in
            # the initial hardware connection check.
            port = factory(
                port=None,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0.05,
                write_timeout=2.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self._serial = port
            port.dtr = True
            port.rts = True
            port.port = self.port
            port.open()
            self._pending.clear()
            # Capture any already-buffered data in the passive terminal, then
            # finish a possibly incomplete prior command with an empty line.
            self._drain_until_quiet()
            self._write(b"\r\n")
            self._drain_until_quiet()
        except Exception as exc:
            self._abort()
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError(f"Could not open {self.port}: {exc}") from exc

    def _drain_until_quiet(self) -> None:
        end = time.monotonic() + 1.5
        quiet_since = time.monotonic()
        while time.monotonic() < end:
            chunk = self._serial.read(max(1, self._serial.in_waiting))
            if chunk:
                self._traffic("RX", bytes(chunk))
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 0.2:
                return
        raise ProtocolError("Continuous data on the serial line; reconnect when the instrument is idle.")

    def _write(self, data: bytes) -> None:
        if not self.connected:
            raise ProtocolError("The instrument is disconnected.")
        written = self._serial.write(data)
        self._traffic("TX", data[:written])
        if written != len(data):
            raise ProtocolError("The complete command could not be sent.")

    def query(self, command: str) -> str:
        """Send one command line and return a complete CR CR LF reply.

        A timeout closes the port. This prevents a delayed response being
        silently interpreted as the answer to the next request.
        """
        if not self.connected:
            raise ProtocolError("The instrument is disconnected.")
        if not command or "\r" in command or "\n" in command:
            raise ValueError("Supply a single nonempty command without line endings.")
        try:
            payload = command.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("Instrument commands must be ASCII.") from exc
        if len(payload) > 80:
            raise ValueError("The instrument accepts at most 80 characters per command line.")
        try:
            # Unsolicited application data must never become the next reply.
            if self._pending:
                raise ProtocolError("Unexpected extra reply data; reconnect to resynchronize.")
            if self._serial.in_waiting:
                leftover = self._serial.read(self._serial.in_waiting)
                self._traffic("RX", bytes(leftover))
                if leftover.replace(b"\x11", b"").replace(b"\x13", b"").strip():
                    raise ProtocolError("Unexpected serial data before query; reconnect to resynchronize.")
            self._write(payload + b"\r\n")
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                chunk = self._serial.read(max(1, self._serial.in_waiting))
                if not chunk:
                    continue
                self._traffic("RX", bytes(chunk))
                # These flow-control characters are shown in the terminal but
                # are not part of a scalar response.
                self._pending.extend(chunk.replace(b"\x11", b"").replace(b"\x13", b""))
                if len(self._pending) > 65536:
                    raise ProtocolError("Instrument reply exceeded the expected size.")
                position = self._pending.find(self.TERMINATOR)
                if position >= 0:
                    frame = bytes(self._pending[:position])
                    del self._pending[: position + len(self.TERMINATOR)]
                    if self._pending.strip():
                        raise ProtocolError("More than one response arrived for a single query.")
                    self._pending.clear()
                    response = frame.decode("ascii", errors="strict").strip()
                    if re.search(r"(?:^|;)E\d+(?:;|$)", response):
                        raise ProtocolError(f"Instrument reported an error: {response}")
                    return response
            raise ResponseTimeout(
                f"No complete response from {self.port} within {self.timeout:g} seconds. "
                "Connection closed; reconnect before retrying."
            )
        except Exception as exc:
            self._abort()
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError(f"Serial communication failed: {exc}") from exc

    @staticmethod
    def _scalar(response: str) -> str:
        value = response.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value.strip()

    def identify(self) -> str:
        result = self._scalar(self.query("&Config.Aux.Prog $Q"))
        if not re.fullmatch(r"716\.\d+", result):
            raise ProtocolError(f"Unsupported instrument firmware: {result!r}.")
        return result

    def status(self) -> str:
        return self.query("$D")

    def measurement_status(self) -> str:
        result = self._scalar(self.query("&Assembly.Meas.Status $Q"))
        if result not in ("ON", "OFF"):
            raise ProtocolError(f"Unexpected measurement status: {result!r}.")
        return result

    def measurement_input(self) -> str:
        return self._scalar(self.query("&Assembly.Meas.MeasInput $Q"))

    def _require_voltage_input(self) -> str:
        channel = self.measurement_input()
        if channel not in self.VOLTAGE_INPUTS:
            raise MeasurementError(
                f"Input {channel!r} is not a potentiometric voltage input. "
                "Choose input 1, 2 or Diff. on the instrument before monitoring mV."
            )
        return channel

    def start_measurement(self) -> bool:
        """Enable only assembly measurement; return whether this client owns it.

        An already-running assembly measurement is left running on disconnect.
        A running titration is never interrupted.
        """
        state = self.status()
        if not (state.startswith("$R.") and state.endswith(".Inac")):
            raise MeasurementError(f"The instrument is not idle ({state}); measurement was not started.")
        self._require_voltage_input()
        if self.measurement_status() == "ON":
            return self.owns_measurement
        # Ownership is recorded before writing: if the cable fails after the
        # setter, the application must tell the user it may still be measuring.
        self.owns_measurement = True
        response = self._scalar(
            self.query('&Assembly.Meas.Status "ON";&Assembly.Meas.Status $Q')
        )
        if response != "ON":
            if response == "OFF":
                self.owns_measurement = False
            raise MeasurementError(f"Measurement did not confirm ON: {response!r}.")
        return True

    def stop_measurement(self) -> bool:
        """Stop only an assembly measurement this client enabled."""
        if not self.owns_measurement:
            return False
        response = self._scalar(
            self.query('&Assembly.Meas.Status "OFF";&Assembly.Meas.Status $Q')
        )
        if response != "OFF":
            raise MeasurementError(f"Measurement did not confirm OFF: {response!r}.")
        self.owns_measurement = False
        return True

    def read_mv(self, expected_input: str | None = None) -> float:
        """Return fresh raw assembly voltage in mV, never pH or cached OFF data."""
        if self.measurement_status() != "ON":
            raise MeasurementError("Assembly measurement is OFF; no fresh mV reading is available.")
        channel = self._require_voltage_input()
        if expected_input is not None and channel != expected_input:
            raise MeasurementError(
                f"Electrode input changed from {expected_input!r} to {channel!r}. "
                "Stop and start a new monitoring session to record the correct input."
            )
        value = self._scalar(self.query("&Info.ActualInfo.Assembly.Meas $Q"))
        # Require an entire finite numeric scalar. An error, status, overload
        # marker or a different physical unit cannot be logged as a voltage.
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:mV)?", value)
        if not match:
            raise MeasurementError(f"Invalid electrode voltage response: {value!r}.")
        result = float(match.group(1))
        if not (-float("inf") < result < float("inf")):
            raise MeasurementError(f"Nonfinite electrode voltage response: {value!r}.")
        return result

    def close(self) -> None:
        """Try to stop an owned measurement, then always release the COM port."""
        try:
            if self.owns_measurement:
                if not self.connected:
                    raise ProtocolError(
                        "Connection lost while assembly measurement was enabled. "
                        "It may still be ON; check the instrument."
                    )
                self.stop_measurement()
        finally:
            self._abort()
