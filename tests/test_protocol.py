"""Protocol behavior tests; no instrument access."""

import unittest

from serial_protocol import InstrumentClient, MeasurementError, ProtocolError, ResponseTimeout


class FakeSerial:
    def __init__(self, replies):
        self.replies = replies
        self.chunks = []
        self.writes = []
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def write(self, data):
        self.writes.append(data)
        self.chunks.extend(self.replies.get(data, []))
        return len(data)

    def read(self, count):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.is_open = False


def client(replies, callback=None):
    instrument = InstrumentClient(callback=callback, timeout=0.01)
    port = FakeSerial(replies)
    instrument._serial = port
    return instrument, port


class ProtocolTests(unittest.TestCase):
    def test_fragmented_response_and_passive_raw_traffic(self):
        traffic = []
        instrument, _ = client({b"&Config.Aux.Prog $Q\r\n": [b'\x13"716.', b'0022"\r', b'\r\n\x11']}, lambda d, b: traffic.append((d, b)))
        self.assertEqual(instrument.identify(), "716.0022")
        self.assertEqual(traffic[0], ("TX", b"&Config.Aux.Prog $Q\r\n"))
        self.assertIn(b"\x13", b"".join(data for direction, data in traffic if direction == "RX"))

    def test_timeout_closes_to_prevent_stale_response_reuse(self):
        instrument, port = client({b"$D\r\n": [b"$R.Mode.SET.Inac\r"]})
        with self.assertRaises(ResponseTimeout):
            instrument.status()
        self.assertFalse(port.is_open)
        with self.assertRaisesRegex(ProtocolError, "disconnected"):
            instrument.status()
        self.assertEqual(len(port.writes), 1)

    def test_unsolicited_data_cannot_be_misattributed(self):
        instrument, port = client({})
        port.chunks = [b'"123.4"\r\r\n']
        with self.assertRaisesRegex(ProtocolError, "Unexpected serial data"):
            instrument.status()
        self.assertEqual(port.writes, [])

    def test_fresh_mv_is_read_only_from_voltage_input(self):
        instrument, port = client({
            b"&Assembly.Meas.Status $Q\r\n": [b'"ON"\r\r\n'],
            b"&Assembly.Meas.MeasInput $Q\r\n": [b'"1"\r\r\n'],
            b"&Info.ActualInfo.Assembly.Meas $Q\r\n": [b'"-162.3"\r\r\n'],
        })
        self.assertEqual(instrument.read_mv(), -162.3)
        self.assertTrue(all(b"$Q" in item for item in port.writes))

    def test_inactive_cached_voltage_is_rejected(self):
        instrument, port = client({b"&Assembly.Meas.Status $Q\r\n": [b'"OFF"\r\r\n']})
        with self.assertRaises(MeasurementError):
            instrument.read_mv()
        self.assertEqual(len(port.writes), 1)

    def test_changed_input_is_not_logged_under_previous_input(self):
        instrument, port = client({
            b"&Assembly.Meas.Status $Q\r\n": [b'"ON"\r\r\n'],
            b"&Assembly.Meas.MeasInput $Q\r\n": [b'"2"\r\r\n'],
        })
        with self.assertRaisesRegex(MeasurementError, "changed from '1' to '2'"):
            instrument.read_mv(expected_input="1")
        self.assertEqual(len(port.writes), 2)

    def test_nonvoltage_input_is_rejected(self):
        for channel in ("Temp", "Upol", "Ipol", "unknown"):
            with self.subTest(channel=channel):
                instrument, port = client({
                    b"&Assembly.Meas.Status $Q\r\n": [b'"ON"\r\r\n'],
                    b"&Assembly.Meas.MeasInput $Q\r\n": [f'"{channel}"\r\r\n'.encode()],
                })
                with self.assertRaises(MeasurementError):
                    instrument.read_mv()
                self.assertEqual(len(port.writes), 2)

    def test_invalid_values_are_not_logged_as_zero(self):
        for value in ("overload", "NaN", "1e999", "7.0 pH", "$R.Mode.SET.Inac"):
            with self.subTest(value=value):
                instrument, _ = client({
                    b"&Assembly.Meas.Status $Q\r\n": [b'"ON"\r\r\n'],
                    b"&Assembly.Meas.MeasInput $Q\r\n": [b'"1"\r\r\n'],
                    b"&Info.ActualInfo.Assembly.Meas $Q\r\n": [f'"{value}"\r\r\n'.encode()],
                })
                with self.assertRaises(MeasurementError):
                    instrument.read_mv()

    def test_does_not_start_during_titration(self):
        instrument, port = client({b"$D\r\n": [b"$S.Mode.SET\r\r\n"]})
        with self.assertRaises(MeasurementError):
            instrument.start_measurement()
        self.assertEqual(port.writes, [b"$D\r\n"])

    def test_existing_measurement_is_not_owned_or_stopped(self):
        instrument, port = client({
            b"$D\r\n": [b"$R.Mode.SET.Inac\r\r\n"],
            b"&Assembly.Meas.MeasInput $Q\r\n": [b'"1"\r\r\n'],
            b"&Assembly.Meas.Status $Q\r\n": [b'"ON"\r\r\n'],
        })
        self.assertFalse(instrument.start_measurement())
        instrument.close()
        self.assertFalse(port.is_open)
        self.assertFalse(any(b'"OFF"' in data for data in port.writes))

    def test_owned_measurement_stops_on_close(self):
        instrument, port = client({
            b"$D\r\n": [b"$R.Mode.SET.Inac\r\r\n"],
            b"&Assembly.Meas.MeasInput $Q\r\n": [b'"1"\r\r\n'],
            b"&Assembly.Meas.Status $Q\r\n": [b'"OFF"\r\r\n'],
            b'&Assembly.Meas.Status "ON";&Assembly.Meas.Status $Q\r\n': [b'"ON"\r\r\n'],
            b'&Assembly.Meas.Status "OFF";&Assembly.Meas.Status $Q\r\n': [b'"OFF"\r\r\n'],
        })
        self.assertTrue(instrument.start_measurement())
        self.assertTrue(instrument.owns_measurement)
        instrument.close()
        self.assertFalse(instrument.owns_measurement)
        self.assertFalse(port.is_open)
        self.assertIn(b'&Assembly.Meas.Status "OFF";&Assembly.Meas.Status $Q\r\n', port.writes)

    def test_errors_are_not_treated_as_success(self):
        instrument, port = client({b"$D\r\n": [b"$S.Mode.MET;E20\r\r\n"]})
        with self.assertRaisesRegex(ProtocolError, "E20"):
            instrument.status()
        self.assertFalse(port.is_open)

    def test_duplicate_frames_are_not_silently_accepted(self):
        instrument, _ = client({b"$D\r\n": [b'"OLD"\r\r\n"NEW"\r\r\n']})
        with self.assertRaisesRegex(ProtocolError, "More than one response"):
            instrument.status()


if __name__ == "__main__":
    unittest.main()
