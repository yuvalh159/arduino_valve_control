# Airtec 4V120 - M5 Valve Controller

Control an Airtec 4V120 (5/2 way bistable pneumatic solenoid valve, 12VDC) from a
Python desktop app through an Arduino and a 2-channel relay module.

The 4V120 is a **bistable (latching)** valve with two positions — it stays in
the last commanded position even when both solenoids are de-energised.

**Features:**
- Professional dark-themed Python UI (customtkinter, Tailwind slate palette)
- Always-visible state banner — changes color to match current valve position
- 2 hardware buttons for standalone control (A / B)
- EEPROM state persistence — survives brown-out resets from relay power draw
- EMI-guarded button inputs — relay switching noise can't trigger false presses
- COM port auto-detection with single-reset probe handoff (no double solenoid click)
- Table-based sequence builder with reordering, run-once, and loop modes

---

## Completed Improvements

- **External 5V power for relay module** — Relay module VCC is now powered
  from a separate 5V source, eliminating Arduino brown-outs from relay coil
  current draw. IN1, IN2, and GND remain connected to the Arduino.

## Future Improvements

- **MOSFET drivers instead of relay module** — A logic-level MOSFET module
  (e.g. IRF520) draws almost no current from the Arduino and switches
  silently with no EMI. This would eliminate the remaining relay noise
  entirely.

---

## Parts List

| # | Item | Specs / Notes |
|---|------|---------------|
| 1 | Arduino Uno (or Nano/Leonardo) | Any board with 4 digital pins + USB serial |
| 2 | 2-Channel 5V Relay Module | 5V coil, switching side rated 12VDC / 1A or higher |
| 3 | Airtec 4V120 - M5 | 5/2 way bistable solenoid valve, **12VDC coils** |
| 4 | 12VDC Power Supply | 1A minimum (each solenoid coil draws ~300-500mA) |
| 5 | USB Cable | Type-B (Uno), Mini-USB (Nano), or Micro-USB (Leonardo) |
| 6 | 2x Momentary Pushbuttons | For direct hardware A/B control (optional) |
| 7 | Jumper Wires | Male-to-female for relay connections |
| 8 | PC with Python 3.8+ | Windows / Linux / macOS |

---

## 1. Understanding the Valve

### What is a 5/2 Valve?

5 ports, 2 positions. Two solenoid coils switch between two air routing states.
The valve is **bistable** — it latches in the last commanded position and holds
it mechanically even when both solenoids are turned off.

### Ports

```
         ┌──────────────────────────────────────┐
         │          Airtec 4V120 - M5            │
         │                                       │
  Sol A ─┤                                       ├─ Sol B
         │         ┌────────┬────────┐           │
         │         │  Pos A │  Pos B │           │
         │         └───┬────┴───┬────┘           │
         └─────────────┼────────┼────────────────┘
                       │        │
                  Port 2 (A)  Port 1 (P)  Port 4 (B)
                       │                     │
                  Port 3 (EA)           Port 5 (EB)
```

| Port | Label | Function |
|------|-------|----------|
| 1 | **P** | Pressure inlet — connect to compressed air supply |
| 2 | **A** | Working output A — to actuator side A |
| 3 | **EA** | Exhaust A |
| 4 | **B** | Working output B — to actuator side B |
| 5 | **EB** | Exhaust B |

### Positions

| Position | Solenoid A | Solenoid B | Air Flow |
|----------|-----------|-----------|----------|
| **A** | ON | OFF | P → A, B → EB (exhaust) |
| **B** | OFF | ON | P → B, A → EA (exhaust) |

> **Bistable behaviour:** When both solenoids are off, the valve stays in
> whichever position it was last switched to. There is no spring return and
> no center position.

---

## 2. Full Wiring Schematic

### System Overview

```
  ┌─────────┐    USB     ┌───────────────────────────────────────────┐
  │         │◄───────────►│              Arduino Uno                  │
  │   PC    │             │                                           │
  │ Python  │             │   Pin 2/3 ── Buttons A/B ── GND           │
  │   UI    │             │   Pin 7 ──────────► Relay Module IN1      │
  │         │             │   Pin 8 ──────────► Relay Module IN2      │
  │         │             │   (Ext 5V) ───────► Relay Module VCC      │
  └─────────┘             │   GND ────────┬──► Relay Module GND      │
                          └───────────────┼──────────────────────────┘
                                          │
                                     COMMON GROUND
                                          │
  ┌───────────────────────────────────────┼──────────────────────────┐
  │            2-Channel Relay Module      │                          │
  │                                        │                          │
  │  ┌─────────────┐    ┌─────────────┐   │                          │
  │  │   Relay 1   │    │   Relay 2   │   │                          │
  │  │             │    │             │   │                          │
  │  │  COM ◄── 12V+    │  COM ◄── 12V+  │                          │
  │  │  NO  ──► wire    │  NO  ──► wire   │                          │
  │  │  NC  (unused)│    │  NC  (unused)│  │                          │
  │  └──────┬──────┘    └──────┬──────┘   │                          │
  │         │                   │          │                          │
  └─────────┼───────────────────┼──────────┘                          │
            │                   │                                      │
            ▼                   ▼                                      │
  ┌─────────────────────────────────────────────┐                     │
  │          Airtec 4V120 - M5                  │                     │
  │                                              │                     │
  │   Solenoid A (+) ◄── Relay 1 NO             │                     │
  │   Solenoid A (-) ──────────────────► 12V GND ┼──► COMMON GROUND   │
  │                                              │                     │
  │   Solenoid B (+) ◄── Relay 2 NO             │                     │
  │   Solenoid B (-) ──────────────────► 12V GND ┼──► COMMON GROUND   │
  │                                              │                     │
  └──────────────────────────────────────────────┘                     │
                                                                       │
  ┌──────────────────────────────┐                                     │
  │      12VDC Power Supply      │                                     │
  │                              │                                     │
  │   (+) ──► Relay 1 COM       │                                     │
  │       ──► Relay 2 COM       │                                     │
  │                              │                                     │
  │  GND  ──► Solenoid A (-)    │                                     │
  │       ──► Solenoid B (-)    ├─────────────────► COMMON GROUND ◄───┘
  │       ──► Arduino GND       │
  │                              │
  └──────────────────────────────┘
```

### Step-by-Step Wiring

#### Step A: Arduino → Relay Module (low-voltage / signal side)

Use jumper wires. These carry only 5V logic signals.

| Arduino Pin | → | Relay Module Pin | Purpose |
|-------------|---|-----------------|---------|
| **External 5V** | → | **VCC** | Powers relay module coils from a separate 5V source (not Arduino 5V) |
| **GND** | → | **GND** | Shared ground |
| **Pin 7** | → | **IN1** | Controls Relay 1 (Solenoid A) |
| **Pin 8** | → | **IN2** | Controls Relay 2 (Solenoid B) |

#### Step A2: Direct Hardware Buttons (optional but recommended)

Use two momentary pushbuttons for direct valve position selection.
No resistor needed — the Arduino uses internal pullups.

| Arduino Pin | Button | Other Side |
|-------------|--------|------------|
| **Pin 2** | **A button** | **GND** |
| **Pin 3** | **B button** | **GND** |

Pressing a button sets the valve directly to that position.
Works with or without the PC connected. The UI auto-syncs when it detects
a button event.

#### Step B: 12V PSU → Relay Module (high-voltage / power side)

Each relay has 3 screw terminals: **COM**, **NO** (Normally Open), **NC** (Normally Closed).
We use **COM** and **NO** only.

| Connection | From | → | To |
|-----------|------|---|-----|
| Power in | **12V PSU (+)** | → | **Relay 1 COM** |
| Power in | **12V PSU (+)** | → | **Relay 2 COM** |
| Signal out | **Relay 1 NO** | → | **Solenoid A (+)** |
| Signal out | **Relay 2 NO** | → | **Solenoid B (+)** |

> **Why NO (Normally Open)?** When Arduino loses power or resets, relays
> de-energize and the NO contacts open → solenoids turn OFF. The valve
> stays in its last position (bistable latch) — a safe default.

#### Step C: Ground Connections (CRITICAL)

All three grounds **must** be tied together:

| Wire | From | → | To |
|------|------|---|-----|
| 1 | **Solenoid A (-)** | → | **12V PSU GND** |
| 2 | **Solenoid B (-)** | → | **12V PSU GND** |
| 3 | **12V PSU GND** | → | **Arduino GND** |

> **WARNING**: If you skip the common ground (wire 3), the relay module
> will behave erratically or not switch at all.

#### Step D: Pneumatic Connections

| Valve Port | Connect To |
|-----------|-----------|
| **Port 1 (P)** | Compressed air supply (with regulator) |
| **Port 2 (A)** | Actuator side A |
| **Port 4 (B)** | Actuator side B |
| **Port 3 (EA)** | Exhaust (open or with silencer) |
| **Port 5 (EB)** | Exhaust (open or with silencer) |

---

## 3. Relay Module Reference

Most 2-channel relay modules look like this:

```
    LOW-VOLTAGE SIDE              HIGH-VOLTAGE SIDE
    (Arduino)                     (12V + Solenoids)
    ┌──────────────┐              ┌──────────────────┐
    │ VCC  (5V)    │              │ Relay 1:         │
    │ GND          │              │   COM ◄── 12V+   │
    │ IN1  (Pin 7) │              │   NO  ──► Sol A+ │
    │ IN2  (Pin 8) │              │   NC  (unused)   │
    │              │              │                  │
    │              │              │ Relay 2:         │
    │              │              │   COM ◄── 12V+   │
    │              │              │   NO  ──► Sol B+ │
    │              │              │   NC  (unused)   │
    └──────────────┘              └──────────────────┘
```

**Terminal meanings:**
- **COM** = Common — always connected to one of the other two
- **NO** = Normally Open — disconnected when relay is OFF, connected when ON
- **NC** = Normally Closed — connected when relay is OFF, disconnected when ON

We use **NO** so that power-off = solenoids off = valve holds last position (safe).

---

## 4. Software Setup

### Step 1: Upload Arduino Sketch

1. Open **Arduino IDE**
2. Open file: `arduino/valve_controller/valve_controller.ino`
3. **Tools → Board → Arduino Uno** (or your board)
4. **Tools → Port → COMx** (your Arduino's port)
5. Click **Upload** (arrow button)
6. Open **Serial Monitor** at **9600 baud** — you should see `READY`
7. Test commands manually:
   - Type `A` + Enter → response: `OK:A` (Relay 1 clicks ON — valve to Position A)
   - Type `B` + Enter → response: `OK:B` (Relay 2 clicks ON — valve to Position B)
   - Type `?` + Enter → response: `STATE:A` or `STATE:B` (current position)
   - Press hardware A/B button → Arduino sends `BTN:A` or `BTN:B`
8. **Close Serial Monitor** before running the Python UI

### Step 2: Install Python Dependencies

```
pip install -r requirements.txt
```

This installs:
- `pyserial` — serial communication with Arduino
- `customtkinter` — modern dark-themed UI framework

### Step 3: Run the UI

```
python valve_ui.py
```

### Step 4: Using the UI

1. Click **Refresh** to list ports, or **Detect Arduino** to auto-select a likely COM port
   - Detect keeps the serial connection open so the following Connect reuses
     it — the Arduino only resets once (no double solenoid click)
2. Click **Connect** — instant if Detect already found the port, otherwise
   wait ~2 seconds for Arduino handshake
3. (Optional) click **Read State** to sync UI with controller state
4. Use manual controls:

| Button | Color | Action |
|--------|-------|--------|
| **POSITION A** | Blue | Energizes Solenoid A: P → A, B → Exhaust |
| **POSITION B** | Orange | Energizes Solenoid B: P → B, A → Exhaust |

5. Use the **Sequence Builder** to automate repeatable patterns:
   - Pick a state (`A` or `B`) and a duration, then click **+ Add**
   - Steps appear in a table — select a row and use **Edit** to change it
   - Reorder with **Up** / **Down** buttons
   - **Remove** deletes the selected step, **Clear** empties the table
   - **Demo** loads a sample 4-step A/B alternating sequence
   - **Run Once** executes the table top-to-bottom, **Loop** repeats until stopped
   - **Stop** halts the sequence (valve stays in last position)
6. The fixed **state banner** at the top always shows the current valve
   position with a color-coded background (blue for A / orange for B)
7. Use `Ctrl +`, `Ctrl -`, `Ctrl 0`, and mouse wheel scrolling to fit UI to your screen
8. Click **Disconnect** or close the window — the valve stays in its last
   position (bistable latch, no solenoid actuation on disconnect)

---

## 5. Safety

| Rule | Why |
|------|-----|
| Both solenoids never ON simultaneously | `applyOutputs()` always sets one LOW before the other HIGH |
| EEPROM state persistence | If the relay power draw resets the Arduino, it restores the last state on boot |
| Single-reset probe handoff | Detect Arduino keeps the serial connection open so Connect reuses it — only one Arduino reset |
| Bistable latch on disconnect | Closing the UI just drops serial — valve stays in last position, no unexpected movement |
| NO relay terminals used | If Arduino resets or loses power, relays open → solenoids OFF → valve holds position |
| Onboard LED (Pin 13) mirrors state | Quick visual — LED ON = a solenoid is energised |
| EMI guard on buttons (400ms) | Relay switching noise can't trigger false button presses |
| Hardware A/B buttons have 200ms debounce | Prevents accidental multi-triggering |
| Buttons work without PC connected | Arduino runs standalone — useful for field testing |

---

## 6. Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| No COM ports in UI | USB driver missing | Install CH340 (clone boards) or FTDI driver |
| Detect Arduino finds no match | Sketch not running / unsupported USB adapter | Upload sketch again, then use Refresh and connect manually |
| "Connection Failed" | Port already in use | Close Arduino IDE Serial Monitor first |
| Relay doesn't click | No signal from Arduino | Check IN1/IN2 wiring, check VCC is 5V |
| Relay clicks but valve doesn't move | Solenoid not powered | Check 12V PSU is ON, check COM/NO wiring |
| Only one solenoid works | One relay wired wrong | Check both COM terminals get 12V+ |
| Valve moves but wrong direction | A and B swapped | Swap Pin 7 / Pin 8 wires, or swap Relay 1 NO / Relay 2 NO |
| UI shows "No response" | Arduino not running sketch | Re-upload the sketch, check baud is 9600 |
| Sequence stops unexpectedly | Hardware button press or serial interruption | Check status bar message, reconnect if needed |
| Relay chatters / flickers | Missing common ground | Connect 12V PSU GND to Arduino GND |
| Arduino resets when relay switches | Relay module drawing too much from 5V | Power relay VCC from external 5V (see Completed Improvements) |
| Hardware A/B buttons do nothing | Button wired to wrong pin or not to GND | Verify Pin 2=A, Pin 3=B, both using INPUT_PULLUP wiring |
| Buttons trigger wrong state after relay click | EMI from relay switching | EMI guard (400ms) should handle this; increase `EMI_GUARD_MS` if needed |

---

## 7. Quick Reference Card

**Print this and keep it at your workbench:**

```
═══════════════════════════════════════════════════════
  AIRTEC 4V120 - WIRING QUICK REFERENCE (12VDC)
═══════════════════════════════════════════════════════

  ARDUINO              RELAY MODULE         VALVE / PSU
  ─────────────────    ────────────────     ─────────────
  Pin 2  ── Btn A ── GND
  Pin 3  ── Btn B ── GND
  Pin 7  ──────────►   IN1
  Pin 8  ──────────►   IN2
  Ext 5V ──────────►   VCC  (separate 5V source, NOT Arduino 5V)
  GND    ──────────►   GND
                       Relay 1 COM  ◄────  12V PSU (+)
                       Relay 1 NO   ────►  Sol A (+)
                       Relay 2 COM  ◄────  12V PSU (+)
                       Relay 2 NO   ────►  Sol B (+)
                                           Sol A (-)  ──► 12V GND
                                           Sol B (-)  ──► 12V GND
  GND    ◄─────────────────────────────────────────────── 12V GND

═══════════════════════════════════════════════════════
  COMMANDS (Serial 9600 baud)
  A = Position A  |  B = Position B  |  ? = Read state
═══════════════════════════════════════════════════════
```

---

## File Structure

```
arduino_valve_control/
├── arduino/
│   └── valve_controller/
│       └── valve_controller.ino    ← Upload to Arduino (once)
├── valve_ui.py                     ← Run on PC: python valve_ui.py
├── requirements.txt                ← Python deps: pip install -r requirements.txt
└── README.md                       ← This file
```
