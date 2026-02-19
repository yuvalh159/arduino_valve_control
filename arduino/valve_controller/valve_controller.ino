// Arduino controller for Airtec 4V130C-M5 (5/3 solenoid valve, 12VDC)
// Serial commands (9600 baud): A = Pos A, B = Pos B, C = Center, ? = Status
// Pin 7 = Relay 1 (Solenoid A), Pin 8 = Relay 2 (Solenoid B)
// Pin 2 = Button A, Pin 3 = Button B, Pin 4 = Button C (all to GND)
//
// NOTE: State is saved to EEPROM so the valve recovers after a reset.
// If the relay module causes brown-out resets, power it from an external
// 5V source instead of the Arduino's 5V pin.

#include <EEPROM.h>

const int SOL_A_PIN = 7;
const int SOL_B_PIN = 8;
const int LED_PIN   = 13;
const int BTN_A_PIN = 2;
const int BTN_B_PIN = 3;
const int BTN_C_PIN = 4;

const int BTN_PINS[3]    = {BTN_A_PIN, BTN_B_PIN, BTN_C_PIN};
const char BTN_TARGETS[3] = {'A', 'B', 'C'};

const int EEPROM_ADDR = 0;

const unsigned long EMI_GUARD_MS = 400;
const unsigned long DEBOUNCE_MS  = 200;

char currentState = 'C';
bool btnPrev[3]   = {HIGH, HIGH, HIGH};
unsigned long btnLastPress[3] = {0, 0, 0};
unsigned long lastChangeTime  = 0;

void applyOutputs(char state) {
  if (state == 'A') {
    digitalWrite(SOL_A_PIN, HIGH);
    digitalWrite(SOL_B_PIN, LOW);
    digitalWrite(LED_PIN,   HIGH);
  } else if (state == 'B') {
    digitalWrite(SOL_A_PIN, LOW);
    digitalWrite(SOL_B_PIN, HIGH);
    digitalWrite(LED_PIN,   HIGH);
  } else {
    digitalWrite(SOL_A_PIN, LOW);
    digitalWrite(SOL_B_PIN, LOW);
    digitalWrite(LED_PIN,   LOW);
  }
}

void setValve(char pos) {
  // Save to EEPROM first (survives reset caused by relay power draw).
  if (pos != currentState) {
    EEPROM.update(EEPROM_ADDR, pos);
  }
  currentState = pos;
  applyOutputs(pos);
  lastChangeTime = millis();
}

char loadSavedState() {
  char saved = EEPROM.read(EEPROM_ADDR);
  if (saved == 'A' || saved == 'B' || saved == 'C') return saved;
  return 'C';
}

void setup() {
  pinMode(SOL_A_PIN, OUTPUT);
  pinMode(SOL_B_PIN, OUTPUT);
  pinMode(LED_PIN,   OUTPUT);
  pinMode(BTN_A_PIN, INPUT_PULLUP);
  pinMode(BTN_B_PIN, INPUT_PULLUP);
  pinMode(BTN_C_PIN, INPUT_PULLUP);

  // Restore saved state (handles brown-out recovery).
  char saved = loadSavedState();
  currentState = saved;
  applyOutputs(saved);

  Serial.begin(9600);
  Serial.println("READY");
}

void checkButtons() {
  unsigned long now = millis();

  if (now - lastChangeTime < EMI_GUARD_MS) {
    for (int i = 0; i < 3; i++) btnPrev[i] = digitalRead(BTN_PINS[i]);
    return;
  }

  for (int i = 0; i < 3; i++) {
    bool reading = digitalRead(BTN_PINS[i]);

    if (reading == LOW && btnPrev[i] == HIGH && (now - btnLastPress[i] > DEBOUNCE_MS)) {
      btnLastPress[i] = now;
      setValve(BTN_TARGETS[i]);
      Serial.print("BTN:");
      Serial.println(currentState);
    }

    btnPrev[i] = reading;
  }
}

void loop() {
  checkButtons();

  if (Serial.available() > 0) {
    char cmd = Serial.read();
    switch (cmd) {
      case 'A': case 'B': case 'C':
        setValve(cmd);
        Serial.print("OK:");
        Serial.println(currentState);
        break;
      case '?':
        Serial.print("STATE:");
        Serial.println(currentState);
        break;
    }
  }
}
