// Arduino controller for Airtec 4V130C-M5 (5/3 solenoid valve, 12VDC)
// Receives single-char commands over Serial (9600 baud)
// Commands: 'A' = Position A, 'B' = Position B, 'C' = Center, '?' = Status
// Pin 7 = Relay 1 (Solenoid A), Pin 8 = Relay 2 (Solenoid B)
// Pin 2 = Cycle button (to GND, internal pullup) — cycles C → A → B → C

const int SOL_A_PIN = 7;
const int SOL_B_PIN = 8;
const int LED_PIN = 13;
const int BTN_PIN = 2;

const unsigned long DEBOUNCE_MS = 200;

char currentState = 'C';
bool lastBtnState = HIGH;
unsigned long lastBtnTime = 0;

void setup()
{
  pinMode(SOL_A_PIN, OUTPUT);
  pinMode(SOL_B_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BTN_PIN, INPUT_PULLUP);

  digitalWrite(SOL_A_PIN, LOW);
  digitalWrite(SOL_B_PIN, LOW);
  digitalWrite(LED_PIN, LOW);

  Serial.begin(9600);
  Serial.println("READY");
}

void setValve(char pos)
{
  switch (pos)
  {
  case 'A':
    digitalWrite(SOL_B_PIN, LOW);
    delay(5);
    digitalWrite(SOL_A_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
    currentState = 'A';
    break;
  case 'B':
    digitalWrite(SOL_A_PIN, LOW);
    delay(5);
    digitalWrite(SOL_B_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
    currentState = 'B';
    break;
  case 'C':
    digitalWrite(SOL_A_PIN, LOW);
    digitalWrite(SOL_B_PIN, LOW);
    digitalWrite(LED_PIN, LOW);
    currentState = 'C';
    break;
  }
}

char nextState()
{
  switch (currentState)
  {
  case 'C':
    return 'A';
  case 'A':
    return 'B';
  case 'B':
    return 'C';
  default:
    return 'C';
  }
}

void checkButton()
{
  bool reading = digitalRead(BTN_PIN);

  if (reading == LOW && lastBtnState == HIGH)
  {
    unsigned long now = millis();
    if (now - lastBtnTime > DEBOUNCE_MS)
    {
      lastBtnTime = now;
      setValve(nextState());
      Serial.print("BTN:");
      Serial.println(currentState);
    }
  }

  lastBtnState = reading;
}

void loop()
{
  checkButton();

  if (Serial.available() > 0)
  {
    char cmd = Serial.read();

    switch (cmd)
    {
    case 'A':
    case 'B':
    case 'C':
      setValve(cmd);
      Serial.print("OK:");
      Serial.println(currentState);
      break;
    case '?':
      Serial.print("STATE:");
      Serial.println(currentState);
      break;
    default:
      Serial.println("ERR:UNKNOWN");
      break;
    }
  }
}
