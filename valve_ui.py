"""
Python UI for controlling Airtec 4V120-M5 valve via Arduino.
The 4V120 is a 5/2 bistable (latching) valve with two positions: A and B.
Supports UI commands, hardware A/B button updates, COM auto-detection, and sequences.
Requires: pyserial, customtkinter
Usage:    python valve_ui.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, ttk
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

    def connect(self, port, baudrate=9600, timeout=0.1, existing_ser=None):
        with self.lock:
            if self.ser and self.ser.is_open:
                self._stop_reader()
                self.ser.close()
            if existing_ser and existing_ser.is_open:
                self.ser = existing_ser
                self.ser.timeout = timeout
            else:
                self.ser = serial.Serial(port, baudrate, timeout=timeout)
                time.sleep(2)
            self.ser.reset_input_buffer()
            self._start_reader()

    def disconnect(self):
        with self.lock:
            self._stop_reader()
            if self.ser and self.ser.is_open:
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

                if line == "READY":
                    continue

                if line.startswith("ERR:"):
                    return line
                if line.startswith(expected_prefix):
                    return line
                if line.startswith(("OK:", "STATE:")):
                    return line

        raise TimeoutError("No response from Arduino")

    def get_button_events(self):
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
        """Try a short handshake. Returns (matched, response_text, ser_or_None).

        On success the serial connection is kept open so the caller can
        hand it straight to connect() and avoid a second Arduino reset.
        """
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
                    return True, line, ser
                if line.startswith("READY") and not saw_ready:
                    saw_ready = True
                    ser.write(b"?")

            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            return False, "No STATE response", None
        except Exception as exc:
            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            return False, str(exc), None


class ValveApp(ctk.CTk):
    BASE_WIDTH = 920
    BASE_HEIGHT = 780
    MIN_WIDTH = 780
    MIN_HEIGHT = 620
    BASE_UI_SCALE = 0.95
    MIN_UI_SCALE = 0.85
    MAX_UI_SCALE = 1.08
    USER_ZOOM_MIN = 0.75
    USER_ZOOM_MAX = 1.60
    USER_ZOOM_STEP = 0.10

    STATE_COLORS = {"A": "#60a5fa", "B": "#fb923c"}
    STATE_BANNER_BG = {"A": "#1e3a5f", "B": "#431407"}
    BANNER_OFF_BG = "#1e293b"

    CARD = "#1e293b"
    CARD_BORDER = "#334155"
    TEXT_SEC = "#94a3b8"
    TEXT_MUTED = "#64748b"

    ARDUINO_HINTS = ("arduino", "ch340", "wch", "cp210", "ftdi", "usb serial")
    ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403}

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Airtec 4V120  -  Valve Controller")
        self.geometry(f"{self.BASE_WIDTH}x{self.BASE_HEIGHT}")
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resizable(True, True)
        self._resize_job = None
        self._fit_scale = self.BASE_UI_SCALE
        self._manual_zoom = 1.0
        self._ui_scale = self.BASE_UI_SCALE
        ctk.set_widget_scaling(self._ui_scale)

        self.controller = ValveController()
        self.current_state = "A"
        self.busy = False
        self.detecting = False

        self.result_queue = queue.Queue()
        self.port_details_by_device = {}

        self.sequence_steps = []
        self.sequence_running = False
        self.sequence_stop_event = threading.Event()
        self.sequence_thread = None
        self.sequence_total_steps = 0

        self._probed_serial = None
        self._probed_port = None

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

    def _card(self, parent, **kw):
        return ctk.CTkFrame(
            parent,
            corner_radius=10,
            fg_color=kw.pop("fg_color", self.CARD),
            border_width=1,
            border_color=kw.pop("border_color", self.CARD_BORDER),
            **kw,
        )

    def _section_label(self, parent, text):
        return ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=("Segoe UI", 10, "bold"),
            text_color=self.TEXT_MUTED,
            anchor="w",
        )

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Fixed top: state banner ──
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        title_bar = ctk.CTkFrame(top, corner_radius=0, fg_color=self.CARD, height=42)
        title_bar.grid(row=0, column=0, sticky="ew")
        title_bar.grid_columnconfigure(0, weight=1)
        title_bar.grid_propagate(False)

        ctk.CTkLabel(
            title_bar,
            text="Airtec 4V120 Valve Controller",
            font=("Segoe UI", 14, "bold"),
            text_color="#e2e8f0",
        ).grid(row=0, column=0, padx=20, pady=10, sticky="w")

        zoom_bar = ctk.CTkFrame(title_bar, fg_color="transparent")
        zoom_bar.grid(row=0, column=1, padx=(0, 16), pady=6, sticky="e")

        self.zoom_out_btn = ctk.CTkButton(
            zoom_bar,
            text="\u2212",
            width=28,
            height=26,
            corner_radius=6,
            command=self._zoom_out,
            font=("Segoe UI", 13, "bold"),
            fg_color="#334155",
            hover_color="#475569",
        )
        self.zoom_out_btn.grid(row=0, column=0, padx=(0, 3))

        self.zoom_label = ctk.CTkLabel(
            zoom_bar,
            text="100%",
            width=48,
            anchor="center",
            font=("Segoe UI", 10),
            text_color=self.TEXT_SEC,
        )
        self.zoom_label.grid(row=0, column=1, padx=2)

        self.zoom_in_btn = ctk.CTkButton(
            zoom_bar,
            text="+",
            width=28,
            height=26,
            corner_radius=6,
            command=self._zoom_in,
            font=("Segoe UI", 13, "bold"),
            fg_color="#334155",
            hover_color="#475569",
        )
        self.zoom_in_btn.grid(row=0, column=2, padx=(3, 0))

        self.state_banner = ctk.CTkFrame(
            top,
            corner_radius=0,
            fg_color=self.BANNER_OFF_BG,
            height=88,
        )
        self.state_banner.grid(row=1, column=0, sticky="ew")
        self.state_banner.grid_columnconfigure(0, weight=1)
        self.state_banner.grid_propagate(False)

        self.state_label = ctk.CTkLabel(
            self.state_banner,
            text="DISCONNECTED",
            font=("Segoe UI", 26, "bold"),
            text_color=self.TEXT_MUTED,
        )
        self.state_label.grid(row=0, column=0, pady=(14, 0))

        self.state_detail = ctk.CTkLabel(
            self.state_banner,
            text="Select a COM port and connect",
            font=("Segoe UI", 12),
            text_color=self.TEXT_SEC,
        )
        self.state_detail.grid(row=1, column=0, pady=(0, 2))

        self.source_label = ctk.CTkLabel(
            self.state_banner,
            text="",
            font=("Segoe UI", 10),
            text_color=self.TEXT_MUTED,
        )
        self.source_label.grid(row=2, column=0, pady=(0, 8))

        # ── Scrollable body ──
        self.page = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color="transparent",
        )
        self.page.grid(row=1, column=0, sticky="nsew")
        self.page.grid_columnconfigure(0, weight=1)
        self.page.grid_rowconfigure(2, weight=1)

        pad_x = 20
        gap = 8

        # ── Connection card ──
        conn = self._card(self.page)
        conn.grid(row=0, column=0, padx=pad_x, pady=(12, gap), sticky="ew")
        conn.grid_columnconfigure(1, weight=1)

        self._section_label(conn, "Connection").grid(
            row=0,
            column=0,
            columnspan=5,
            padx=16,
            pady=(10, 4),
            sticky="w",
        )

        ctk.CTkLabel(
            conn,
            text="COM Port",
            font=("Segoe UI", 12),
            text_color="#cbd5e1",
        ).grid(row=1, column=0, padx=(16, 8), pady=(4, 8), sticky="w")

        self.port_var = ctk.StringVar(value="No ports found")
        self.port_menu = ctk.CTkOptionMenu(
            conn,
            variable=self.port_var,
            values=["No ports found"],
            command=self._on_port_selected,
            width=145,
            font=("Segoe UI", 12),
            corner_radius=8,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#64748b",
        )
        self.port_menu.grid(row=1, column=1, padx=4, pady=(4, 8), sticky="w")

        btn_r = 8

        self.refresh_btn = ctk.CTkButton(
            conn,
            text="Refresh",
            width=85,
            corner_radius=btn_r,
            command=self._refresh_ports,
            font=("Segoe UI", 12),
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.refresh_btn.grid(row=1, column=2, padx=3, pady=(4, 8))

        self.detect_btn = ctk.CTkButton(
            conn,
            text="Detect Arduino",
            width=125,
            corner_radius=btn_r,
            command=self._detect_arduino_port,
            font=("Segoe UI", 12),
            fg_color="#312e81",
            hover_color="#3730a3",
            text_color="#c7d2fe",
        )
        self.detect_btn.grid(row=1, column=3, padx=3, pady=(4, 8))

        self.connect_btn = ctk.CTkButton(
            conn,
            text="Connect",
            width=110,
            corner_radius=btn_r,
            command=self._toggle_connection,
            font=("Segoe UI", 12, "bold"),
            fg_color="#166534",
            hover_color="#15803d",
            text_color="#bbf7d0",
        )
        self.connect_btn.grid(row=1, column=4, padx=(3, 16), pady=(4, 8))

        self.port_info_label = ctk.CTkLabel(
            conn,
            text="Select a COM port to view USB details.",
            font=("Segoe UI", 10),
            text_color=self.TEXT_MUTED,
            anchor="w",
        )
        self.port_info_label.grid(
            row=2,
            column=0,
            columnspan=5,
            padx=16,
            pady=(0, 2),
            sticky="ew",
        )

        self.connected_port_label = ctk.CTkLabel(
            conn,
            text="Not connected",
            font=("Segoe UI", 10, "bold"),
            text_color=self.TEXT_MUTED,
            anchor="w",
        )
        self.connected_port_label.grid(
            row=3,
            column=0,
            columnspan=5,
            padx=16,
            pady=(0, 10),
            sticky="ew",
        )

        # ── Valve Control card ──
        ctrl = self._card(self.page)
        ctrl.grid(row=1, column=0, padx=pad_x, pady=gap, sticky="ew")
        ctrl.grid_columnconfigure((0, 1), weight=1)

        header_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        header_row.grid(
            row=0, column=0, columnspan=2, padx=16, pady=(10, 6), sticky="ew"
        )
        header_row.grid_columnconfigure(0, weight=1)

        self._section_label(header_row, "Valve Control").grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.read_state_btn = ctk.CTkButton(
            header_row,
            text="Read State",
            width=95,
            corner_radius=btn_r,
            command=self._query_state,
            font=("Segoe UI", 12),
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.read_state_btn.grid(row=0, column=1, sticky="e")

        btn_h = 66
        btn_font = ("Segoe UI", 13, "bold")

        self.btn_a = ctk.CTkButton(
            ctrl,
            text="POSITION A\nSolenoid A",
            height=btn_h,
            corner_radius=10,
            fg_color="#1d4ed8",
            hover_color="#2563eb",
            font=btn_font,
            command=lambda: self._send("A"),
        )
        self.btn_a.grid(row=1, column=0, padx=(16, 5), pady=(6, 16), sticky="ew")

        self.btn_b = ctk.CTkButton(
            ctrl,
            text="POSITION B\nSolenoid B",
            height=btn_h,
            corner_radius=10,
            fg_color="#c2410c",
            hover_color="#ea580c",
            font=btn_font,
            command=lambda: self._send("B"),
        )
        self.btn_b.grid(row=1, column=1, padx=(5, 16), pady=(6, 16), sticky="ew")

        # ── Sequence Builder card ──
        seq = self._card(self.page)
        seq.grid(row=2, column=0, padx=pad_x, pady=(gap, 16), sticky="nsew")
        seq.grid_columnconfigure(0, weight=1)
        seq.grid_rowconfigure(2, weight=1)

        self._section_label(seq, "Sequence Builder").grid(
            row=0,
            column=0,
            padx=16,
            pady=(10, 6),
            sticky="w",
        )

        editor = ctk.CTkFrame(seq, fg_color="transparent")
        editor.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="ew")

        ctk.CTkLabel(
            editor,
            text="State",
            font=("Segoe UI", 12),
            text_color="#cbd5e1",
        ).grid(row=0, column=0, padx=(4, 6), pady=5)

        self.seq_state_var = ctk.StringVar(value="A")
        self.seq_state_menu = ctk.CTkOptionMenu(
            editor,
            variable=self.seq_state_var,
            values=["A", "B"],
            width=70,
            corner_radius=8,
            font=("Segoe UI", 12),
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#64748b",
        )
        self.seq_state_menu.grid(row=0, column=1, padx=3, pady=5)

        ctk.CTkLabel(
            editor,
            text="Duration (s)",
            font=("Segoe UI", 12),
            text_color="#cbd5e1",
        ).grid(row=0, column=2, padx=(10, 6), pady=5)

        self.seq_duration_var = ctk.StringVar(value="1.0")
        self.seq_duration_entry = ctk.CTkEntry(
            editor,
            textvariable=self.seq_duration_var,
            width=75,
            corner_radius=8,
            font=("Segoe UI", 12),
            fg_color="#0f172a",
            border_color="#334155",
        )
        self.seq_duration_entry.grid(row=0, column=3, padx=3, pady=5)

        sb = lambda **k: ctk.CTkButton(
            editor, corner_radius=btn_r, font=("Segoe UI", 11), **k
        )

        self.seq_add_btn = sb(
            text="+ Add",
            width=72,
            command=self._add_sequence_step,
            fg_color="#0f766e",
            hover_color="#0d9488",
            text_color="#ccfbf1",
        )
        self.seq_add_btn.grid(row=0, column=4, padx=3, pady=5)

        self.seq_edit_btn = sb(
            text="Edit",
            width=62,
            command=self._edit_selected_step,
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.seq_edit_btn.grid(row=0, column=5, padx=3, pady=5)

        self.seq_remove_btn = sb(
            text="Remove",
            width=72,
            command=self._remove_sequence_step,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            text_color="#fecaca",
        )
        self.seq_remove_btn.grid(row=0, column=6, padx=3, pady=5)

        self.seq_clear_btn = sb(
            text="Clear",
            width=62,
            command=self._clear_sequence_steps,
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.seq_clear_btn.grid(row=0, column=7, padx=3, pady=5)

        self.seq_demo_btn = sb(
            text="Demo",
            width=62,
            command=self._load_demo_sequence,
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.seq_demo_btn.grid(row=0, column=8, padx=3, pady=5)

        # Sequence table
        table_frame = ctk.CTkFrame(
            seq,
            corner_radius=8,
            fg_color="#0f172a",
            border_width=1,
            border_color="#1e293b",
        )
        table_frame.grid(row=2, column=0, padx=16, pady=4, sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Seq.Treeview",
            background="#0f172a",
            foreground="#e2e8f0",
            fieldbackground="#0f172a",
            borderwidth=0,
            font=("Segoe UI", 11),
            rowheight=32,
        )
        style.configure(
            "Seq.Treeview.Heading",
            background="#1e293b",
            foreground="#94a3b8",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Seq.Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )

        self.seq_table = ttk.Treeview(
            table_frame,
            columns=("num", "state", "duration"),
            show="headings",
            selectmode="browse",
            style="Seq.Treeview",
            height=8,
        )
        self.seq_table.heading("num", text="#")
        self.seq_table.heading("state", text="State")
        self.seq_table.heading("duration", text="Duration (s)")
        self.seq_table.column("num", width=50, anchor="center", stretch=False)
        self.seq_table.column("state", width=150, anchor="center")
        self.seq_table.column("duration", width=150, anchor="center")
        self.seq_table.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.seq_table.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.seq_table.configure(yscrollcommand=scrollbar.set)
        self.seq_table.bind("<<TreeviewSelect>>", self._on_table_select)

        self.seq_table.tag_configure("state_A", background="#172554")
        self.seq_table.tag_configure("state_B", background="#431407")

        # Move + run bar
        action_bar = ctk.CTkFrame(seq, fg_color="transparent")
        action_bar.grid(row=3, column=0, padx=12, pady=(4, 2), sticky="ew")
        action_bar.grid_columnconfigure(2, weight=1)

        self.seq_up_btn = ctk.CTkButton(
            action_bar,
            text="\u25b2  Up",
            width=70,
            corner_radius=btn_r,
            command=self._move_step_up,
            font=("Segoe UI", 11),
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.seq_up_btn.grid(row=0, column=0, padx=(4, 3), pady=4)

        self.seq_down_btn = ctk.CTkButton(
            action_bar,
            text="\u25bc  Down",
            width=78,
            corner_radius=btn_r,
            command=self._move_step_down,
            font=("Segoe UI", 11),
            fg_color="#334155",
            hover_color="#475569",
            text_color="#cbd5e1",
        )
        self.seq_down_btn.grid(row=0, column=1, padx=3, pady=4)

        spacer = ctk.CTkLabel(action_bar, text="")
        spacer.grid(row=0, column=2)

        self.seq_run_once_btn = ctk.CTkButton(
            action_bar,
            text="\u25b6  Run Once",
            width=105,
            corner_radius=btn_r,
            command=lambda: self._start_sequence(loop_mode=False),
            font=("Segoe UI", 12, "bold"),
            fg_color="#1d4ed8",
            hover_color="#2563eb",
        )
        self.seq_run_once_btn.grid(row=0, column=3, padx=3, pady=4)

        self.seq_run_loop_btn = ctk.CTkButton(
            action_bar,
            text="\u21bb  Loop",
            width=88,
            corner_radius=btn_r,
            command=lambda: self._start_sequence(loop_mode=True),
            font=("Segoe UI", 12, "bold"),
            fg_color="#6d28d9",
            hover_color="#7c3aed",
        )
        self.seq_run_loop_btn.grid(row=0, column=4, padx=3, pady=4)

        self.seq_stop_btn = ctk.CTkButton(
            action_bar,
            text="\u25a0  Stop",
            width=80,
            corner_radius=btn_r,
            command=self._stop_sequence,
            font=("Segoe UI", 12, "bold"),
            fg_color="#991b1b",
            hover_color="#b91c1c",
        )
        self.seq_stop_btn.grid(row=0, column=5, padx=(3, 4), pady=4)

        bottom_bar = ctk.CTkFrame(seq, fg_color="transparent")
        bottom_bar.grid(row=4, column=0, padx=16, pady=(0, 10), sticky="ew")
        bottom_bar.grid_columnconfigure(0, weight=1)

        self.sequence_status = ctk.CTkLabel(
            bottom_bar,
            text="Sequence idle",
            font=("Segoe UI", 10),
            text_color=self.TEXT_MUTED,
            anchor="e",
        )
        self.sequence_status.grid(row=0, column=0, pady=2, sticky="e")

        # ── Status bar (fixed at bottom) ──
        self.status_bar = ctk.CTkLabel(
            self,
            text="Ready  \u2502  Ctrl +/\u2212 zoom, Ctrl+0 reset",
            font=("Segoe UI", 10),
            text_color=self.TEXT_MUTED,
            anchor="w",
            height=24,
        )
        self.status_bar.grid(row=2, column=0, padx=20, pady=(2, 8), sticky="ew")

        self._refresh_ports()
        self._refresh_sequence_table()

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
                if state in ("A", "B"):
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

    def _get_state_label(self, state):
        return {
            "A": "Position A  \u2014  P \u2192 A, B exhaust",
            "B": "Position B  \u2014  P \u2192 B, A exhaust",
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
        likely_color = "#4ade80" if score > 0 else self.TEXT_MUTED

        self.port_info_label.configure(
            text=f"{device}  \u2502  {description}  \u2502  {manufacturer}  \u2502  {likely_text}",
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
                matched, response, ser = ValveController.probe_port(item["device"])
                if matched:
                    return {
                        "details": details,
                        "port": item["device"],
                        "mode": "handshake",
                        "response": response,
                        "ser": ser,
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

            self._close_probed_serial()
            probed_ser = result.get("ser")
            if probed_ser and probed_ser.is_open:
                self._probed_serial = probed_ser
                self._probed_port = result.get("port")

            self._apply_port_details(result["details"])
            port = result.get("port")
            mode = result.get("mode")
            if port:
                self.port_var.set(port)
                self._update_port_details()
                if mode == "handshake":
                    self.connected_port_label.configure(
                        text=f"\u2714  Arduino detected on {port} (handshake verified)",
                        text_color="#4ade80",
                    )
                    self.status_bar.configure(
                        text=f"Arduino detected on {port} via serial handshake"
                    )
                else:
                    self.connected_port_label.configure(
                        text=f"\u2248  Likely Arduino on {port} (USB signature match)",
                        text_color="#fbbf24",
                    )
                    self.status_bar.configure(
                        text=f"Likely Arduino port: {port} (based on USB details)"
                    )
            else:
                self.connected_port_label.configure(
                    text="No Arduino detected",
                    text_color=self.TEXT_MUTED,
                )
                self.status_bar.configure(
                    text="No Arduino response detected. Verify sketch and USB cable."
                )

        self._run_async(work, done)

    # Connection handlers
    def _close_probed_serial(self):
        if self._probed_serial:
            try:
                if self._probed_serial.is_open:
                    self._probed_serial.close()
            except Exception:
                pass
            self._probed_serial = None
            self._probed_port = None

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

        probed_ser = None
        if (
            self._probed_serial
            and self._probed_serial.is_open
            and self._probed_port == port
        ):
            probed_ser = self._probed_serial
            self._probed_serial = None
            self._probed_port = None
        else:
            self._close_probed_serial()

        self.busy = True
        self.connect_btn.configure(text="Connecting...", state="disabled")
        self.status_bar.configure(text=f"Connecting to {port}...")
        self._set_buttons_enabled(False)
        self._set_sequence_controls()

        def work():
            self.controller.connect(port, existing_ser=probed_ser)
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
                    text="Not connected",
                    text_color=self.TEXT_MUTED,
                )
                self.status_bar.configure(text=f"Connection failed: {err}")
                messagebox.showerror("Connection Failed", str(err))
                return

            self._update_controls(connected=True)
            state = "A"
            if result and ":" in result:
                state = result.split(":")[-1]
            if state not in ("A", "B"):
                state = "A"
            self.current_state = state
            self._show_state(state)
            self.connected_port_label.configure(
                text=f"\u25cf  Connected on {port}",
                text_color="#4ade80",
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
            self._update_controls(connected=False)
            self._show_state(None)
            self.connected_port_label.configure(
                text="Not connected",
                text_color=self.TEXT_MUTED,
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
                if state in ("A", "B"):
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
                if state in ("A", "B"):
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

    def _add_sequence_step(self):
        if self.sequence_running:
            return
        state = self.seq_state_var.get()
        duration = self._parse_sequence_duration()
        if duration is None:
            return
        self.sequence_steps.append({"state": state, "duration": duration})
        self._refresh_sequence_table()
        items = self.seq_table.get_children()
        if items:
            self.seq_table.selection_set(items[-1])
            self.seq_table.see(items[-1])
        self._set_sequence_controls()
        self.sequence_status.configure(
            text=f"Added step {len(self.sequence_steps)}: {state} for {duration:.2f}s"
        )

    def _edit_selected_step(self):
        if self.sequence_running:
            return
        sel = self.seq_table.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a step to edit first.")
            return
        idx = self.seq_table.index(sel[0])
        duration = self._parse_sequence_duration()
        if duration is None:
            return
        state = self.seq_state_var.get()
        self.sequence_steps[idx] = {"state": state, "duration": duration}
        self._refresh_sequence_table()
        items = self.seq_table.get_children()
        self.seq_table.selection_set(items[idx])
        self.sequence_status.configure(
            text=f"Updated step {idx + 1} to {state} for {duration:.2f}s"
        )

    def _remove_sequence_step(self):
        if self.sequence_running or not self.sequence_steps:
            return
        sel = self.seq_table.selection()
        if sel:
            idx = self.seq_table.index(sel[0])
        else:
            idx = len(self.sequence_steps) - 1
        removed = self.sequence_steps.pop(idx)
        self._refresh_sequence_table()
        items = self.seq_table.get_children()
        if items:
            new_idx = min(idx, len(items) - 1)
            self.seq_table.selection_set(items[new_idx])
        self._set_sequence_controls()
        self.sequence_status.configure(
            text=f"Removed step: {removed['state']} for {removed['duration']:.2f}s"
        )

    def _move_step_up(self):
        if self.sequence_running:
            return
        sel = self.seq_table.selection()
        if not sel:
            return
        idx = self.seq_table.index(sel[0])
        if idx == 0:
            return
        self.sequence_steps[idx - 1], self.sequence_steps[idx] = (
            self.sequence_steps[idx],
            self.sequence_steps[idx - 1],
        )
        self._refresh_sequence_table()
        items = self.seq_table.get_children()
        self.seq_table.selection_set(items[idx - 1])
        self.seq_table.see(items[idx - 1])

    def _move_step_down(self):
        if self.sequence_running:
            return
        sel = self.seq_table.selection()
        if not sel:
            return
        idx = self.seq_table.index(sel[0])
        if idx >= len(self.sequence_steps) - 1:
            return
        self.sequence_steps[idx], self.sequence_steps[idx + 1] = (
            self.sequence_steps[idx + 1],
            self.sequence_steps[idx],
        )
        self._refresh_sequence_table()
        items = self.seq_table.get_children()
        self.seq_table.selection_set(items[idx + 1])
        self.seq_table.see(items[idx + 1])

    def _clear_sequence_steps(self):
        if self.sequence_running or not self.sequence_steps:
            return
        self.sequence_steps.clear()
        self._refresh_sequence_table()
        self._set_sequence_controls()
        self.sequence_status.configure(text="Sequence cleared")

    def _load_demo_sequence(self):
        if self.sequence_running:
            return
        self.sequence_steps = [
            {"state": "A", "duration": 1.0},
            {"state": "B", "duration": 1.0},
            {"state": "A", "duration": 0.5},
            {"state": "B", "duration": 0.5},
        ]
        self._refresh_sequence_table()
        self._set_sequence_controls()
        self.sequence_status.configure(text="Loaded demo sequence (4 steps)")

    def _refresh_sequence_table(self):
        for item in self.seq_table.get_children():
            self.seq_table.delete(item)
        for i, step in enumerate(self.sequence_steps):
            tag = f"state_{step['state']}"
            self.seq_table.insert(
                "",
                "end",
                values=(i + 1, step["state"], f"{step['duration']:.2f}"),
                tags=(tag,),
            )

    def _on_table_select(self, _event=None):
        sel = self.seq_table.selection()
        if not sel:
            return
        idx = self.seq_table.index(sel[0])
        if 0 <= idx < len(self.sequence_steps):
            step = self.sequence_steps[idx]
            self.seq_state_var.set(step["state"])
            self.seq_duration_var.set(f"{step['duration']:.2f}")

    def _start_sequence(self, loop_mode):
        if self.busy or self.detecting or self.sequence_running:
            return
        if not self.controller.is_connected():
            messagebox.showwarning(
                "Not Connected", "Connect to Arduino before running a sequence."
            )
            return
        if not self.sequence_steps:
            messagebox.showwarning("No Steps", "Add at least one sequence step first.")
            return

        steps = [dict(step) for step in self.sequence_steps]
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
            args=(steps, loop_mode),
            daemon=True,
        )
        self.sequence_thread.start()

    def _run_sequence_worker(self, steps, loop_mode):
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
                fg_color="#991b1b",
                hover_color="#b91c1c",
                text_color="#fecaca",
            )
            self.port_menu.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")
            self.detect_btn.configure(state="disabled")
        else:
            self.connect_btn.configure(
                text="Connect",
                state="normal" if not self.busy else "disabled",
                fg_color="#166534",
                hover_color="#15803d",
                text_color="#bbf7d0",
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
        self.read_state_btn.configure(state=state)

    def _set_sequence_controls(self):
        connected = self.controller.is_connected()
        has_steps = bool(self.sequence_steps)
        editable = not self.sequence_running

        edit_state = "normal" if editable else "disabled"
        self.seq_state_menu.configure(state=edit_state)
        self.seq_duration_entry.configure(state=edit_state)
        self.seq_add_btn.configure(state=edit_state)
        self.seq_edit_btn.configure(state=edit_state)
        self.seq_remove_btn.configure(state=edit_state)
        self.seq_clear_btn.configure(state=edit_state)
        self.seq_demo_btn.configure(state=edit_state)
        self.seq_up_btn.configure(state=edit_state)
        self.seq_down_btn.configure(state=edit_state)

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

        if hasattr(self, "seq_table") and event.widget is self.seq_table:
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

    def _show_state(self, state, source=None):
        if state is None:
            self.state_banner.configure(fg_color=self.BANNER_OFF_BG)
            self.state_label.configure(text="DISCONNECTED", text_color=self.TEXT_MUTED)
            self.state_detail.configure(text="Select a COM port and connect")
            self.source_label.configure(text="")
            return

        bg = self.STATE_BANNER_BG.get(state, self.BANNER_OFF_BG)
        self.state_banner.configure(fg_color=bg)

        names = {"A": "POSITION A", "B": "POSITION B"}
        color = self.STATE_COLORS.get(state, self.TEXT_MUTED)
        self.state_label.configure(text=names.get(state, state), text_color=color)
        self.state_detail.configure(text=self._get_state_label(state))

        source_text = {
            "button": "Changed via hardware buttons",
            "ui": "Changed via UI",
            "sequence": "Changed via sequence",
            "sync": "Synced from controller",
        }
        self.source_label.configure(text=source_text.get(source, ""))

    def on_close(self):
        self._close_probed_serial()
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
