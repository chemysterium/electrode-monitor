# Electrode Monitor

A local desktop app for reading electrode potential in **mV**, recording timestamped CSV files, and observing the application's RS232 communication.

The interface uses a neutral name and an original geometric waveform icon. It contains no manufacturer logos or product branding. This is an independent application, not an official instrument utility.

## Features

- Live voltage display and a graph of the latest 600 samples.
- Polling interval from 0.5 to 3600 seconds.
- CSV with timezone-aware timestamps, elapsed seconds, input, port, and firmware metadata.
- Passive TX/RX pane and an accompanying exact-byte serial log.
- Start/stop voltage measurement without starting titration or dosing.
- Checks for inactive measurement, changed input, invalid values, and incomplete responses.
- Shutdown stops only measurements started by this app and releases the port.

## Compatibility

Python 3.10 or newer with Tkinter is required. Windows hardware operation has been tested. The same code supports Linux APIs, but Linux hardware operation has not yet been verified. The serial client implements the documented command tree identified by firmware **716.x**. It is not a universal serial driver. Communication was verified on Windows with firmware **716.0022**, a USB-to-RS232 adapter, and potentiometric input 1.

See [protocol reference](docs/PROTOCOL.md) for source product information and compatibility limits. No manufacturer manuals or artwork are bundled.

## Install and launch on Windows

1. Install Python 3.10 or newer with Tkinter and the Python launcher.
2. Download or clone this repository into a writable folder.
3. Double-click **Setup.cmd**. It creates `.venv` and downloads the declared dependency; internet access is needed for setup.
4. Double-click **Start Electrode Monitor.cmd**.

Alternatively, inside the repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m electrode_monitor
```

A built wheel can be installed with `python -m pip install path/to/electrode_monitor-0.1.0-py3-none-any.whl`; run the installed `electrode-monitor` launcher afterward. This release is Python software, not a standalone executable.

## Install and launch on Linux

A graphical desktop, Tkinter, and serial-port access are required. For Debian/Ubuntu:

```bash
sudo apt install python3-tk python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m electrode_monitor
```

Select the adapter's port, usually `/dev/ttyUSB0` or `/dev/ttyACM0`, from the port list. On Debian/Ubuntu, if access is denied, add your account to the serial-access group with `sudo usermod -aG dialout "$USER"`, then log out and back in. Group names and package names can differ on other distributions. Do not run the whole GUI as root.

The Windows `.cmd` files are not used on Linux. Linux CI exercises offline tests and GUI startup in a virtual display; it cannot verify the physical cable or electrode.

## Use

1. Set the idle instrument to **9600 baud / 8 data bits / no parity / 1 stop bit / hardware handshake**, with remote serial control enabled. Close other programs using its port.
2. Select the port and click **Connect**. On Windows, COM5 is the initial suggestion. On Linux, select the actual adapter device from the port list.
3. Choose the read interval and click **Start mV monitor**. The existing input is preserved. Inputs 1, 2 and Diff. support potentiometric voltage; other input types are rejected.
4. Click **Start CSV log…** and choose a new file. Recording begins then, not retroactively. The default folder is `Electrode Monitor Logs` in your user home folder.
5. Click **Stop** to stop monitoring and recording, or **Disconnect** to release the port. Closing the window also performs cleanup.

Each sample is flushed immediately. A matching `.rs232.log` records transmitted and received bytes during CSV recording. Existing files are never overwritten. Timestamps record response receipt; the requested interval is not a guarantee of the instrument's internal update rate.

Separate measurement blocks instrument method starts. Stop monitoring before using a method. If measurement was already ON when the app attached, Stop leaves it ON. After communication loss, the app cannot confirm measurement shutdown and tells you to check the instrument. Displayed values come directly from the instrument; the app does not calibrate electrodes.

## Serial traffic pane

The observer displays actual TX/RX chunks with visible CR, LF, XON and XOFF markers. Replies may span multiple chunks. There is no manual command-entry field; the observer itself sends no extra commands.

It observes only this app's connection, not other programs' traffic. COM ports are exclusive. Visible history is bounded; **Save traffic…** exports the last 5,000 chunks. Use CSV recording for a complete raw record during a recording period.

## Development and distribution

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/package_source.py
```

Offline tests use mocks and never contact hardware. The explicit `tests/hardware_check.py` briefly enables measurement and writes five samples; run it only with an idle compatible instrument. Pass `--port /dev/ttyUSB0` on Linux or `--port COM5` on Windows.

CI is configured to run offline tests and a disconnected GUI smoke test on Windows and Linux with Python 3.10, 3.12 and 3.13, then build a wheel and source distribution. These hosted checks run after upload; they have not run merely because the repository was prepared. Artifacts are stored without automatic publication. See the [GitHub release guide](docs/GITHUB.md).

Source uploads exclude logs, measurement data, caches, virtual environments, and downloaded libraries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Application license

The original application code and waveform icon are provided under the [MIT license](LICENSE). Dependency licenses are separate and preserved.
