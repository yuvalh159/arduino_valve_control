# Airtec 4V130 C/E/P - M5 Valve Controller

Control an Airtec 4V130 (5/3 way pneumatic solenoid valve, 12VDC) from a
Python desktop app through an Arduino Uno and a 2-channel relay module.

Supports all three center-position variants: **C** (Closed), **E** (Exhaust), **P** (Pressure).

---

## Parts List

| # | Item | Specs / Notes |
|---|------|---------------|
| 1 | Arduino Uno (or Nano/Leonardo) | Any board with 2 digital pins + USB serial |
| 2 | 2-Channel 5V Relay Module | 5V coil, switching side rated 12VDC / 1A or higher |
| 3 | Airtec 4V130 C/E/P - M5 | 5/3 way solenoid valve, **12VDC coils** |
| 4 | 12VDC Power Supply | 1A minimum (each solenoid coil draws ~300-500mA) |
| 5 | USB Cable | Type-B (Uno), Mini-USB (Nano), or Micro-USB (Leonardo) |
| 6 | Momentary Pushbutton | For manual cycle control (optional) |
| 7 | Jumper Wires | Male-to-female for relay connections |
| 8 | PC with Python 3.8+ | Windows / Linux / macOS |

---

## 1. Understanding the Valve

### What is a 5/3 Valve?

5 ports, 3 positions. Two solenoid coils select between three air routing states.

### Ports

```
         ┌──────────────────────────────────────┐
         │          Airtec 4V130 - M5            │
         │                                       │
  Sol A ─┤                                       ├─ Sol B
         │     ┌────────┬────────┬────────┐      │
         │     │  Pos A │ Center │  Pos B │      │
         │     └───┬────┴───┬────┴───┬────┘      │
         └─────────┼────────┼────────┼───────────┘
                   │        │        │
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
| **Center** | OFF | OFF | Depends on variant (see below) |
| **B** | OFF | ON | P → B, A → EA (exhaust) |

### Center Variants

All three variants are **electrically identical** — same wiring, same control.
The difference is purely mechanical (internal spool design):

| Variant | Model Name | Center Behaviour |
|---------|-----------|-----------------|
| **C** | 4V130**C**-M5 | All ports blocked — no air flows, actuator holds position |
| **E** | 4V130**E**-M5 | A and B vent to exhaust — actuator depressurises |
| **P** | 4V130**P**-M5 | Pressure sent to A and B — actuator locks in place |

> **Not sure which variant you have?** Connect everything, put the valve in
> Position A, then press CENTER and observe what your actuator does.
> - Holds position → **C** (Closed)
> - Goes limp / retracts → **E** (Exhaust)
> - Locks stiff / doesn't move → **P** (Pressure)

---

## 2. Full Wiring Schematic

### System Overview

```
  ┌─────────┐    USB     ┌───────────────────────────────────────────┐
  │         │◄───────────►│              Arduino Uno                  │
  │   PC    │             │                                           │
  │ Python  │             │   Pin 2 ── Button ── GND (cycle C→A→B→C) │
  │   UI    │             │   Pin 7 ──────────► Relay Module IN1      │
  │         │             │   Pin 8 ──────────► Relay Module IN2      │
  │         │             │   5V ─────────────► Relay Module VCC      │
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
  │          Airtec 4V130 C/E/P - M5            │                     │
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
| **5V** | → | **VCC** | Powers relay module coils |
| **GND** | → | **GND** | Shared ground |
| **Pin 7** | → | **IN1** | Controls Relay 1 (Solenoid A) |
| **Pin 8** | → | **IN2** | Controls Relay 2 (Solenoid B) |

#### Step A2: Cycle Button (optional but recommended)

Wire a momentary pushbutton between **Pin 2** and **GND**.
No resistor needed — the Arduino uses its internal pullup.

```
  Arduino Pin 2 ──── Button ──── Arduino GND
```

Each press cycles the valve: **Center → A → B → Center → ...**
Works with or without the PC connected. The UI auto-syncs when it detects
a button press.

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
> de-energize and the NO contacts open → solenoids turn OFF → safe state.

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

We use **NO** so that power-off = solenoids off = safe.

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
   - Type `A` + Enter → response: `OK:A` (Relay 1 clicks ON)
   - Type `B` + Enter → response: `OK:B` (Relay 2 clicks ON)
   - Type `C` + Enter → response: `OK:C` (both relays OFF)
   - Type `?` + Enter → response: `STATE:C` (current state)
   - Press the hardware button → Arduino sends `BTN:<state>` (e.g. `BTN:A`)
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

1. Select your **COM port** from the dropdown (click Refresh if needed)
2. Select your **valve variant** (C / E / P) from the Model dropdown
3. Click **Connect** — wait ~2 seconds for Arduino handshake
4. Use the three control buttons:

| Button | Color | Action |
|--------|-------|--------|
| **POSITION A** | Blue | Energizes Solenoid A: P → A, B → Exhaust |
| **CENTER** | Green | Both solenoids off: center position |
| **POSITION B** | Orange | Energizes Solenoid B: P → B, A → Exhaust |

5. The large status indicator shows the current valve state
6. Click **Disconnect** or close the window — the valve safely returns to CENTER

---

## 5. Safety

| Rule | Why |
|------|-----|
| Both solenoids never ON simultaneously | Arduino always turns one OFF before the other ON (5ms delay) |
| Startup defaults to CENTER | Both pins LOW on boot → both relays OFF → both solenoids OFF |
| Disconnect sends CENTER first | Closing the UI de-energises both solenoids before dropping serial |
| NO relay terminals used | If Arduino resets or loses power, relays open → solenoids OFF |
| Onboard LED (Pin 13) mirrors state | Quick visual — LED ON = a solenoid is energised |
| Hardware button has 200ms debounce | Prevents accidental double-presses |
| Button works without PC connected | Arduino runs standalone — useful for field testing |

---

## 6. Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| No COM ports in UI | USB driver missing | Install CH340 (clone boards) or FTDI driver |
| "Connection Failed" | Port already in use | Close Arduino IDE Serial Monitor first |
| Relay doesn't click | No signal from Arduino | Check IN1/IN2 wiring, check VCC is 5V |
| Relay clicks but valve doesn't move | Solenoid not powered | Check 12V PSU is ON, check COM/NO wiring |
| Only one solenoid works | One relay wired wrong | Check both COM terminals get 12V+ |
| Valve moves but wrong direction | A and B swapped | Swap Pin 7 / Pin 8 wires, or swap Relay 1 NO / Relay 2 NO |
| UI shows "No response" | Arduino not running sketch | Re-upload the sketch, check baud is 9600 |
| Relay chatters / flickers | Missing common ground | Connect 12V PSU GND to Arduino GND |

---

## 7. Quick Reference Card

**Print this and keep it at your workbench:**

```
═══════════════════════════════════════════════════════
  AIRTEC 4V130 - WIRING QUICK REFERENCE (12VDC)
═══════════════════════════════════════════════════════

  ARDUINO              RELAY MODULE         VALVE / PSU
  ─────────────────    ────────────────     ─────────────
  Pin 2  ── Button ── GND  (cycle C→A→B→C)
  Pin 7  ──────────►   IN1
  Pin 8  ──────────►   IN2
  5V     ──────────►   VCC
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
  A = Position A  |  B = Position B  |  C = Center
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
