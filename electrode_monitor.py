"""Local electrode voltage monitor with CSV and passive serial logging."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import math
from pathlib import Path
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from serial.tools import list_ports
from serial_protocol import InstrumentClient
from session_log import SessionLog

APP_NAME = "Electrode Monitor"
DEFAULT_LOG_DIR = Path.home() / "Electrode Monitor Logs"


def now():
    return datetime.now().astimezone()


def visible_bytes(data: bytes) -> str:
    names = {13: "<CR>", 10: "<LF>", 17: "<XON>", 19: "<XOFF>"}
    return "".join(names.get(b, chr(b) if 32 <= b < 127 else f"<0x{b:02X}>") for b in data)


class InstrumentWorker(threading.Thread):
    """Own the serial port and files on one thread; never touch Tk here."""

    def __init__(self, events):
        super().__init__(name="Electrode serial worker", daemon=False)
        self.events = events
        self.commands = queue.Queue()
        self.device = None
        self.monitoring = False
        self.logger = None
        self.interval = 1.0
        self.firmware = ""
        self.input_name = ""
        self.port = ""
        self.origin = 0.0
        self.next_poll = 0.0
        self.running = True

    def emit(self, kind, **values):
        self.events.put((kind, values))

    def traffic(self, direction, data):
        stamp = now()
        self.emit("traffic", timestamp=stamp, direction=direction, data=data)
        if self.logger:
            try:
                self.logger.write_traffic(stamp, direction, data)
            except Exception as exc:
                self.log_failure(exc)

    def log_failure(self, exc):
        self.stop_log()
        self.emit("error", message=f"File logging stopped: {exc}. Live monitoring can continue.")

    def stop_log(self):
        logger, self.logger = self.logger, None
        if logger:
            try:
                logger.close()
            except Exception as exc:
                self.emit("error", message=f"Could not finish log files: {exc}")
        self.emit("logging", active=False)

    def stop_monitor(self):
        self.monitoring = False
        try:
            if self.device:
                self.device.stop_measurement()
        finally:
            self.emit("monitor", active=False)

    def disconnect(self):
        device = self.device
        try:
            self.stop_monitor()
        except Exception as exc:
            self.emit("error", message=f"Could not verify measurement stopped: {exc}. Check the instrument before running a method.")
        finally:
            if device:
                try:
                    device.close()
                except Exception as exc:
                    self.emit("error", message=str(exc))
            self.device = None
            self.stop_log()
            self.emit("connection", connected=False)

    def run(self):
        try:
            while self.running:
                wait = max(0.0, min(0.1, self.next_poll - time.monotonic())) if self.monitoring else 0.1
                try:
                    action, args = self.commands.get(timeout=wait)
                except queue.Empty:
                    action, args = None, {}
                if action:
                    try:
                        self.handle(action, args)
                    except Exception as exc:
                        self.emit("error", message=str(exc))
                        if action in {"connect", "start", "status", "stop"}:
                            self.disconnect()
                    finally:
                        self.emit("ready")
                if self.running and self.monitoring and time.monotonic() >= self.next_poll:
                    try:
                        value = self.device.read_mv(expected_input=self.input_name)
                        stamp, mono = now(), time.monotonic()
                        elapsed = mono - self.origin
                        self.emit("sample", timestamp=stamp, elapsed=elapsed, mv=value)
                        if self.logger:
                            try:
                                self.logger.write_sample(stamp, elapsed, value)
                            except Exception as exc:
                                self.log_failure(exc)
                        # No catch-up bursts if a response takes longer than the requested period.
                        self.next_poll = max(self.next_poll + self.interval, mono + 0.05)
                    except Exception as exc:
                        self.emit("error", message=f"Monitoring stopped: {exc}")
                        self.disconnect()
        finally:
            self.disconnect()
            self.emit("closed")

    def handle(self, action, args):
        if action == "connect":
            self.port = args["port"]
            self.device = InstrumentClient(port=self.port, baudrate=9600, callback=self.traffic)
            self.device.connect()
            self.firmware = self.device.identify()
            if not self.firmware.startswith("716."):
                raise ValueError(f"Unsupported instrument firmware: {self.firmware!r}.")
            self.input_name = self.device.measurement_input()
            status = self.device.status()
            self.emit("connection", connected=True, firmware=self.firmware, input=self.input_name, status=status)
        elif action == "disconnect":
            self.disconnect()
        elif action == "start":
            self.interval = args["interval"]
            self.input_name = self.device.measurement_input()
            owned = self.device.start_measurement()
            self.origin = time.monotonic()
            # Allow the newly enabled measurement to settle before the first read.
            self.next_poll = self.origin + 0.5
            self.monitoring = True
            self.emit("monitor", active=True, input=self.input_name, owned=owned)
        elif action == "stop":
            self.stop_monitor()
            self.stop_log()
        elif action == "status":
            self.emit("status", status=self.device.status())
        elif action == "log_start":
            if not self.device or not self.monitoring:
                raise ValueError("Start the mV monitor before opening a log file.")
            if self.logger:
                raise ValueError("A log is already open.")
            self.logger = SessionLog(args["path"], self.port, self.firmware, self.input_name, self.interval)
            self.emit("logging", active=True, path=str(args["path"]))
        elif action == "log_stop":
            self.stop_log()
        elif action == "quit":
            self.running = False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self._set_original_icon()
        self.geometry("1060x820")
        self.minsize(900, 740)
        self.configure(bg="#edf2f5")
        self.events = queue.Queue()
        self.worker = InstrumentWorker(self.events)
        self.connected = self.monitoring = self.logging = self.busy = self.closing = False
        self.samples = deque(maxlen=600)
        self.traffic_history = deque(maxlen=5000)
        self.sample_count = 0
        self.last_sample_time = None
        self.last_sample_label = ""
        self.input_name = "—"
        self.port_var = tk.StringVar(value="COM5" if os.name == "nt" else "")
        self.interval_var = tk.StringVar(value="1.0")
        self.connection_var = tk.StringVar(value="Disconnected")
        self.detail_var = tk.StringVar(value="9600 baud  ·  8 data bits  ·  no parity  ·  1 stop bit  ·  RTS + DTR active")
        self.reading_var = tk.StringVar(value="—")
        self.measure_var = tk.StringVar(value="Connect, then start the mV monitor.")
        self.log_var = tk.StringVar(value="Not recording")
        self.status_var = tk.StringVar(value="Ready")
        self.autoscroll = tk.BooleanVar(value=True)
        self._build()
        self.refresh_ports()
        self.worker.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self.drain)

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf2f5")
        style.configure("TLabel", background="#edf2f5", foreground="#24384b", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", foreground="white", background="#087f8c")
        style.map("Accent.TButton", background=[("active", "#096b76"), ("disabled", "#a5b5bd")])
        style.configure("TLabelframe", background="#edf2f5")
        style.configure("TLabelframe.Label", background="#edf2f5", foreground="#24384b", font=("Segoe UI Semibold", 10))
        main = ttk.Frame(self, padding=20)
        main.pack(fill="both", expand=True)
        title = ttk.Frame(main)
        title.pack(fill="x")
        ttk.Label(title, text="ELECTRODE MONITOR", font=("Segoe UI Semibold", 18)).pack(side="left")
        ttk.Label(title, textvariable=self.connection_var, foreground="#087f8c").pack(side="right")
        ttk.Label(main, text="Electrode voltage • live monitoring & local logging", foreground="#607587").pack(anchor="w", pady=(2, 14))

        connection = ttk.Frame(main)
        connection.pack(fill="x")
        ttk.Label(connection, text="Serial port").pack(side="left", padx=(0, 8))
        self.port_box = ttk.Combobox(connection, textvariable=self.port_var, width=13)
        self.port_box.pack(side="left")
        self.refresh_btn = ttk.Button(connection, text="Refresh", command=self.refresh_ports)
        self.refresh_btn.pack(side="left", padx=6)
        self.connect_btn = ttk.Button(connection, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left")
        self.status_btn = ttk.Button(connection, text="Read status", command=lambda: self.send("status"))
        self.status_btn.pack(side="left", padx=6)
        ttk.Button(connection, text="Help", command=self.show_help).pack(side="right")
        ttk.Label(main, textvariable=self.detail_var, foreground="#607587", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 12))

        reading = tk.Frame(main, bg="white", padx=18, pady=10)
        reading.pack(fill="x")
        tk.Label(reading, text="ELECTRODE POTENTIAL", bg="white", fg="#607587", font=("Segoe UI Semibold", 10)).pack(anchor="w")
        number = tk.Frame(reading, bg="white")
        number.pack(anchor="w")
        tk.Label(number, textvariable=self.reading_var, bg="white", fg="#087f8c", font=("Segoe UI", 42, "bold")).pack(side="left")
        tk.Label(number, text="mV", bg="white", fg="#607587", font=("Segoe UI", 20)).pack(side="left", padx=12, pady=(17, 0))
        tk.Label(reading, textvariable=self.measure_var, bg="white", fg="#607587", font=("Segoe UI", 10)).pack(anchor="w")

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=12)
        ttk.Label(controls, text="Read every").pack(side="left")
        self.interval_box = ttk.Spinbox(controls, from_=0.5, to=3600, increment=0.5, textvariable=self.interval_var, width=6)
        self.interval_box.pack(side="left", padx=6)
        ttk.Label(controls, text="s").pack(side="left", padx=(0, 12))
        self.start_btn = ttk.Button(controls, text="Start mV monitor", style="Accent.TButton", command=self.start_monitor)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(controls, text="Stop", command=lambda: self.send("stop"))
        self.stop_btn.pack(side="left", padx=6)
        self.log_btn = ttk.Button(controls, text="Start CSV log…", command=self.toggle_log)
        self.log_btn.pack(side="right")
        ttk.Label(main, textvariable=self.log_var, foreground="#607587", wraplength=970).pack(anchor="w", pady=(0, 6))

        self.plot = tk.Canvas(main, height=155, bg="white", highlightthickness=0)
        self.plot.pack(fill="x")
        self.plot.bind("<Configure>", lambda _: self.draw_plot())

        terminal_frame = ttk.LabelFrame(main, text="RS232 traffic · passive observer", padding=8)
        terminal_frame.pack(fill="both", expand=True, pady=(12, 6))
        options = ttk.Frame(terminal_frame)
        options.pack(fill="x", pady=(0, 5))
        ttk.Label(options, text="Actual app TX/RX bytes • no command entry", foreground="#607587", font=("Segoe UI", 9)).pack(side="left")
        ttk.Checkbutton(options, text="Auto-scroll", variable=self.autoscroll).pack(side="right")
        ttk.Button(options, text="Save traffic…", command=self.save_traffic, padding=3).pack(side="right", padx=8)
        text_frame = ttk.Frame(terminal_frame)
        text_frame.pack(fill="both", expand=True)
        self.terminal = tk.Text(text_frame, height=8, wrap="none", bg="#142737", fg="#d7e3ec", insertbackground="white", font=("Consolas", 9), relief="flat", state="disabled")
        yscroll = ttk.Scrollbar(text_frame, command=self.terminal.yview)
        xscroll = ttk.Scrollbar(terminal_frame, orient="horizontal", command=self.terminal.xview)
        self.terminal.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        self.terminal.pack(side="left", fill="both", expand=True)
        xscroll.pack(fill="x")
        self.terminal.tag_configure("TX", foreground="#74d3dd")
        self.terminal.tag_configure("RX", foreground="#c8e7a0")
        ttk.Label(main, textvariable=self.status_var, foreground="#526b7d", wraplength=990).pack(anchor="w")
        self.update_controls()

    def refresh_ports(self):
        try:
            ports = [p.device for p in list_ports.comports()]
            self.port_box["values"] = ports
            if not self.port_var.get() and ports:
                self.port_var.set(ports[0])
        except Exception as exc:
            self.status_var.set(f"Port discovery: {exc}")

    def send(self, action, **args):
        if self.busy or self.closing:
            return
        self.busy = True
        self.status_var.set("Working…")
        self.update_controls()
        self.worker.commands.put((action, args))

    def toggle_connect(self):
        if self.connected:
            self.send("disconnect")
        else:
            port = self.port_var.get().strip()
            if not port:
                messagebox.showerror("Serial port", "Choose a COM port.", parent=self)
                return
            self.send("connect", port=port)

    def start_monitor(self):
        try:
            interval = float(self.interval_var.get())
            if not math.isfinite(interval) or not 0.5 <= interval <= 3600:
                raise ValueError
        except ValueError:
            messagebox.showerror("Read interval", "Enter an interval from 0.5 to 3600 seconds.", parent=self)
            return
        self.send("start", interval=interval)

    def toggle_log(self):
        if self.logging:
            self.send("log_stop")
            return
        logs = DEFAULT_LOG_DIR
        try:
            logs.mkdir(exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Log folder", str(exc), parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, title="New mV CSV log (existing files are never overwritten)", initialdir=str(logs), initialfile=f"mv_{now():%Y%m%d_%H%M%S}.csv", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            self.send("log_start", path=path)

    def save_traffic(self):
        path = filedialog.asksaveasfilename(parent=self, title="Save recent RS232 traffic", initialdir=str(Path.home()), initialfile=f"traffic_{now():%Y%m%d_%H%M%S}.txt", defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            try:
                with open(path, "x", encoding="utf-8") as f:
                    f.writelines(self.traffic_history)
            except OSError as exc:
                messagebox.showerror("Save traffic", str(exc), parent=self)

    def update_controls(self):
        available = not self.busy and not self.closing
        def state(widget, enabled):
            widget.configure(state="normal" if enabled else "disabled")
        state(self.connect_btn, available)
        self.connect_btn.configure(text="Disconnect" if self.connected else "Connect")
        state(self.port_box, available and not self.connected)
        state(self.refresh_btn, available and not self.connected)
        state(self.status_btn, available and self.connected)
        state(self.start_btn, available and self.connected and not self.monitoring)
        state(self.stop_btn, available and self.monitoring)
        state(self.interval_box, available and not self.monitoring)
        state(self.log_btn, available and self.monitoring)
        self.log_btn.configure(text="Stop CSV log" if self.logging else "Start CSV log…")

    def drain(self):
        for _ in range(300):
            try:
                kind, values = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "closed":
                if self.closing:
                    self.destroy()
                    return
            else:
                self.on_event(kind, values)
        if self.monitoring and self.last_sample_time is not None:
            age = time.monotonic() - self.last_sample_time
            if age > max(4.0, float(self.interval_var.get()) * 2.5):
                self.reading_var.set("—")
                self.measure_var.set(f"Waiting for a fresh reading • last sample {age:.0f} s ago")
        self.after(80, self.drain)

    def on_event(self, kind, v):
        if kind == "traffic":
            line = f"{v['timestamp'].isoformat(timespec='milliseconds')}  {v['direction']}  {visible_bytes(v['data'])}\n"
            self.traffic_history.append(line)
            self.terminal.configure(state="normal")
            self.terminal.insert("end", line, v["direction"])
            lines = int(self.terminal.index("end-1c").split(".")[0])
            if lines > 2000:
                self.terminal.delete("1.0", f"{lines - 1800}.0")
            if self.autoscroll.get():
                self.terminal.see("end")
            self.terminal.configure(state="disabled")
            return
        if kind == "connection":
            self.connected = v["connected"]
            if self.connected:
                self.input_name = v["input"]
                self.connection_var.set(f"Connected · {self.port_var.get()} · {v['firmware']}")
                self.measure_var.set(f"Electrode input {self.input_name} • ready to monitor")
                self.status_var.set(v["status"])
            else:
                self.connection_var.set("Disconnected")
                self.reading_var.set("—")
                self.measure_var.set("Disconnected • no live reading")
        elif kind == "monitor":
            self.monitoring = v["active"]
            self.reading_var.set("—")
            if self.monitoring:
                self.samples.clear()
                self.sample_count = 0
                self.last_sample_time = None
                self.input_name = v["input"]
                self.measure_var.set(f"Electrode input {self.input_name} • waiting for first reading")
                self.status_var.set("Monitoring. Stop releases measurement started by this app." if v["owned"] else "Observing measurement already enabled on the instrument; Stop leaves it enabled.")
                self.draw_plot()
            else:
                self.measure_var.set("Monitor stopped • last trace retained")
        elif kind == "sample":
            self.sample_count += 1
            self.last_sample_time = time.monotonic()
            self.reading_var.set(f"{v['mv']:+.1f}")
            self.measure_var.set(f"Input {self.input_name}  •  {v['timestamp']:%H:%M:%S}  •  {self.sample_count} readings")
            self.samples.append((v["elapsed"], v["mv"]))
            self.draw_plot()
        elif kind == "logging":
            self.logging = v["active"]
            self.log_var.set(f"Recording CSV + RS232 log: {v['path']}" if self.logging else "Not recording")
        elif kind == "status":
            self.status_var.set(v["status"])
        elif kind == "error":
            self.status_var.set(v["message"])
            if not self.closing:
                messagebox.showerror(APP_NAME, v["message"], parent=self)
        elif kind == "ready":
            self.busy = False
            if self.status_var.get() == "Working…":
                self.status_var.set("Ready")
        self.update_controls()

    def draw_plot(self):
        c = self.plot
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        left, right, top, bottom = 72, w - 20, 36, h - 28
        c.create_text(12, 10, anchor="nw", text="Recent voltage", fill="#607587", font=("Segoe UI", 9))
        if not self.samples or w < 150:
            c.create_text(w / 2, h / 2, text="The voltage trace appears here when monitoring starts.", fill="#7d909e", font=("Segoe UI", 10))
            return
        points = list(self.samples)
        low, high = min(v for _, v in points), max(v for _, v in points)
        pad = max(0.5, (high - low) * 0.12)
        low, high = low - pad, high + pad
        t0, t1 = points[0][0], max(points[-1][0], points[0][0] + 1.0)
        for i in range(3):
            y = top + (bottom - top) * i / 2
            label = high - (high - low) * i / 2
            c.create_line(left, y, right, y, fill="#e9eff3")
            c.create_text(left - 8, y, anchor="e", text=f"{label:.1f}", fill="#607587", font=("Segoe UI", 9))
        coordinates = []
        for t, v in points:
            coordinates.extend((left + (t - t0) / (t1 - t0) * (right - left), bottom - (v - low) / (high - low) * (bottom - top)))
        if len(coordinates) > 2:
            c.create_line(*coordinates, fill="#087f8c", width=2)
        x, y = coordinates[-2:]
        c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#087f8c", outline="")
        c.create_text(left, h - 10, anchor="w", text=f"{t0:.0f} s", fill="#607587", font=("Segoe UI", 9))
        c.create_text(right, h - 10, anchor="e", text=f"{points[-1][0]:.1f} s elapsed", fill="#607587", font=("Segoe UI", 9))

    def on_close(self):
        if self.closing:
            return
        self.closing = True
        self.status_var.set("Stopping measurement, closing logs and releasing COM port…")
        self.update_controls()
        self.worker.commands.put(("quit", {}))

    def _set_original_icon(self):
        """Draw an original geometric trace instead of a third-party logo."""
        self.app_icon = tk.PhotoImage(width=32, height=32)
        self.app_icon.put("#087f8c", to=(0, 0, 32, 32))
        points = [(4, 18), (9, 18), (13, 8), (19, 25), (23, 14), (28, 14)]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            steps = max(abs(x1 - x0), abs(y1 - y0))
            for step in range(steps + 1):
                x = round(x0 + (x1 - x0) * step / steps)
                y = round(y0 + (y1 - y0) * step / steps)
                self.app_icon.put("white", to=(x, y, x + 2, y + 2))
        self.iconphoto(True, self.app_icon)

    def show_help(self):
        messagebox.showinfo(
            APP_NAME,
            "Connect → Start mV monitor → Start CSV log.\n\n"
            "Use an idle, compatible instrument with RS control enabled and "
            "9600 / 8 / no parity / 1 stop bit / hardware handshake.\n\n"
            "The selected electrode input is preserved. Stop ends recording "
            "and switches off only measurement started by this app.\n\n"
            "The traffic pane observes this app's connection. "
            "Close other serial applications before connecting.\n\n"
            "No titration or dosing commands are provided.",
            parent=self,
        )


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
