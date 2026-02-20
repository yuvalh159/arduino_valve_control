// Arduino controller for Airtec 4V120-M5 (5/2 bistable solenoid valve, 12VDC)
// Serial commands (9600 baud): A = Position A, B = Position B, ? = Status
// Pin 7 = Relay 1 (Solenoid A), Pin 8 = Relay 2 (Solenoid B)
// Pin 2 = Button A, Pin 3 = Button B (both to GND)
//
// The 4V120 is a bistable (latching) valve — it stays in the last commanded
// position even when both solenoids are de-energised.
// State is saved to EEPROM so the correct solenoid is pulsed after reset.

#include <EEPROM.h>

const int SOL_A_PIN = 7;
const int SOL_B_PIN = 8;
const int LED_PIN   = 13;
const int BTN_A_PIN = 2;
const int BTN_B_PIN = 3;

const int BTN_PINS[2]     = {BTN_A_PIN, BTN_B_PIN};
const char BTN_TARGETS[2] = {'A', 'B'};

const int EEPROM_ADDR = 0;

const unsigned long PULSE_MS     = 150;
const unsigned long EMI_GUARD_MS = 400;
const unsigned long DEBOUNCE_MS  = 200;

char currentState = 'A';
bool btnPrev[2]   = {HIGH, HIGH};
unsigned long btnLastPress[2] = {0, 0};
unsigned long lastChangeTime  = 0;

void pulseValve(char pos) {
  digitalWrite(LED_PIN, HIGH);

  if (pos == 'A') {
    digitalWrite(SOL_A_PIN, HIGH);
    digitalWrite(SOL_B_PIN, LOW);
  } else {
    digitalWrite(SOL_A_PIN, LOW);
    digitalWrite(SOL_B_PIN, HIGH);
  }

  delay(PULSE_MS);

  digitalWrite(SOL_A_PIN, LOW);
  digitalWrite(SOL_B_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
}

void setValve(char pos) {
  if (pos != currentState) {
    EEPROM.update(EEPROM_ADDR, pos);
  }
  currentState = pos;
  pulseValve(pos);
  lastChangeTime = millis();
}

char loadSavedState() {
  char saved = EEPROM.read(EEPROM_ADDR);
  if (saved == 'A' || saved == 'B') return saved;
  return 'A';
}

void setup() {
  pinMode(SOL_A_PIN, OUTPUT);
  pinMode(SOL_B_PIN, OUTPUT);
  pinMode(LED_PIN,   OUTPUT);
  pinMode(BTN_A_PIN, INPUT_PULLUP);
  pinMode(BTN_B_PIN, INPUT_PULLUP);

  char saved = loadSavedState();
  currentState = saved;
  pulseValve(saved);

  Serial.begin(9600);
  Serial.println("READY");
}

void checkButtons() {
  unsigned long now = millis();

  if (now - lastChangeTime < EMI_GUARD_MS) {
    for (int i = 0; i < 2; i++) btnPrev[i] = digitalRead(BTN_PINS[i]);
    return;
  }

  for (int i = 0; i < 2; i++) {
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
      case 'A': case 'B':
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
