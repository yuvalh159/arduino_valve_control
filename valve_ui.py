"""
Python UI for controlling Airtec 4V130 C/E/P -M5 valve via Arduino.
Supports both UI buttons and hardware cycle button on the Arduino.
Requires: pyserial, customtkinter
Usage:    python valve_ui.py
"""

import customtkinter as ctk
from tkinter import messagebox
import serial
import serial.tools.list_ports
import threading
import queue
import time


class ValveController:
    """Thread-safe serial communication with background listener for button events."""

    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.response_queue = queue.Queue()
        self.event_queue = queue.Queue()
        self._reader_thread = None
        self._running = False

    def connect(self, port, baudrate=9600, timeout=0.1):
        with self.lock:
            if self.ser and self.ser.is_open:
                self._stop_reader()
                self.ser.close()
            self.ser = serial.Serial(port, baudrate, timeout=timeout)
            time.sleep(2)
            self.ser.reset_input_buffer()
            self._start_reader()

    def disconnect(self):
        with self.lock:
            self._stop_reader()
            if self.ser and self.ser.is_open:
                try:
                    self.ser.write(b"C")
                    time.sleep(0.15)
                except Exception:
                    pass
                self.ser.close()
            self.ser = None
            self._clear_queues()

    def is_connected(self):
        with self.lock:
            return self.ser is not None and self.ser.is_open

    def send_command(self, cmd):
        with self.lock:
            if not self.ser or not self.ser.is_open:
                raise ConnectionError("Not connected")
            self.ser.write(cmd.encode())

        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                line = self.response_queue.get(timeout=0.1)
                return line
            except queue.Empty:
                continue
        raise TimeoutError("No response from Arduino")

    def get_button_events(self):
        """Return list of state changes triggered by the hardware button."""
        events = []
        while True:
            try:
                events.append(self.event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _start_reader(self):
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _stop_reader(self):
        self._running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._reader_thread = None

    def _reader_loop(self):
        while self._running:
            try:
                if not self.ser or not self.ser.is_open:
                    break
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("BTN:"):
                    self.event_queue.put(line.split(":")[1])
                elif line.startswith(("OK:", "STATE:", "ERR:", "READY")):
                    self.response_queue.put(line)
            except Exception:
                if not self._running:
                    break

    def _clear_queues(self):
        for q in (self.response_queue, self.event_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    @staticmethod
    def list_ports():
        return [p.device for p in serial.tools.list_ports.comports()]


class ValveApp(ctk.CTk):

    VARIANTS = {
        "C  -  Closed Center": {
            "btn": "CENTER\nAll Blocked",
            "status": "Center  -  All ports blocked",
            "hint": "All ports blocked when centered",
        },
        "E  -  Exhaust Center": {
            "btn": "CENTER\nA+B Exhaust",
            "status": "Center  -  A and B exhaust",
            "hint": "A and B vent to exhaust when centered",
        },
        "P  -  Pressure Center": {
            "btn": "CENTER\nA+B Pressurised",
            "status": "Center  -  Pressure to A and B",
            "hint": "Pressure sent to A and B when centered",
        },
    }

    STATE_COLORS = {"A": "#2196F3", "B": "#FF9800", "C": "#4CAF50"}

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Airtec 4V130  -  Valve Controller")
        self.geometry("620x520")
        self.resizable(False, False)

        self.controller = ValveController()
        self.current_state = "C"
        self.busy = False
        self.result_queue = queue.Queue()

        self._build_ui()
        self._update_controls(connected=False)
        self._poll_results()
        self._poll_button_events()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # ── Connection ────────────────────────────────────────
        conn = ctk.CTkFrame(self, corner_radius=10)
        conn.grid(row=0, column=0, padx=16, pady=(16, 6), sticky="ew")
        conn.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(conn, text="COM Port", font=("Segoe UI", 13)).grid(
            row=0, column=0, padx=(16, 8), pady=12
        )

        self.port_var = ctk.StringVar()
        self.port_menu = ctk.CTkOptionMenu(
            conn, variable=self.port_var, values=[""], width=140,
            font=("Segoe UI", 12),
        )
        self.port_menu.grid(row=0, column=1, padx=4, pady=12, sticky="w")

        self.refresh_btn = ctk.CTkButton(
            conn, text="Refresh", width=80, command=self._refresh_ports,
            fg_color="#555", hover_color="#666", font=("Segoe UI", 12),
        )
        self.refresh_btn.grid(row=0, column=2, padx=4, pady=12)

        self.connect_btn = ctk.CTkButton(
            conn, text="Connect", width=110, command=self._toggle_connection,
            fg_color="#2e7d32", hover_color="#388e3c", font=("Segoe UI", 12, "bold"),
        )
        self.connect_btn.grid(row=0, column=3, padx=(4, 16), pady=12)

        # ── Variant ───────────────────────────────────────────
        var_frame = ctk.CTkFrame(self, corner_radius=10)
        var_frame.grid(row=1, column=0, padx=16, pady=6, sticky="ew")
        var_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(var_frame, text="Model", font=("Segoe UI", 13)).grid(
            row=0, column=0, padx=(16, 8), pady=12
        )

        self.variant_var = ctk.StringVar(value=list(self.VARIANTS.keys())[0])
        self.variant_menu = ctk.CTkOptionMenu(
            var_frame, variable=self.variant_var,
            values=list(self.VARIANTS.keys()), width=220,
            command=self._on_variant_change, font=("Segoe UI", 12),
        )
        self.variant_menu.grid(row=0, column=1, padx=4, pady=12)

        self.variant_hint = ctk.CTkLabel(
            var_frame, text=self.VARIANTS[self.variant_var.get()]["hint"],
            font=("Segoe UI", 11, "italic"), text_color="#aaa",
        )
        self.variant_hint.grid(row=0, column=2, padx=(12, 16), pady=12, sticky="w")

        # ── Controls ──────────────────────────────────────────
        ctrl = ctk.CTkFrame(self, corner_radius=10)
        ctrl.grid(row=2, column=0, padx=16, pady=6, sticky="ew")
        ctrl.grid_columnconfigure((0, 1, 2), weight=1)

        btn_h = 80
        btn_font = ("Segoe UI", 15, "bold")

        self.btn_a = ctk.CTkButton(
            ctrl, text="POSITION A\nSolenoid A", height=btn_h,
            fg_color="#1565C0", hover_color="#1976D2", font=btn_font,
            command=lambda: self._send("A"),
        )
        self.btn_a.grid(row=0, column=0, padx=(16, 6), pady=16, sticky="ew")

        variant_info = self.VARIANTS[self.variant_var.get()]
        self.btn_c = ctk.CTkButton(
            ctrl, text=variant_info["btn"], height=btn_h,
            fg_color="#2e7d32", hover_color="#388e3c", font=btn_font,
            command=lambda: self._send("C"),
        )
        self.btn_c.grid(row=0, column=1, padx=6, pady=16, sticky="ew")

        self.btn_b = ctk.CTkButton(
            ctrl, text="POSITION B\nSolenoid B", height=btn_h,
            fg_color="#e65100", hover_color="#f57c00", font=btn_font,
            command=lambda: self._send("B"),
        )
        self.btn_b.grid(row=0, column=2, padx=(6, 16), pady=16, sticky="ew")

        # ── Active indicator ──────────────────────────────────
        self.indicator_frame = ctk.CTkFrame(self, corner_radius=10)
        self.indicator_frame.grid(row=3, column=0, padx=16, pady=6, sticky="ew")
        self.indicator_frame.grid_columnconfigure(0, weight=1)

        self.state_label = ctk.CTkLabel(
            self.indicator_frame, text="DISCONNECTED",
            font=("Segoe UI", 22, "bold"), text_color="#777",
        )
        self.state_label.grid(row=0, column=0, pady=(16, 4))

        self.state_detail = ctk.CTkLabel(
            self.indicator_frame, text="Select a COM port and connect",
            font=("Segoe UI", 12), text_color="#999",
        )
        self.state_detail.grid(row=1, column=0, pady=(0, 4))

        self.source_label = ctk.CTkLabel(
            self.indicator_frame, text="",
            font=("Segoe UI", 10), text_color="#666",
        )
        self.source_label.grid(row=2, column=0, pady=(0, 12))

        # ── Status bar ────────────────────────────────────────
        self.status_bar = ctk.CTkLabel(
            self, text="Ready", font=("Segoe UI", 10),
            text_color="#666", anchor="w",
        )
        self.status_bar.grid(row=4, column=0, padx=20, pady=(2, 10), sticky="ew")

        self._refresh_ports()

    # ── Polling loops ─────────────────────────────────────────

    def _poll_results(self):
        try:
            while True:
                callback = self.result_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        self.after(50, self._poll_results)

    def _poll_button_events(self):
        if self.controller.is_connected():
            events = self.controller.get_button_events()
            for state in events:
                if state in ("A", "B", "C"):
                    self.current_state = state
                    self._show_state(state, source="button")
                    self.status_bar.configure(text=f"Button pressed  -  valve set to {state}")
        self.after(100, self._poll_button_events)

    def _run_async(self, func, on_done):
        def _worker():
            try:
                result = func()
                self.result_queue.put(lambda: on_done(result, None))
            except Exception as e:
                self.result_queue.put(lambda: on_done(None, e))
        threading.Thread(target=_worker, daemon=True).start()

    # ── Variant ───────────────────────────────────────────────

    def _on_variant_change(self, _choice=None):
        info = self.VARIANTS[self.variant_var.get()]
        self.btn_c.configure(text=info["btn"])
        self.variant_hint.configure(text=info["hint"])
        if self.current_state == "C":
            self._show_state("C")

    def _get_state_label(self, state):
        info = self.VARIANTS[self.variant_var.get()]
        return {
            "A": "Position A  -  P to A, B exhaust",
            "B": "Position B  -  P to B, A exhaust",
            "C": info["status"],
        }.get(state, state)

    # ── Connection ────────────────────────────────────────────

    def _refresh_ports(self):
        ports = ValveController.list_ports()
        if ports:
            self.port_menu.configure(values=ports)
            self.port_var.set(ports[0])
        else:
            self.port_menu.configure(values=["No ports found"])
            self.port_var.set("No ports found")

    def _toggle_connection(self):
        if self.busy:
            return
        if self.controller.is_connected():
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        port = self.port_var.get()
        if not port or port == "No ports found":
            messagebox.showwarning("No Port", "Select a COM port first.")
            return

        self.busy = True
        self.connect_btn.configure(text="Connecting...", state="disabled")
        self.status_bar.configure(text=f"Connecting to {port}...")

        def work():
            self.controller.connect(port)
            return self.controller.send_command("?")

        def done(result, err):
            self.busy = False
            if err:
                self.connect_btn.configure(text="Connect", state="normal")
                self.status_bar.configure(text=f"Failed: {err}")
                messagebox.showerror("Connection Failed", str(err))
                return
            self._update_controls(connected=True)
            state = result.split(":")[-1] if result else "C"
            self.current_state = state
            self._show_state(state)
            self.status_bar.configure(text=f"Connected on {port}")

        self._run_async(work, done)

    def _do_disconnect(self):
        self.busy = True
        self.connect_btn.configure(text="Disconnecting...", state="disabled")

        def work():
            self.controller.disconnect()

        def done(_result, _err):
            self.busy = False
            self.current_state = "C"
            self._update_controls(connected=False)
            self._show_state(None)
            self.status_bar.configure(text="Disconnected")

        self._run_async(work, done)

    # ── Valve commands ────────────────────────────────────────

    def _send(self, cmd):
        if self.busy or not self.controller.is_connected():
            return

        self.busy = True
        self._set_buttons_enabled(False)
        self.status_bar.configure(text=f"Sending {cmd}...")

        def work():
            return self.controller.send_command(cmd)

        def done(result, err):
            self.busy = False
            self._set_buttons_enabled(True)
            if err:
                self.status_bar.configure(text=f"Error: {err}")
                return
            if result and result.startswith("OK:"):
                state = result.split(":")[1]
                self.current_state = state
                self._show_state(state, source="ui")
                self.status_bar.configure(text=f"Valve set to {state}")
            else:
                self.status_bar.configure(text=f"Unexpected response: {result}")

        self._run_async(work, done)

    # ── UI helpers ────────────────────────────────────────────

    def _update_controls(self, connected):
        self._set_buttons_enabled(connected)
        if connected:
            self.connect_btn.configure(
                text="Disconnect", state="normal",
                fg_color="#c62828", hover_color="#e53935",
            )
        else:
            self.connect_btn.configure(
                text="Connect", state="normal",
                fg_color="#2e7d32", hover_color="#388e3c",
            )

    def _set_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.btn_a.configure(state=state)
        self.btn_b.configure(state=state)
        self.btn_c.configure(state=state)

    def _show_state(self, state, source=None):
        if state is None:
            self.state_label.configure(text="DISCONNECTED", text_color="#777")
            self.state_detail.configure(text="Select a COM port and connect")
            self.source_label.configure(text="")
            return

        names = {"A": "POSITION A", "B": "POSITION B", "C": "CENTER"}
        color = self.STATE_COLORS.get(state, "#777")
        self.state_label.configure(text=names.get(state, state), text_color=color)
        self.state_detail.configure(text=self._get_state_label(state))

        if source == "button":
            self.source_label.configure(text="Changed via hardware button")
        elif source == "ui":
            self.source_label.configure(text="Changed via UI")
        else:
            self.source_label.configure(text="")

    def on_close(self):
        if self.controller.is_connected():
            try:
                self.controller.disconnect()
            except Exception:
                pass
        self.destroy()


def main():
    app = ValveApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
