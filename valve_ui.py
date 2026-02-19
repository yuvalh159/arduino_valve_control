"""
Python UI for controlling Airtec 4V130 C/E/P-M5 valve via Arduino.
Supports UI commands, hardware A/B/C button updates, COM auto-detection, and sequences.
Requires: pyserial, customtkinter
Usage:    python valve_ui.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import serial
import serial.tools.list_ports
import threading
import queue
import time


class ValveController:
    """Thread-safe serial communication with background listener."""

    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
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
            # Arduino boards typically reset on serial open.
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
        expected_prefix = "STATE:" if cmd == "?" else "OK:"

        with self.command_lock:
            with self.lock:
                if not self.ser or not self.ser.is_open:
                    raise ConnectionError("Not connected")
                self.ser.write(cmd.encode())

            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    line = self.response_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Ignore boot banner if it arrives late.
                if line == "READY":
                    continue

                if line.startswith("ERR:"):
                    return line
                if line.startswith(expected_prefix):
                    return line
                # Keep compatibility with unexpected but valid responses.
                if line.startswith(("OK:", "STATE:")):
                    return line

        raise TimeoutError("No response from Arduino")

    def get_button_events(self):
        """Return state changes triggered by hardware A/B/C buttons."""
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
        return [p["device"] for p in ValveController.list_ports_with_details()]

    @staticmethod
    def list_ports_with_details():
        details = []
        for port in serial.tools.list_ports.comports():
            details.append(
                {
                    "device": port.device,
                    "description": port.description or "",
                    "manufacturer": getattr(port, "manufacturer", "") or "",
                    "product": getattr(port, "product", "") or "",
                    "hwid": port.hwid or "",
                    "vid": getattr(port, "vid", None),
                    "pid": getattr(port, "pid", None),
                }
            )
        details.sort(key=lambda item: item["device"])
        return details

    @staticmethod
    def probe_port(port, baudrate=9600):
        """Try a short handshake on a port and return (matched, response_text)."""
        ser = None
        try:
            ser = serial.Serial(port, baudrate, timeout=0.25)
            time.sleep(1.8)
            ser.reset_input_buffer()
            ser.write(b"?")
            deadline = time.time() + 1.5
            saw_ready = False

            while time.time() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("STATE:"):
                    return True, line
                if line.startswith("READY") and not saw_ready:
                    saw_ready = True
                    ser.write(b"?")

            return False, "No STATE response"
        except Exception as exc:
            return False, str(exc)
        finally:
            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass


class ValveApp(ctk.CTk):
    BASE_WIDTH = 900
    BASE_HEIGHT = 760
    MIN_WIDTH = 780
    MIN_HEIGHT = 620
    BASE_UI_SCALE = 0.95
    MIN_UI_SCALE = 0.85
    MAX_UI_SCALE = 1.08
    USER_ZOOM_MIN = 0.75
    USER_ZOOM_MAX = 1.60
    USER_ZOOM_STEP = 0.10

    VARIANTS = {
        "C  -  Closed Center": {
            "btn": "CENTER\nAll Blocked",
            "status": "Center  -  All ports blocked",
            "hint": "All ports blocked while centered",
        },
        "E  -  Exhaust Center": {
            "btn": "CENTER\nA+B Exhaust",
            "status": "Center  -  A and B exhaust",
            "hint": "A and B vent to exhaust while centered",
        },
        "P  -  Pressure Center": {
            "btn": "CENTER\nA+B Pressurized",
            "status": "Center  -  Pressure to A and B",
            "hint": "Pressure sent to A and B while centered",
        },
    }

    STATE_COLORS = {"A": "#42A5F5", "B": "#FFA726", "C": "#66BB6A"}
    ARDUINO_HINTS = ("arduino", "ch340", "wch", "cp210", "ftdi", "usb serial")
    ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403}

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Airtec 4V130  -  Valve Controller")
        self.geometry(f"{self.BASE_WIDTH}x{self.BASE_HEIGHT}")
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resizable(True, True)
        self._resize_job = None
        self._fit_scale = self.BASE_UI_SCALE
        self._manual_zoom = 1.0
        self._ui_scale = self.BASE_UI_SCALE
        ctk.set_widget_scaling(self._ui_scale)

        self.controller = ValveController()
        self.current_state = "C"
        self.busy = False
        self.detecting = False

        self.result_queue = queue.Queue()
        self.port_details_by_device = {}

        self.sequence_steps = []
        self.sequence_running = False
        self.sequence_stop_event = threading.Event()
        self.sequence_thread = None
        self.sequence_total_steps = 0
        self.sequence_nodes = {}
        self.sequence_links = {}
        self.sequence_incoming = {}
        self.selected_sequence_node = None
        self.pending_link_source = None
        self.next_sequence_node_id = 1
        self.last_added_sequence_node = None
        self.drag_node_id = None
        self.drag_last_x = 0
        self.drag_last_y = 0

        self._build_ui()
        self._refresh_zoom_label()
        self.bind("<Configure>", self._on_window_resize)
        self._bind_zoom_shortcuts()
        self.bind_all("<MouseWheel>", self._on_page_mousewheel, add="+")
        self.bind_all("<Button-4>", lambda _event: self._scroll_page(-1), add="+")
        self.bind_all("<Button-5>", lambda _event: self._scroll_page(1), add="+")
        self._update_controls(connected=False)
        self._poll_results()
        self._poll_button_events()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.page = ctk.CTkScrollableFrame(
            self, corner_radius=0, fg_color="transparent"
        )
        self.page.grid(row=0, column=0, sticky="nsew")
        self.page.grid_columnconfigure(0, weight=1)
        self.page.grid_rowconfigure(5, weight=1)

        # Header
        header = ctk.CTkFrame(self.page, corner_radius=12)
        header.grid(row=0, column=0, padx=16, pady=(16, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            header,
            text="Airtec 4V130 Valve Controller",
            font=("Segoe UI", 21, "bold"),
        ).grid(row=0, column=0, padx=16, pady=(14, 2), sticky="w")
        ctk.CTkLabel(
            header,
            text="Connect Arduino, drive A/B/Center, and run repeatable valve sequences.",
            font=("Segoe UI", 11),
            text_color="#A8A8A8",
        ).grid(row=1, column=0, padx=16, pady=(0, 12), sticky="w")

        zoom_bar = ctk.CTkFrame(header, fg_color="transparent")
        zoom_bar.grid(row=0, column=1, rowspan=2, padx=(8, 16), pady=10, sticky="e")

        self.zoom_out_btn = ctk.CTkButton(
            zoom_bar,
            text="-",
            width=34,
            command=self._zoom_out,
            font=("Segoe UI", 13, "bold"),
        )
        self.zoom_out_btn.grid(row=0, column=0, padx=(0, 4))

        self.zoom_label = ctk.CTkLabel(
            zoom_bar, text="100%", width=58, anchor="center", font=("Segoe UI", 11)
        )
        self.zoom_label.grid(row=0, column=1, padx=2)

        self.zoom_in_btn = ctk.CTkButton(
            zoom_bar,
            text="+",
            width=34,
            command=self._zoom_in,
            font=("Segoe UI", 13, "bold"),
        )
        self.zoom_in_btn.grid(row=0, column=2, padx=(4, 0))

        # Connection card
        conn = ctk.CTkFrame(self.page, corner_radius=12)
        conn.grid(row=1, column=0, padx=16, pady=6, sticky="ew")
        conn.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(conn, text="COM Port", font=("Segoe UI", 13)).grid(
            row=0, column=0, padx=(16, 8), pady=(12, 8), sticky="w"
        )

        self.port_var = ctk.StringVar(value="No ports found")
        self.port_menu = ctk.CTkOptionMenu(
            conn,
            variable=self.port_var,
            values=["No ports found"],
            command=self._on_port_selected,
            width=150,
            font=("Segoe UI", 12),
        )
        self.port_menu.grid(row=0, column=1, padx=4, pady=(12, 8), sticky="w")

        self.refresh_btn = ctk.CTkButton(
            conn,
            text="Refresh",
            width=90,
            command=self._refresh_ports,
            fg_color="#4E4E4E",
            hover_color="#5A5A5A",
            font=("Segoe UI", 12),
        )
        self.refresh_btn.grid(row=0, column=2, padx=4, pady=(12, 8))

        self.detect_btn = ctk.CTkButton(
            conn,
            text="Detect Arduino",
            width=130,
            command=self._detect_arduino_port,
            fg_color="#3949AB",
            hover_color="#3F51B5",
            font=("Segoe UI", 12),
        )
        self.detect_btn.grid(row=0, column=3, padx=4, pady=(12, 8))

        self.connect_btn = ctk.CTkButton(
            conn,
            text="Connect",
            width=115,
            command=self._toggle_connection,
            fg_color="#2E7D32",
            hover_color="#388E3C",
            font=("Segoe UI", 12, "bold"),
        )
        self.connect_btn.grid(row=0, column=4, padx=(4, 16), pady=(12, 8))

        self.port_info_label = ctk.CTkLabel(
            conn,
            text="Select a COM port to view USB details.",
            font=("Segoe UI", 11),
            text_color="#A0A0A0",
            anchor="w",
        )
        self.port_info_label.grid(
            row=1, column=0, columnspan=5, padx=16, pady=(0, 4), sticky="ew"
        )

        self.connected_port_label = ctk.CTkLabel(
            conn,
            text="Arduino COM: Not connected",
            font=("Segoe UI", 11, "bold"),
            text_color="#8F8F8F",
            anchor="w",
        )
        self.connected_port_label.grid(
            row=2, column=0, columnspan=5, padx=16, pady=(0, 12), sticky="ew"
        )

        # Variant and sync card
        var_frame = ctk.CTkFrame(self.page, corner_radius=12)
        var_frame.grid(row=2, column=0, padx=16, pady=6, sticky="ew")
        var_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(var_frame, text="Valve Variant", font=("Segoe UI", 13)).grid(
            row=0, column=0, padx=(16, 8), pady=12, sticky="w"
        )

        self.variant_var = ctk.StringVar(value=list(self.VARIANTS.keys())[0])
        self.variant_menu = ctk.CTkOptionMenu(
            var_frame,
            variable=self.variant_var,
            values=list(self.VARIANTS.keys()),
            command=self._on_variant_change,
            width=230,
            font=("Segoe UI", 12),
        )
        self.variant_menu.grid(row=0, column=1, padx=4, pady=12, sticky="w")

        self.variant_hint = ctk.CTkLabel(
            var_frame,
            text=self.VARIANTS[self.variant_var.get()]["hint"],
            font=("Segoe UI", 11, "italic"),
            text_color="#AAAAAA",
        )
        self.variant_hint.grid(row=0, column=2, padx=(12, 8), pady=12, sticky="w")

        self.read_state_btn = ctk.CTkButton(
            var_frame,
            text="Read State",
            width=105,
            command=self._query_state,
            fg_color="#455A64",
            hover_color="#546E7A",
            font=("Segoe UI", 12),
        )
        self.read_state_btn.grid(row=0, column=3, padx=(4, 16), pady=12)

        # Manual controls card
        ctrl = ctk.CTkFrame(self.page, corner_radius=12)
        ctrl.grid(row=3, column=0, padx=16, pady=6, sticky="ew")
        ctrl.grid_columnconfigure((0, 1, 2), weight=1)

        btn_h = 70
        btn_font = ("Segoe UI", 13, "bold")

        self.btn_a = ctk.CTkButton(
            ctrl,
            text="POSITION A\nSolenoid A",
            height=btn_h,
            fg_color="#1565C0",
            hover_color="#1976D2",
            font=btn_font,
            command=lambda: self._send("A"),
        )
        self.btn_a.grid(row=0, column=0, padx=(16, 6), pady=16, sticky="ew")

        variant_info = self.VARIANTS[self.variant_var.get()]
        self.btn_c = ctk.CTkButton(
            ctrl,
            text=variant_info["btn"],
            height=btn_h,
            fg_color="#2E7D32",
            hover_color="#388E3C",
            font=btn_font,
            command=lambda: self._send("C"),
        )
        self.btn_c.grid(row=0, column=1, padx=6, pady=16, sticky="ew")

        self.btn_b = ctk.CTkButton(
            ctrl,
            text="POSITION B\nSolenoid B",
            height=btn_h,
            fg_color="#E65100",
            hover_color="#F57C00",
            font=btn_font,
            command=lambda: self._send("B"),
        )
        self.btn_b.grid(row=0, column=2, padx=(6, 16), pady=16, sticky="ew")

        # Active indicator card
        self.indicator_frame = ctk.CTkFrame(self.page, corner_radius=12)
        self.indicator_frame.grid(row=4, column=0, padx=16, pady=6, sticky="ew")
        self.indicator_frame.grid_columnconfigure(0, weight=1)

        self.state_label = ctk.CTkLabel(
            self.indicator_frame,
            text="DISCONNECTED",
            font=("Segoe UI", 24, "bold"),
            text_color="#777777",
        )
        self.state_label.grid(row=0, column=0, pady=(16, 4))

        self.state_detail = ctk.CTkLabel(
            self.indicator_frame,
            text="Select a COM port and connect",
            font=("Segoe UI", 12),
            text_color="#999999",
        )
        self.state_detail.grid(row=1, column=0, pady=(0, 4))

        self.source_label = ctk.CTkLabel(
            self.indicator_frame,
            text="",
            font=("Segoe UI", 10),
            text_color="#777777",
        )
        self.source_label.grid(row=2, column=0, pady=(0, 12))

        # Sequence card
        seq = ctk.CTkFrame(self.page, corner_radius=12)
        seq.grid(row=5, column=0, padx=16, pady=6, sticky="nsew")
        seq.grid_columnconfigure(0, weight=1)
        seq.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(seq, text="Sequence Builder", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, padx=16, pady=(12, 4), sticky="w"
        )

        editor = ctk.CTkFrame(seq, fg_color="transparent")
        editor.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="ew")
        editor.grid_columnconfigure(10, weight=1)

        ctk.CTkLabel(editor, text="Step", font=("Segoe UI", 12)).grid(
            row=0, column=0, padx=(4, 6), pady=6
        )
        self.seq_state_var = ctk.StringVar(value="A")
        self.seq_state_menu = ctk.CTkOptionMenu(
            editor,
            variable=self.seq_state_var,
            values=["A", "B", "C"],
            width=75,
            font=("Segoe UI", 12),
        )
        self.seq_state_menu.grid(row=0, column=1, padx=4, pady=6)

        ctk.CTkLabel(editor, text="Duration (s)", font=("Segoe UI", 12)).grid(
            row=0, column=2, padx=(10, 6), pady=6
        )
        self.seq_duration_var = ctk.StringVar(value="1.0")
        self.seq_duration_entry = ctk.CTkEntry(
            editor, textvariable=self.seq_duration_var, width=100, font=("Segoe UI", 12)
        )
        self.seq_duration_entry.grid(row=0, column=3, padx=4, pady=6)

        self.seq_add_btn = ctk.CTkButton(
            editor,
            text="Add Step",
            width=95,
            command=self._add_sequence_step,
            fg_color="#00796B",
            hover_color="#00897B",
            font=("Segoe UI", 12),
        )
        self.seq_add_btn.grid(row=0, column=4, padx=4, pady=6)

        self.seq_update_btn = ctk.CTkButton(
            editor,
            text="Apply to Selected",
            width=130,
            command=self._update_selected_sequence_step,
            fg_color="#546E7A",
            hover_color="#607D8B",
            font=("Segoe UI", 12),
        )
        self.seq_update_btn.grid(row=0, column=5, padx=4, pady=6)

        self.seq_remove_btn = ctk.CTkButton(
            editor,
            text="Remove",
            width=110,
            command=self._remove_last_sequence_step,
            fg_color="#6D4C41",
            hover_color="#795548",
            font=("Segoe UI", 12),
        )
        self.seq_remove_btn.grid(row=0, column=6, padx=4, pady=6)

        self.seq_clear_btn = ctk.CTkButton(
            editor,
            text="Clear",
            width=80,
            command=self._clear_sequence_steps,
            fg_color="#5D4037",
            hover_color="#6D4C41",
            font=("Segoe UI", 12),
        )
        self.seq_clear_btn.grid(row=0, column=7, padx=4, pady=6)

        self.seq_demo_btn = ctk.CTkButton(
            editor,
            text="Load Demo",
            width=105,
            command=self._load_demo_sequence,
            fg_color="#37474F",
            hover_color="#455A64",
            font=("Segoe UI", 12),
        )
        self.seq_demo_btn.grid(row=0, column=8, padx=(4, 4), pady=6)

        self.seq_connect_btn = ctk.CTkButton(
            editor,
            text="Connect",
            width=90,
            command=self._arm_connect_selected,
            fg_color="#2E7D32",
            hover_color="#388E3C",
            font=("Segoe UI", 12),
        )
        self.seq_connect_btn.grid(row=0, column=9, padx=4, pady=6)

        self.seq_disconnect_btn = ctk.CTkButton(
            editor,
            text="Disconnect",
            width=100,
            command=self._disconnect_selected_sequence_step,
            fg_color="#6A1B9A",
            hover_color="#7B1FA2",
            font=("Segoe UI", 12),
        )
        self.seq_disconnect_btn.grid(row=0, column=10, padx=(4, 6), pady=6, sticky="w")

        canvas_wrap = ctk.CTkFrame(seq, corner_radius=8)
        canvas_wrap.grid(row=2, column=0, padx=16, pady=6, sticky="nsew")
        canvas_wrap.grid_columnconfigure(0, weight=1)
        canvas_wrap.grid_rowconfigure(0, weight=1)

        self.sequence_canvas = tk.Canvas(
            canvas_wrap,
            height=240,
            bg="#12171F",
            highlightthickness=1,
            highlightbackground="#2A2F36",
            bd=0,
        )
        self.sequence_canvas.grid(row=0, column=0, sticky="nsew")
        self.sequence_canvas.configure(scrollregion=(0, 0, 1400, 900))

        self.sequence_canvas_hbar = ctk.CTkScrollbar(
            canvas_wrap, orientation="horizontal", command=self.sequence_canvas.xview
        )
        self.sequence_canvas_hbar.grid(row=1, column=0, sticky="ew")
        self.sequence_canvas_vbar = ctk.CTkScrollbar(
            canvas_wrap, orientation="vertical", command=self.sequence_canvas.yview
        )
        self.sequence_canvas_vbar.grid(row=0, column=1, sticky="ns")
        self.sequence_canvas.configure(
            xscrollcommand=self.sequence_canvas_hbar.set,
            yscrollcommand=self.sequence_canvas_vbar.set,
        )
        self.sequence_canvas.bind("<ButtonPress-1>", self._on_sequence_canvas_press)
        self.sequence_canvas.bind("<B1-Motion>", self._on_sequence_canvas_drag)
        self.sequence_canvas.bind("<ButtonRelease-1>", self._on_sequence_canvas_release)
        self.sequence_canvas.bind("<MouseWheel>", self._on_sequence_mousewheel)
        self.sequence_canvas.bind(
            "<Button-4>", lambda _event: self._scroll_sequence_box(-1)
        )
        self.sequence_canvas.bind(
            "<Button-5>", lambda _event: self._scroll_sequence_box(1)
        )
        self.sequence_canvas.bind("<Configure>", self._on_sequence_canvas_configure)

        run_bar = ctk.CTkFrame(seq, fg_color="transparent")
        run_bar.grid(row=3, column=0, padx=12, pady=(4, 0), sticky="ew")
        run_bar.grid_columnconfigure(4, weight=1)

        self.seq_run_once_btn = ctk.CTkButton(
            run_bar,
            text="Run Once",
            width=100,
            command=lambda: self._start_sequence(loop_mode=False),
            fg_color="#1976D2",
            hover_color="#1E88E5",
            font=("Segoe UI", 12, "bold"),
        )
        self.seq_run_once_btn.grid(row=0, column=0, padx=4, pady=6)

        self.seq_run_loop_btn = ctk.CTkButton(
            run_bar,
            text="Run Loop",
            width=100,
            command=lambda: self._start_sequence(loop_mode=True),
            fg_color="#5E35B1",
            hover_color="#673AB7",
            font=("Segoe UI", 12, "bold"),
        )
        self.seq_run_loop_btn.grid(row=0, column=1, padx=4, pady=6)

        self.seq_stop_btn = ctk.CTkButton(
            run_bar,
            text="Stop",
            width=90,
            command=self._stop_sequence,
            fg_color="#C62828",
            hover_color="#D32F2F",
            font=("Segoe UI", 12, "bold"),
        )
        self.seq_stop_btn.grid(row=0, column=2, padx=4, pady=6)

        self.return_center_var = ctk.BooleanVar(value=True)
        self.return_center_chk = ctk.CTkCheckBox(
            run_bar,
            text="Return to Center after sequence",
            variable=self.return_center_var,
            font=("Segoe UI", 11),
        )
        self.return_center_chk.grid(row=0, column=3, padx=(10, 4), pady=6, sticky="w")

        self.sequence_status = ctk.CTkLabel(
            seq,
            text="Sequence idle",
            font=("Segoe UI", 11),
            text_color="#A0A0A0",
            anchor="w",
        )
        self.sequence_status.grid(row=4, column=0, padx=16, pady=(0, 12), sticky="ew")

        # Status bar
        self.status_bar = ctk.CTkLabel(
            self,
            text="Ready  |  Ctrl + / - to zoom, Ctrl+0 reset",
            font=("Segoe UI", 10),
            text_color="#777777",
            anchor="w",
        )
        self.status_bar.grid(row=1, column=0, padx=20, pady=(4, 10), sticky="ew")

        self._refresh_ports()
        self._refresh_sequence_box()

    # Polling loops
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
                    if self.sequence_running:
                        self.sequence_stop_event.set()
                        self.sequence_status.configure(
                            text="Sequence interrupted by hardware button input"
                        )
                        self.status_bar.configure(
                            text=f"Hardware buttons changed valve to {state}; sequence stopping"
                        )
                    else:
                        self.status_bar.configure(
                            text=f"Hardware buttons changed valve to {state}"
                        )
        self.after(100, self._poll_button_events)

    def _run_async(self, func, on_done):
        def _worker():
            try:
                result = func()
                self.result_queue.put(lambda: on_done(result, None))
            except Exception as exc:
                err = exc
                self.result_queue.put(lambda: on_done(None, err))

        threading.Thread(target=_worker, daemon=True).start()

    # Variant helpers
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

    # COM port helpers
    def _on_port_selected(self, _value=None):
        self._update_port_details()

    def _refresh_ports(self):
        details = ValveController.list_ports_with_details()
        self._apply_port_details(details)
        if details:
            self.status_bar.configure(text=f"Found {len(details)} serial port(s)")
        else:
            self.status_bar.configure(text="No serial ports found")

    def _apply_port_details(self, details):
        self.port_details_by_device = {item["device"]: item for item in details}
        ports = [item["device"] for item in details]
        current = self.port_var.get()

        if ports:
            self.port_menu.configure(values=ports)
            if current not in ports:
                self.port_var.set(ports[0])
        else:
            self.port_menu.configure(values=["No ports found"])
            self.port_var.set("No ports found")

        self._update_port_details()

    def _score_port(self, info):
        text = " ".join(
            [
                info.get("description", ""),
                info.get("manufacturer", ""),
                info.get("product", ""),
                info.get("hwid", ""),
            ]
        ).lower()
        score = 0
        for hint in self.ARDUINO_HINTS:
            if hint in text:
                score += 3 if hint == "arduino" else 1
        if info.get("vid") in self.ARDUINO_VIDS:
            score += 2
        return score

    def _update_port_details(self):
        device = self.port_var.get()
        info = self.port_details_by_device.get(device)
        if not info:
            self.port_info_label.configure(
                text="Select a COM port to view USB details.",
                text_color="#A0A0A0",
            )
            return

        description = info.get("description") or "Unknown device"
        manufacturer = info.get("manufacturer") or "Unknown manufacturer"
        hwid = (info.get("hwid") or "").strip()
        if len(hwid) > 70:
            hwid = hwid[:67] + "..."
        score = self._score_port(info)
        likely_text = "Likely Arduino" if score > 0 else "Unknown USB serial device"
        likely_color = "#8BC34A" if score > 0 else "#A0A0A0"

        self.port_info_label.configure(
            text=f"{device} | {description} | {manufacturer} | {likely_text} | {hwid}",
            text_color=likely_color,
        )

    def _detect_arduino_port(self):
        if self.busy or self.detecting:
            return
        if self.controller.is_connected():
            self.status_bar.configure(
                text=f"Already connected on {self.port_var.get()} (disconnect to scan)"
            )
            return

        self.detecting = True
        self.detect_btn.configure(text="Detecting...", state="disabled")
        self.status_bar.configure(text="Scanning COM ports for Arduino controller...")

        def work():
            details = ValveController.list_ports_with_details()
            ranked = sorted(details, key=self._score_port, reverse=True)
            probe_candidates = [item for item in ranked if self._score_port(item) > 0]
            if not probe_candidates:
                probe_candidates = ranked

            for item in probe_candidates:
                matched, response = ValveController.probe_port(item["device"])
                if matched:
                    return {
                        "details": details,
                        "port": item["device"],
                        "mode": "handshake",
                        "response": response,
                    }

            if ranked and self._score_port(ranked[0]) > 0:
                return {
                    "details": details,
                    "port": ranked[0]["device"],
                    "mode": "signature",
                    "response": "",
                }

            return {"details": details, "port": None, "mode": "none", "response": ""}

        def done(result, err):
            self.detecting = False
            self.detect_btn.configure(text="Detect Arduino")
            self._update_controls(connected=self.controller.is_connected())

            if err:
                self.status_bar.configure(text=f"Arduino detection failed: {err}")
                return

            self._apply_port_details(result["details"])
            port = result.get("port")
            mode = result.get("mode")
            if port:
                self.port_var.set(port)
                self._update_port_details()
                if mode == "handshake":
                    self.connected_port_label.configure(
                        text=f"Arduino COM candidate: {port} (handshake successful)",
                        text_color="#7CD67C",
                    )
                    self.status_bar.configure(
                        text=f"Arduino detected on {port} via serial handshake"
                    )
                else:
                    self.connected_port_label.configure(
                        text=f"Arduino COM candidate: {port} (USB signature match)",
                        text_color="#C8D66B",
                    )
                    self.status_bar.configure(
                        text=f"Likely Arduino port: {port} (based on USB details)"
                    )
            else:
                self.connected_port_label.configure(
                    text="Arduino COM: Not detected",
                    text_color="#8F8F8F",
                )
                self.status_bar.configure(
                    text="No Arduino response detected. Verify sketch and USB cable."
                )

        self._run_async(work, done)

    # Connection handlers
    def _toggle_connection(self):
        if self.busy or self.detecting:
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
        self._set_buttons_enabled(False)
        self._set_sequence_controls()

        def work():
            self.controller.connect(port)
            return self.controller.send_command("?")

        def done(result, err):
            self.busy = False
            if err:
                if self.controller.is_connected():
                    try:
                        self.controller.disconnect()
                    except Exception:
                        pass
                self._update_controls(connected=False)
                self.connected_port_label.configure(
                    text="Arduino COM: Not connected",
                    text_color="#8F8F8F",
                )
                self.status_bar.configure(text=f"Connection failed: {err}")
                messagebox.showerror("Connection Failed", str(err))
                return

            self._update_controls(connected=True)
            state = "C"
            if result and ":" in result:
                state = result.split(":")[-1]
            if state not in ("A", "B", "C"):
                state = "C"
            self.current_state = state
            self._show_state(state)
            self.connected_port_label.configure(
                text=f"Arduino COM: Connected on {port}",
                text_color="#7CD67C",
            )
            self.status_bar.configure(text=f"Connected on {port}")

        self._run_async(work, done)

    def _do_disconnect(self):
        self.busy = True
        self.connect_btn.configure(text="Disconnecting...", state="disabled")
        self.status_bar.configure(text="Disconnecting...")
        self._set_buttons_enabled(False)
        self._set_sequence_controls()

        def work():
            self.sequence_stop_event.set()
            if self.sequence_thread and self.sequence_thread.is_alive():
                self.sequence_thread.join(timeout=2)
            self.controller.disconnect()
            return None

        def done(_result, _err):
            self.busy = False
            self.sequence_running = False
            self.sequence_thread = None
            self.current_state = "C"
            self._update_controls(connected=False)
            self._show_state(None)
            self.connected_port_label.configure(
                text="Arduino COM: Not connected",
                text_color="#8F8F8F",
            )
            self.sequence_status.configure(text="Sequence idle")
            self.status_bar.configure(text="Disconnected")

        self._run_async(work, done)

    # Command handlers
    def _query_state(self):
        if self.busy or not self.controller.is_connected() or self.sequence_running:
            return

        self.busy = True
        self.status_bar.configure(text="Reading current valve state...")
        self._set_buttons_enabled(False)
        self._set_sequence_controls()

        def work():
            return self.controller.send_command("?")

        def done(result, err):
            self.busy = False
            self._set_buttons_enabled(self.controller.is_connected())
            self._set_sequence_controls()

            if err:
                self.status_bar.configure(text=f"State read failed: {err}")
                return

            if result and result.startswith("STATE:"):
                state = result.split(":")[1]
                if state in ("A", "B", "C"):
                    self.current_state = state
                    self._show_state(state, source="sync")
                    self.status_bar.configure(text=f"Controller reports state {state}")
                    return

            self.status_bar.configure(text=f"Unexpected response: {result}")

        self._run_async(work, done)

    def _send(self, cmd):
        if self.busy or self.sequence_running or not self.controller.is_connected():
            return

        self.busy = True
        self._set_buttons_enabled(False)
        self._set_sequence_controls()
        self.status_bar.configure(text=f"Sending {cmd}...")

        def work():
            return self.controller.send_command(cmd)

        def done(result, err):
            self.busy = False
            self._set_buttons_enabled(self.controller.is_connected())
            self._set_sequence_controls()
            if err:
                self.status_bar.configure(text=f"Command failed: {err}")
                return

            if result and result.startswith("OK:"):
                state = result.split(":")[1]
                if state in ("A", "B", "C"):
                    self.current_state = state
                    self._show_state(state, source="ui")
                self.status_bar.configure(text=f"Valve set to {state}")
            elif result and result.startswith("ERR:"):
                self.status_bar.configure(text=f"Arduino error: {result}")
            else:
                self.status_bar.configure(text=f"Unexpected response: {result}")

        self._run_async(work, done)

    # Sequence builder
    def _parse_sequence_duration(self):
        raw_duration = self.seq_duration_var.get().strip().replace(",", ".")
        try:
            duration = float(raw_duration)
        except ValueError:
            messagebox.showwarning(
                "Invalid Duration", "Duration must be a number in seconds."
            )
            return None

        if duration < 0.05 or duration > 120:
            messagebox.showwarning(
                "Invalid Duration", "Use a duration between 0.05 and 120 seconds."
            )
            return None
        return duration

    def _get_sequence_node_color(self, state):
        return {"A": "#1565C0", "B": "#E65100", "C": "#2E7D32"}.get(state, "#455A64")

    def _create_sequence_node(self, state, duration, x=None, y=None):
        node_id = self.next_sequence_node_id
        self.next_sequence_node_id += 1

        index = len(self.sequence_nodes)
        if x is None:
            x = 70 + (index % 4) * 235
        if y is None:
            y = 60 + (index // 4) * 115

        self.sequence_nodes[node_id] = {
            "state": state,
            "duration": duration,
            "x": float(x),
            "y": float(y),
            "width": 180.0,
            "height": 72.0,
            "items": [],
        }
        self._render_sequence_node(node_id)
        self._normalize_sequence_canvas_scrollregion()
        return node_id

    def _render_sequence_node(self, node_id):
        if node_id not in self.sequence_nodes:
            return
        node = self.sequence_nodes[node_id]
        for item in node["items"]:
            self.sequence_canvas.delete(item)

        x = node["x"]
        y = node["y"]
        w = node["width"]
        h = node["height"]
        state = node["state"]
        duration = node["duration"]
        is_selected = self.selected_sequence_node == node_id
        is_pending_source = self.pending_link_source == node_id
        fill = self._get_sequence_node_color(state)
        outline = (
            "#DCE4FF"
            if is_selected
            else ("#9CCC65" if is_pending_source else "#2D3645")
        )
        width = 3 if is_selected or is_pending_source else 1
        node_tag = f"seq_node_{node_id}"

        rect = self.sequence_canvas.create_rectangle(
            x,
            y,
            x + w,
            y + h,
            fill=fill,
            outline=outline,
            width=width,
            tags=("sequence_node", node_tag),
        )
        title = self.sequence_canvas.create_text(
            x + 12,
            y + 22,
            text=f"State {state}",
            fill="#FFFFFF",
            anchor="w",
            font=("Segoe UI", 11, "bold"),
            tags=("sequence_node", node_tag),
        )
        detail = self.sequence_canvas.create_text(
            x + 12,
            y + 49,
            text=f"Hold {duration:.2f}s",
            fill="#E6E6E6",
            anchor="w",
            font=("Segoe UI", 10),
            tags=("sequence_node", node_tag),
        )
        in_dot = self.sequence_canvas.create_oval(
            x - 6,
            y + h / 2 - 6,
            x + 6,
            y + h / 2 + 6,
            fill="#CFD8DC",
            outline="",
            tags=("sequence_node", node_tag),
        )
        out_dot = self.sequence_canvas.create_oval(
            x + w - 6,
            y + h / 2 - 6,
            x + w + 6,
            y + h / 2 + 6,
            fill="#CFD8DC",
            outline="",
            tags=("sequence_node", node_tag),
        )
        node["items"] = [rect, title, detail, in_dot, out_dot]
        self.sequence_canvas.tag_raise("sequence_node")

    def _node_id_from_item(self, item_id):
        for tag in self.sequence_canvas.gettags(item_id):
            if tag.startswith("seq_node_"):
                try:
                    return int(tag.split("_")[-1])
                except ValueError:
                    return None
        return None

    def _node_id_from_canvas_event(self, event):
        current = self.sequence_canvas.find_withtag("current")
        if current:
            node_id = self._node_id_from_item(current[0])
            if node_id in self.sequence_nodes:
                return node_id

        x = self.sequence_canvas.canvasx(event.x)
        y = self.sequence_canvas.canvasy(event.y)
        for node_id, node in self.sequence_nodes.items():
            if (
                node["x"] <= x <= node["x"] + node["width"]
                and node["y"] <= y <= node["y"] + node["height"]
            ):
                return node_id
        return None

    def _select_sequence_node(self, node_id):
        self.selected_sequence_node = (
            node_id if node_id in self.sequence_nodes else None
        )
        if self.selected_sequence_node:
            node = self.sequence_nodes[self.selected_sequence_node]
            self.seq_state_var.set(node["state"])
            self.seq_duration_var.set(f"{node['duration']:.2f}")
        for existing_id in list(self.sequence_nodes.keys()):
            self._render_sequence_node(existing_id)
        self._redraw_sequence_links()

    def _add_sequence_step(self):
        if self.sequence_running:
            return

        state = self.seq_state_var.get()
        duration = self._parse_sequence_duration()
        if duration is None:
            return

        node_id = self._create_sequence_node(state, duration)
        prev = self.last_added_sequence_node
        self.last_added_sequence_node = node_id
        if prev and prev in self.sequence_nodes and prev != node_id:
            self._connect_sequence_nodes(prev, node_id, quiet=True)
        self._select_sequence_node(node_id)
        self._refresh_sequence_box(keep_message=True)
        self._set_sequence_controls()
        self.sequence_status.configure(
            text=f"Added block {len(self.sequence_nodes)}: {state} for {duration:.2f}s"
        )

    def _update_selected_sequence_step(self):
        if self.sequence_running:
            return
        node_id = self.selected_sequence_node
        if not node_id or node_id not in self.sequence_nodes:
            messagebox.showinfo("No Selection", "Select a block to update first.")
            return

        duration = self._parse_sequence_duration()
        if duration is None:
            return

        state = self.seq_state_var.get()
        node = self.sequence_nodes[node_id]
        node["state"] = state
        node["duration"] = duration
        self._render_sequence_node(node_id)
        self._redraw_sequence_links()
        self._refresh_sequence_box(keep_message=True)
        self.sequence_status.configure(
            text=f"Updated selected block to {state} for {duration:.2f}s"
        )

    def _remove_last_sequence_step(self):
        if self.sequence_running or not self.sequence_nodes:
            return

        target = self.selected_sequence_node
        if not target:
            target = self.last_added_sequence_node
        if target not in self.sequence_nodes:
            target = max(self.sequence_nodes.keys())

        removed = dict(self.sequence_nodes[target])
        self._delete_sequence_node(target)
        self._refresh_sequence_box(keep_message=True)
        self._set_sequence_controls()
        self.sequence_status.configure(
            text=f"Removed block: {removed['state']} for {removed['duration']:.2f}s"
        )

    def _clear_sequence_steps(self):
        if self.sequence_running or not self.sequence_nodes:
            return
        self.sequence_canvas.delete("all")
        self.sequence_nodes.clear()
        self.sequence_links.clear()
        self.sequence_incoming.clear()
        self.selected_sequence_node = None
        self.pending_link_source = None
        self.last_added_sequence_node = None
        self.sequence_steps.clear()
        self._refresh_sequence_box()
        self._set_sequence_controls()
        self.sequence_status.configure(
            text="Sequence cleared. Add blocks, drag to move, then connect them."
        )

    def _load_demo_sequence(self):
        if self.sequence_running:
            return

        self.sequence_canvas.delete("all")
        self.sequence_nodes.clear()
        self.sequence_links.clear()
        self.sequence_incoming.clear()
        self.selected_sequence_node = None
        self.pending_link_source = None
        self.last_added_sequence_node = None

        demo = [
            ("C", 0.5),
            ("A", 1.2),
            ("C", 0.4),
            ("B", 1.2),
            ("C", 0.6),
        ]
        previous = None
        for idx, (state, duration) in enumerate(demo):
            x = 70 + idx * 220
            y = 70 + (idx % 2) * 95
            node_id = self._create_sequence_node(state, duration, x=x, y=y)
            if previous:
                self._connect_sequence_nodes(previous, node_id, quiet=True)
            previous = node_id
            self.last_added_sequence_node = node_id

        self._select_sequence_node(previous)
        self._refresh_sequence_box(keep_message=True)
        self._set_sequence_controls()
        self.sequence_status.configure(
            text="Loaded demo blocks. Drag them around and reconnect if needed."
        )

    def _delete_sequence_node(self, node_id):
        if node_id not in self.sequence_nodes:
            return

        old_target = self.sequence_links.pop(node_id, None)
        if old_target in self.sequence_incoming:
            self.sequence_incoming.pop(old_target, None)

        old_source = self.sequence_incoming.pop(node_id, None)
        if old_source is not None and self.sequence_links.get(old_source) == node_id:
            self.sequence_links.pop(old_source, None)

        for item in self.sequence_nodes[node_id]["items"]:
            self.sequence_canvas.delete(item)
        self.sequence_nodes.pop(node_id, None)

        if self.selected_sequence_node == node_id:
            self.selected_sequence_node = None
        if self.pending_link_source == node_id:
            self.pending_link_source = None
        if self.last_added_sequence_node == node_id:
            self.last_added_sequence_node = (
                max(self.sequence_nodes.keys()) if self.sequence_nodes else None
            )

        self._redraw_sequence_links()
        self._normalize_sequence_canvas_scrollregion()

    def _path_exists(self, links, start, goal):
        current = start
        seen = set()
        while current is not None and current not in seen:
            if current == goal:
                return True
            seen.add(current)
            current = links.get(current)
        return False

    def _connect_sequence_nodes(self, source_id, target_id, quiet=False):
        if source_id not in self.sequence_nodes or target_id not in self.sequence_nodes:
            return False
        if source_id == target_id:
            if not quiet:
                self.status_bar.configure(text="Cannot connect a block to itself")
            return False

        candidate = dict(self.sequence_links)
        candidate[source_id] = target_id
        for src, dst in list(candidate.items()):
            if src != source_id and dst == target_id:
                candidate.pop(src, None)

        if self._path_exists(candidate, target_id, source_id):
            if not quiet:
                self.status_bar.configure(
                    text="Connection rejected: it would create a loop"
                )
            return False

        self.sequence_links = candidate
        self.sequence_incoming = {dst: src for src, dst in self.sequence_links.items()}
        self.pending_link_source = None
        self._redraw_sequence_links()
        self._refresh_sequence_box(keep_message=True)
        if not quiet:
            self.sequence_status.configure(text="Blocks connected")
        return True

    def _arm_connect_selected(self):
        if self.sequence_running:
            return
        node_id = self.selected_sequence_node
        if not node_id or node_id not in self.sequence_nodes:
            messagebox.showinfo("No Selection", "Select a source block first.")
            return
        self.pending_link_source = node_id
        self._select_sequence_node(node_id)
        self.sequence_status.configure(
            text="Select target block to complete connection"
        )

    def _disconnect_selected_sequence_step(self):
        if self.sequence_running:
            return
        node_id = self.selected_sequence_node
        if not node_id or node_id not in self.sequence_nodes:
            return

        changed = False
        out_target = self.sequence_links.pop(node_id, None)
        if out_target is not None:
            self.sequence_incoming.pop(out_target, None)
            changed = True
        in_source = self.sequence_incoming.pop(node_id, None)
        if in_source is not None and self.sequence_links.get(in_source) == node_id:
            self.sequence_links.pop(in_source, None)
            changed = True

        if changed:
            self._redraw_sequence_links()
            self._refresh_sequence_box(keep_message=True)
            self.sequence_status.configure(text="Disconnected selected block")

    def _ordered_sequence_node_ids(self):
        if not self.sequence_nodes:
            return []
        if not self.sequence_links:
            return None
        if len(self.sequence_links) != len(self.sequence_nodes) - 1:
            return None

        incoming = {dst: src for src, dst in self.sequence_links.items()}
        starts = [node_id for node_id in self.sequence_nodes if node_id not in incoming]
        if len(starts) != 1:
            return None

        ordered = []
        seen = set()
        current = starts[0]
        while current is not None:
            if current in seen:
                return None
            seen.add(current)
            ordered.append(current)
            current = self.sequence_links.get(current)

        if len(ordered) != len(self.sequence_nodes):
            return None
        return ordered

    def _refresh_sequence_box(self, keep_message=False):
        ordered_ids = self._ordered_sequence_node_ids()
        mode = "connected"
        if ordered_ids is None:
            mode = "layout"
            ordered_ids = sorted(
                self.sequence_nodes.keys(),
                key=lambda node_id: (
                    self.sequence_nodes[node_id]["y"],
                    self.sequence_nodes[node_id]["x"],
                    node_id,
                ),
            )

        self.sequence_steps = [
            {
                "state": self.sequence_nodes[node_id]["state"],
                "duration": self.sequence_nodes[node_id]["duration"],
            }
            for node_id in ordered_ids
        ]
        self.sequence_incoming = {
            dst: src
            for src, dst in self.sequence_links.items()
            if src in self.sequence_nodes and dst in self.sequence_nodes
        }

        self._redraw_sequence_links()
        self._normalize_sequence_canvas_scrollregion()

        self.sequence_canvas.delete("empty_hint")
        if not self.sequence_nodes:
            self.sequence_canvas.create_text(
                70,
                46,
                text=(
                    "Add blocks, drag them, and connect source -> target.\n"
                    "If blocks are not fully connected, run order follows top-to-bottom layout."
                ),
                fill="#B0BEC5",
                anchor="w",
                font=("Segoe UI", 11),
                tags=("empty_hint",),
            )

        if keep_message or self.sequence_running:
            return
        if not self.sequence_steps:
            self.sequence_status.configure(
                text="Sequence idle. Add blocks, drag to move, and connect them."
            )
        elif mode == "connected":
            self.sequence_status.configure(
                text=f"{len(self.sequence_steps)} blocks ready (connected order)"
            )
        else:
            self.sequence_status.configure(
                text=(
                    f"{len(self.sequence_steps)} blocks ready (layout order). "
                    "Connect all blocks to lock execution order."
                )
            )

    def _redraw_sequence_links(self):
        self.sequence_canvas.delete("sequence_link")
        for source_id, target_id in list(self.sequence_links.items()):
            if (
                source_id not in self.sequence_nodes
                or target_id not in self.sequence_nodes
            ):
                self.sequence_links.pop(source_id, None)
                continue
            source = self.sequence_nodes[source_id]
            target = self.sequence_nodes[target_id]
            sx = source["x"] + source["width"] + 6
            sy = source["y"] + source["height"] / 2
            tx = target["x"] - 6
            ty = target["y"] + target["height"] / 2

            self.sequence_canvas.create_line(
                sx,
                sy,
                tx,
                ty,
                fill="#A5D6A7",
                width=2,
                arrow=tk.LAST,
                arrowshape=(10, 12, 4),
                smooth=True,
                tags=("sequence_link",),
            )
        self.sequence_canvas.tag_lower("sequence_link")

    def _normalize_sequence_canvas_scrollregion(self):
        bbox = self.sequence_canvas.bbox("all")
        width = max(self.sequence_canvas.winfo_width(), 1)
        height = max(self.sequence_canvas.winfo_height(), 1)
        if not bbox:
            self.sequence_canvas.configure(scrollregion=(0, 0, width, height))
            return
        x1, y1, x2, y2 = bbox
        right = max(int(x2 + 120), width)
        bottom = max(int(y2 + 120), height)
        self.sequence_canvas.configure(scrollregion=(0, 0, right, bottom))

    def _on_sequence_canvas_configure(self, _event=None):
        self._normalize_sequence_canvas_scrollregion()

    def _on_sequence_canvas_press(self, event):
        if self.sequence_running:
            return "break"
        node_id = self._node_id_from_canvas_event(event)
        if node_id is None:
            self.pending_link_source = None
            self._select_sequence_node(None)
            self.drag_node_id = None
            return "break"

        self._select_sequence_node(node_id)
        if (
            self.pending_link_source
            and self.pending_link_source in self.sequence_nodes
            and self.pending_link_source != node_id
        ):
            self._connect_sequence_nodes(self.pending_link_source, node_id)

        self.drag_node_id = node_id
        self.drag_last_x = self.sequence_canvas.canvasx(event.x)
        self.drag_last_y = self.sequence_canvas.canvasy(event.y)
        return "break"

    def _on_sequence_canvas_drag(self, event):
        if self.sequence_running or not self.drag_node_id:
            return "break"
        if self.drag_node_id not in self.sequence_nodes:
            self.drag_node_id = None
            return "break"

        x = self.sequence_canvas.canvasx(event.x)
        y = self.sequence_canvas.canvasy(event.y)
        dx = x - self.drag_last_x
        dy = y - self.drag_last_y
        if dx == 0 and dy == 0:
            return "break"

        node = self.sequence_nodes[self.drag_node_id]
        node["x"] = max(20, node["x"] + dx)
        node["y"] = max(20, node["y"] + dy)
        for item in node["items"]:
            self.sequence_canvas.move(item, dx, dy)
        self.drag_last_x = x
        self.drag_last_y = y

        self._redraw_sequence_links()
        self._normalize_sequence_canvas_scrollregion()
        return "break"

    def _on_sequence_canvas_release(self, _event):
        self.drag_node_id = None
        self._refresh_sequence_box(keep_message=True)
        return "break"

    def _start_sequence(self, loop_mode):
        if self.busy or self.detecting or self.sequence_running:
            return
        self._refresh_sequence_box(keep_message=True)
        if not self.controller.is_connected():
            messagebox.showwarning(
                "Not Connected", "Connect to Arduino before running a sequence."
            )
            return
        if not self.sequence_steps:
            messagebox.showwarning("No Steps", "Add at least one sequence step first.")
            return

        steps = [dict(step) for step in self.sequence_steps]
        return_to_center = bool(self.return_center_var.get())
        self.sequence_total_steps = len(steps)
        self.sequence_running = True
        self.sequence_stop_event.clear()

        mode_text = "looping" if loop_mode else "single run"
        self.sequence_status.configure(text=f"Sequence started ({mode_text})")
        self.status_bar.configure(text=f"Running sequence ({mode_text})...")
        self._set_buttons_enabled(False)
        self._set_sequence_controls()

        self.sequence_thread = threading.Thread(
            target=self._run_sequence_worker,
            args=(steps, loop_mode, return_to_center),
            daemon=True,
        )
        self.sequence_thread.start()

    def _run_sequence_worker(self, steps, loop_mode, return_to_center):
        loops = 0
        stopped = False
        error = None

        try:
            while not self.sequence_stop_event.is_set():
                loops += 1
                for step_index, step in enumerate(steps, start=1):
                    if self.sequence_stop_event.is_set():
                        break

                    result = self.controller.send_command(step["state"])
                    if not result or not result.startswith("OK:"):
                        raise RuntimeError(f"Unexpected response: {result}")

                    state = result.split(":")[1]
                    duration = step["duration"]
                    self.result_queue.put(
                        lambda s=state, i=step_index, d=duration, l=loops: (
                            self._on_sequence_step(s, i, d, l)
                        )
                    )

                    start = time.time()
                    while time.time() - start < duration:
                        if self.sequence_stop_event.wait(0.05):
                            break

                if not loop_mode:
                    break

            stopped = self.sequence_stop_event.is_set()
            should_center = stopped or return_to_center
            if should_center and self.controller.is_connected():
                center_result = self.controller.send_command("C")
                if center_result and center_result.startswith("OK:"):
                    self.result_queue.put(
                        lambda: self._show_state("C", source="sequence")
                    )
        except Exception as exc:
            error = exc
        finally:
            self.result_queue.put(
                lambda err=error, count=loops, was_stopped=stopped, is_loop=loop_mode: (
                    self._finish_sequence(err, count, was_stopped, is_loop)
                )
            )

    def _on_sequence_step(self, state, step_index, duration, loop_count):
        self.current_state = state
        self._show_state(state, source="sequence")
        self.sequence_status.configure(
            text=(
                f"Loop {loop_count} | Step {step_index}/{self.sequence_total_steps}: "
                f"{state} for {duration:.2f}s"
            )
        )
        self.status_bar.configure(
            text=f"Sequence running: loop {loop_count}, step {step_index}/{self.sequence_total_steps}"
        )

    def _finish_sequence(self, error, loops, was_stopped, is_loop):
        self.sequence_running = False
        self.sequence_thread = None
        self._set_buttons_enabled(self.controller.is_connected() and not self.busy)
        self._set_sequence_controls()

        if not self.controller.is_connected():
            self.sequence_status.configure(text="Sequence idle")
            return

        if error:
            self.sequence_status.configure(text=f"Sequence error: {error}")
            self.status_bar.configure(text=f"Sequence error: {error}")
            return

        if was_stopped:
            self.sequence_status.configure(text="Sequence stopped")
            self.status_bar.configure(text="Sequence stopped")
            return

        loop_word = "cycle" if loops == 1 else "cycles"
        mode = "loop run" if is_loop else "single run"
        self.sequence_status.configure(
            text=f"Sequence completed ({loops} {loop_word}, {mode})"
        )
        self.status_bar.configure(text=f"Sequence completed ({loops} {loop_word})")

    def _stop_sequence(self):
        if not self.sequence_running:
            return
        self.sequence_stop_event.set()
        self.sequence_status.configure(text="Stopping sequence...")
        self.status_bar.configure(text="Stopping sequence...")
        self._set_sequence_controls()

    # UI helpers
    def _update_controls(self, connected):
        self._set_buttons_enabled(connected and not self.busy)

        if connected:
            self.connect_btn.configure(
                text="Disconnect",
                state="normal" if not self.busy else "disabled",
                fg_color="#C62828",
                hover_color="#E53935",
            )
            self.port_menu.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")
            self.detect_btn.configure(state="disabled")
        else:
            self.connect_btn.configure(
                text="Connect",
                state="normal" if not self.busy else "disabled",
                fg_color="#2E7D32",
                hover_color="#388E3C",
            )
            self.port_menu.configure(state="normal")
            self.refresh_btn.configure(state="normal")
            if self.detecting:
                self.detect_btn.configure(state="disabled")
            else:
                self.detect_btn.configure(state="normal")

        self._set_sequence_controls()

    def _set_buttons_enabled(self, enabled):
        state = "normal" if enabled and not self.sequence_running else "disabled"
        self.btn_a.configure(state=state)
        self.btn_b.configure(state=state)
        self.btn_c.configure(state=state)
        self.read_state_btn.configure(state=state)

    def _set_sequence_controls(self):
        connected = self.controller.is_connected()
        has_steps = bool(self.sequence_nodes)
        editable = not self.sequence_running

        edit_state = "normal" if editable else "disabled"
        self.seq_state_menu.configure(state=edit_state)
        self.seq_duration_entry.configure(state=edit_state)
        self.seq_add_btn.configure(state=edit_state)
        self.seq_update_btn.configure(state=edit_state)
        self.seq_remove_btn.configure(state=edit_state)
        self.seq_clear_btn.configure(state=edit_state)
        self.seq_demo_btn.configure(state=edit_state)
        self.seq_connect_btn.configure(state=edit_state)
        self.seq_disconnect_btn.configure(state=edit_state)
        self.return_center_chk.configure(state=edit_state)

        run_enabled = connected and has_steps and not self.busy and not self.detecting
        self.seq_run_once_btn.configure(state="normal" if run_enabled else "disabled")
        self.seq_run_loop_btn.configure(state="normal" if run_enabled else "disabled")
        self.seq_stop_btn.configure(
            state="normal" if self.sequence_running else "disabled"
        )

    def _bind_zoom_shortcuts(self):
        self.bind_all("<Control-plus>", lambda _event: self._zoom_in(), add="+")
        self.bind_all("<Control-equal>", lambda _event: self._zoom_in(), add="+")
        self.bind_all("<Control-KP_Add>", lambda _event: self._zoom_in(), add="+")
        self.bind_all("<Control-minus>", lambda _event: self._zoom_out(), add="+")
        self.bind_all("<Control-KP_Subtract>", lambda _event: self._zoom_out(), add="+")
        self.bind_all("<Control-0>", lambda _event: self._zoom_reset(), add="+")

    def _zoom_in(self):
        self._set_manual_zoom(self._manual_zoom + self.USER_ZOOM_STEP)

    def _zoom_out(self):
        self._set_manual_zoom(self._manual_zoom - self.USER_ZOOM_STEP)

    def _zoom_reset(self):
        self._set_manual_zoom(1.0)

    def _set_manual_zoom(self, value):
        clamped = max(self.USER_ZOOM_MIN, min(self.USER_ZOOM_MAX, value))
        if abs(clamped - self._manual_zoom) < 0.001:
            return
        self._manual_zoom = clamped
        self._apply_ui_scaling(force=True)
        self.status_bar.configure(
            text=f"Zoom set to {int(round(self._manual_zoom * 100))}%"
        )

    def _refresh_zoom_label(self):
        if hasattr(self, "zoom_label"):
            self.zoom_label.configure(text=f"{int(round(self._manual_zoom * 100))}%")

    def _on_window_resize(self, event):
        if event.widget is not self:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._apply_ui_scaling)

    def _apply_ui_scaling(self, force=False):
        self._resize_job = None
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)

        width_scale = width / self.BASE_WIDTH
        height_scale = height / self.BASE_HEIGHT
        self._fit_scale = min(width_scale, height_scale) * self.BASE_UI_SCALE
        self._fit_scale = max(
            self.MIN_UI_SCALE, min(self.MAX_UI_SCALE, self._fit_scale)
        )
        target_scale = self._fit_scale * self._manual_zoom
        target_scale = max(0.70, min(1.80, target_scale))

        if force or abs(target_scale - self._ui_scale) >= 0.02:
            self._ui_scale = target_scale
            ctk.set_widget_scaling(self._ui_scale)
            self._refresh_zoom_label()

    def _on_page_mousewheel(self, event):
        ctrl_pressed = bool(event.state & 0x0004)
        if ctrl_pressed:
            if event.delta > 0:
                self._zoom_in()
            elif event.delta < 0:
                self._zoom_out()
            return "break"

        if hasattr(self, "sequence_canvas") and event.widget == self.sequence_canvas:
            return None

        units = 0
        if event.delta > 0:
            units = -1
        elif event.delta < 0:
            units = 1
        if units != 0:
            return self._scroll_page(units)
        return None

    def _scroll_page(self, units):
        if hasattr(self, "page") and hasattr(self.page, "_parent_canvas"):
            self.page._parent_canvas.yview_scroll(units, "units")
            return "break"
        return None

    def _on_sequence_mousewheel(self, event):
        ctrl_pressed = bool(event.state & 0x0004)
        if ctrl_pressed:
            if event.delta > 0:
                self._zoom_in()
            elif event.delta < 0:
                self._zoom_out()
            return "break"

        units = 0
        if event.delta > 0:
            units = -1
        elif event.delta < 0:
            units = 1
        if units != 0:
            self._scroll_sequence_box(units)
        return "break"

    def _scroll_sequence_box(self, units):
        self.sequence_canvas.yview_scroll(units, "units")
        return "break"

    def _show_state(self, state, source=None):
        if state is None:
            self.state_label.configure(text="DISCONNECTED", text_color="#777777")
            self.state_detail.configure(text="Select a COM port and connect")
            self.source_label.configure(text="")
            return

        names = {"A": "POSITION A", "B": "POSITION B", "C": "CENTER"}
        color = self.STATE_COLORS.get(state, "#777777")
        self.state_label.configure(text=names.get(state, state), text_color=color)
        self.state_detail.configure(text=self._get_state_label(state))

        if source == "button":
            self.source_label.configure(text="Changed via hardware buttons")
        elif source == "ui":
            self.source_label.configure(text="Changed via UI")
        elif source == "sequence":
            self.source_label.configure(text="Changed via sequence")
        elif source == "sync":
            self.source_label.configure(text="Synced from controller")
        else:
            self.source_label.configure(text="")

    def on_close(self):
        self.sequence_stop_event.set()
        if self.sequence_thread and self.sequence_thread.is_alive():
            self.sequence_thread.join(timeout=1.5)
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
