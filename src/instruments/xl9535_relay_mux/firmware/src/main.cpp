// USB-to-I2C bridge for the XL9535 relay board, with a host watchdog.
//
// The host computes every register write, and this firmware passes them through. It also
// keeps a safe state for the relays (an I2C address and the two output port values), which
// it writes:
//   - at boot, so a bridge reset or brownout leaves the relays in a known state;
//   - when the host watchdog trips, that is, when it is armed and the host sends no RPC
//     call for longer than its timeout;
//   - when the host asks, with apply_safe_state.
// After a trip, write_i2c refuses (returns WRITE_TRIPPED) until the host calls
// watchdog_clear, so a host that comes back can't carry on from state it no longer has.
//
// The safe state is changed in two steps, so a corrupted or stray call can't set it:
// stage_safe_state returns a random token, and commit_safe_state with that token stores
// it in flash. The host reads both back to check them.
//
// The loop task is also on the ESP-IDF task watchdog, so a firmware hang resets the chip,
// which then applies the safe state at boot.

#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <esp_random.h>
#include <simpleRPC.h>

namespace {

constexpr uint32_t FIRMWARE_VERSION = 0x020000;  // 2.0.0. Version 1 had no watchdog.

constexpr uint8_t REGISTER_OUTPUT_PORT_0 = 0x02;
constexpr uint8_t REGISTER_CONFIGURATION_PORT_0 = 0x06;
constexpr uint8_t FIRST_ADDRESS = 0x20, LAST_ADDRESS = 0x27;
constexpr uint8_t SAFE_WRITE_ATTEMPTS = 3;
constexpr uint32_t MINIMUM_TIMEOUT_MS = 100, MAXIMUM_TIMEOUT_MS = 600000;

constexpr uint8_t WRITE_TRIPPED = 0xFE;  // Above the Wire.endTransmission codes (0 to 5).
constexpr uint8_t STATUS_ARMED = 0x01, STATUS_TRIPPED = 0x02;
constexpr uint8_t ARM_OK = 0, ARM_BAD_TIMEOUT = 1, ARM_TRIPPED = 2;
constexpr uint8_t COMMIT_OK = 0, COMMIT_BAD_TOKEN = 1, COMMIT_STORE_FAILED = 2;
constexpr uint32_t STAGED_VALID = 1UL << 24;

struct SafeState {
    uint8_t address;
    uint8_t port0;
    uint8_t port1;
};

SafeState safeState = {FIRST_ADDRESS, 0x00, 0x00};  // Every relay open.
SafeState staged = {};
uint32_t stagedToken = 0;  // 0 means nothing is staged.

bool armed = false;
bool tripped = false;
uint32_t timeoutMs = 0;
uint32_t lastCallMs = 0;
uint8_t lastSafeWriteStatus = 0;
uint32_t tripCount = 0;

Preferences preferences;

uint32_t pack(SafeState const& state) {
    return (uint32_t(state.address) << 16) | (uint32_t(state.port1) << 8) | state.port0;
}

void heard() {
    lastCallMs = millis();
}

uint8_t writePair(uint8_t address, uint8_t first_register, uint8_t port0, uint8_t port1) {
    // The XL9535's register pointer moves on to the other port of the pair.
    Wire.beginTransmission(address);
    Wire.write(first_register);
    Wire.write(port0);
    Wire.write(port1);
    return Wire.endTransmission();
}

// Outputs first, then directions, as the host driver does, so no relay closes on the way.
uint8_t writeSafeState() {
    uint8_t status = 0;
    for (uint8_t attempt = 0; attempt < SAFE_WRITE_ATTEMPTS; ++attempt) {
        status = writePair(
            safeState.address, REGISTER_OUTPUT_PORT_0, safeState.port0, safeState.port1);
        if (status == 0) {
            status = writePair(safeState.address, REGISTER_CONFIGURATION_PORT_0, 0x00, 0x00);
        }
        if (status == 0) {
            break;
        }
    }
    lastSafeWriteStatus = status;
    return status;
}

void loadSafeState() {
    preferences.begin("relay_mux", true);
    if (preferences.isKey("address")) {
        uint8_t address = preferences.getUChar("address", FIRST_ADDRESS);
        if (address >= FIRST_ADDRESS && address <= LAST_ADDRESS) {
            safeState = {address, preferences.getUChar("port0", 0), preferences.getUChar("port1", 0)};
        }
    }
    preferences.end();
}

void trip() {
    armed = false;
    tripped = true;
    ++tripCount;
    writeSafeState();
    digitalWrite(LED_BUILTIN, HIGH);
}

// RPC calls. Every one counts as the host being alive.

uint8_t writeI2C(uint8_t address, uint8_t payload_length, uint8_t* payload) {
    heard();
    if (tripped) {
        return WRITE_TRIPPED;
    }
    Wire.beginTransmission(address);
    Wire.write(payload, payload_length);
    return Wire.endTransmission();
}

void setLed(uint8_t value) {
    heard();
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, value);
}

int rpcDigitalRead(uint8_t pin) {
    heard();
    return digitalRead(pin);
}

void rpcDigitalWrite(uint8_t pin, uint8_t value) {
    heard();
    digitalWrite(pin, value);
}

void rpcPinMode(uint8_t pin, uint8_t mode) {
    heard();
    pinMode(pin, mode);
}

uint32_t firmwareVersion() {
    heard();
    return FIRMWARE_VERSION;
}

uint8_t watchdogArm(uint32_t timeout_ms) {
    heard();
    if (timeout_ms < MINIMUM_TIMEOUT_MS || timeout_ms > MAXIMUM_TIMEOUT_MS) {
        return ARM_BAD_TIMEOUT;
    }
    if (tripped) {
        return ARM_TRIPPED;
    }
    timeoutMs = timeout_ms;
    armed = true;
    return ARM_OK;
}

void watchdogKick() {
    heard();
}

void watchdogDisarm() {
    heard();
    armed = false;
}

uint8_t watchdogStatus() {
    heard();
    return (armed ? STATUS_ARMED : 0) | (tripped ? STATUS_TRIPPED : 0);
}

uint32_t watchdogTimeoutMs() {
    heard();
    return timeoutMs;
}

uint32_t watchdogTripCount() {
    heard();
    return tripCount;
}

uint8_t watchdogClear() {
    heard();
    tripped = false;
    digitalWrite(LED_BUILTIN, LOW);
    return lastSafeWriteStatus;
}

uint8_t applySafeState() {
    heard();
    return writeSafeState();
}

uint8_t lastSafeStateStatus() {
    heard();
    return lastSafeWriteStatus;
}

uint32_t getSafeState() {
    heard();
    return pack(safeState);
}

uint32_t stageSafeState(uint8_t address, uint8_t port0, uint8_t port1) {
    heard();
    stagedToken = 0;
    if (address < FIRST_ADDRESS || address > LAST_ADDRESS) {
        return 0;
    }
    staged = {address, port0, port1};
    do {
        stagedToken = esp_random();
    } while (stagedToken == 0);
    return stagedToken;
}

uint32_t getStagedSafeState() {
    heard();
    return stagedToken ? (STAGED_VALID | pack(staged)) : 0;
}

uint8_t commitSafeState(uint32_t token) {
    heard();
    if (stagedToken == 0 || token != stagedToken) {
        stagedToken = 0;
        return COMMIT_BAD_TOKEN;
    }
    stagedToken = 0;
    preferences.begin("relay_mux", false);
    bool stored = preferences.putUChar("address", staged.address) == 1 &&
                  preferences.putUChar("port0", staged.port0) == 1 &&
                  preferences.putUChar("port1", staged.port1) == 1;
    preferences.end();
    if (!stored) {
        return COMMIT_STORE_FAILED;
    }
    loadSafeState();
    return pack(safeState) == pack(staged) ? COMMIT_OK : COMMIT_STORE_FAILED;
}

}  // namespace

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);
    Wire.begin();
    Serial.begin(115200);
    loadSafeState();
    writeSafeState();
    enableLoopWDT();  // The core feeds it between calls to loop().
    heard();
}

void loop() {
    interface(
        Serial,
        writeI2C,
        "write_i2c: Sends bytes to the I2C interface. @address: Target device for communications. @payload_length: Number of bytes to send. @payload: Payload byte contents. @return: Wire.endTransmission status, 0 when acknowledged, or 254 when refused after a watchdog trip.",
        setLed,
        "set_led: Sets the value of the indicator LED. @value: Value of the LED, high if greater than 1.",
        rpcDigitalRead,
        "digital_read: Reads back a given digital pin. @pin: Target pin to read from. @return: Pin level.",
        rpcDigitalWrite,
        "digital_write: Writes to a given digital pin. @pin: Target pin to write to. @value: Target value of the pin.",
        rpcPinMode,
        "pin_mode: Sets I/O configuration of the given pin. @pin: Target pin to configure. @value: Target value of the pin configuration.",
        firmwareVersion,
        "firmware_version: Firmware version. @return: Major, minor and patch, one byte each, major highest.",
        watchdogArm,
        "watchdog_arm: Arms the host watchdog. Any RPC call feeds it. @timeout_ms: Milliseconds without a call before the safe state is applied, 100 to 600000. @return: 0 if armed, 1 for a bad timeout, 2 if tripped and not yet cleared.",
        watchdogKick,
        "watchdog_kick: Feeds the host watchdog, and does nothing else.",
        watchdogDisarm,
        "watchdog_disarm: Disarms the host watchdog.",
        watchdogStatus,
        "watchdog_status: Watchdog state. @return: Bit 0 set when armed, bit 1 set when tripped.",
        watchdogTimeoutMs,
        "watchdog_timeout_ms: The timeout last armed. @return: Milliseconds.",
        watchdogTripCount,
        "watchdog_trip_count: Trips since boot. @return: Count.",
        watchdogClear,
        "watchdog_clear: Clears a trip, so write_i2c works again. @return: Status of the last safe state write, 0 if it was acknowledged.",
        applySafeState,
        "apply_safe_state: Writes the safe state now. @return: Wire.endTransmission status, 0 when acknowledged.",
        lastSafeStateStatus,
        "last_safe_state_status: Status of the last safe state write, at boot, on a trip or on request. @return: Wire.endTransmission status.",
        getSafeState,
        "get_safe_state: The stored safe state. @return: Address in bits 16 to 23, port 1 outputs in bits 8 to 15, port 0 outputs in bits 0 to 7.",
        stageSafeState,
        "stage_safe_state: Stages a new safe state, to be committed with the returned token. @address: XL9535 address, 0x20 to 0x27. @port0: Port 0 outputs. @port1: Port 1 outputs. @return: Token, or 0 for a bad address.",
        getStagedSafeState,
        "get_staged_safe_state: The staged safe state. @return: As get_safe_state, with bit 24 set, or 0 when nothing is staged.",
        commitSafeState,
        "commit_safe_state: Stores the staged safe state in flash. @token: Token from stage_safe_state. @return: 0 if stored and read back, 1 for a wrong or stale token, 2 if storing failed.");

    if (armed && millis() - lastCallMs > timeoutMs) {
        trip();
    }
}
