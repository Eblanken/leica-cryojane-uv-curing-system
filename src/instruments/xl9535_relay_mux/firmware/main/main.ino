// Make sure to install the esp32 boards package as well as simpleRPC via the
// boards and library manager interfaces.

// Simplest possible code to give control over the relay cards,
// this is just a pass through for the desktop computer.

#include "stdint.h"
#include "Wire.h"
#include <simpleRPC.h>

void writeI2C(uint8_t address, uint8_t payload_length, uint8_t * payload) { // I can't think of a reason to do more than 256 bytes
    Wire.beginTransmission(address);
    Wire.write(payload, payload_length);
    Wire.endTransmission();
}

void setLed(uint8_t value) {
    digitalWrite(LED_BUILTIN, value);
    pinMode(LED_BUILTIN, OUTPUT);
}

void setup() {
    Wire.begin();
    Serial.begin(115200);
}

void loop() {
    interface(
        Serial,
        writeI2C,
        "write_i2c: Sends bytes to the I2C interface. @address: Target device for communications. @payload_length: Number of bytes to send. @payload: Payload byte contents.",
        setLed,
        "set_led: Sets the value of the indicator LED. @value: Value of the LED, high if greater than 1.",
        digitalRead,
        "digital_read: Reads back a given digital pin. @pin: Target pin to read from.",
        digitalWrite,
        "digital_write: Writes to a given digital pin. @pin: Target pin to write to. @value: Target value of the pin.",
        pinMode,
        "pin_mode: Sets I/O configuration of the given pin. @pin: Target pin to configure. @value: Target value of the pin configuration."
    );
}