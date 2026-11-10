# XL9535 relay mux

A 16-relay board driven by an XL9535 I2C I/O expander, controlled through an ESP32-C6 (SparkFun Qwiic Pocket) that passes I2C writes through from USB using simpleRPC. Firmware 2.0 adds a host watchdog that puts the relays in a stored safe state.

- **Driver:** `xl9535_relay_mux.driver` has three layers.
  - `I2cBridge` covers every RPC call in the firmware: `write_i2c`, `set_led`, `digital_read`, `digital_write` and `pin_mode`, and in firmware 2.0 the watchdog and safe state calls. `write_i2c` raises when the board doesn't acknowledge, with firmware 2.0.
  - `Xl9535` gives access to the expander's output, polarity and configuration registers.
  - `RelayMux` closes at most one relay per group, and opens a group's old relay before closing the new one. By default every relay is in one group. Pass `groups` when relays do different jobs.
- **Bluesky:** `xl9535_relay_mux.ophyd_device.RelayMuxDevice`. Move `channel` to a relay index, or to `OPEN` (-1). Unstaging opens every relay.
  `RoutedRelayMuxDevice` adds `route`, for a second group named `route`.
- **Simulator:** `xl9535_relay_mux.sim.SimulatedBridge` stands in for the firmware's RPC interface and tracks which relays are energised.
- **Firmware:** `firmware/`, a PlatformIO project for the SparkFun ESP32-C6 Qwiic Pocket (Arduino core 3.3.11 through the pioarduino platform, simpleRPC 3.2.0). Build and flash with `pixi run -e firmware pio run -d src/instruments/xl9535_relay_mux/firmware -t upload`. The first build downloads about 7.5 GB of toolchain into `~/.platformio`.

## Watchdog and safe state (firmware 2.0)

- **Safe state:** an I2C address and the two output port values, stored in the bridge's flash. All relays open by default. The bridge writes it at boot, on `apply_safe_state`, and when the watchdog trips.
- **Watchdog:** `RelayMux(..., watchdog_s=5.0)` arms it, and a background thread feeds it four times per timeout until `close()`. Any RPC call also feeds it. If the host goes quiet for longer than the timeout, the bridge writes the safe state, lights its LED, and refuses I2C writes, which raise `WatchdogTripped`, until `recover_from_trip()`. Opening a `RelayMux` clears a trip left by an earlier host, with a warning.
- **What it covers:** the host crashing, losing power or losing USB, and the Python process being killed. It doesn't cover a plan that hangs while Python keeps running, since the thread keeps feeding. A firmware hang trips the ESP32's own task watchdog, which resets it, and the reset applies the safe state.
- **Changing the safe state:** `mux.program_safe_state([relays], confirm=True)`. It allows one relay per group, and it lasts across power cycles. The bridge stages it and returns a random token, the host reads the staged copy back, commits it with the token, and reads the stored copy back. A wrong token discards the staged copy.

## Quirks

- **I2C pins.** The Qwiic Pocket's variant uses GPIO 6 (SDA) and 7 (SCL). A build for the generic ESP32-C6 board uses other pins and gets no acknowledgement.
- **No read-back.** The firmware can only write I2C, so the driver records what it last wrote rather than reading registers back. Adding a `read_i2c` call to the firmware would let `Xl9535` read the input and output ports.
- **Opening clears the relays.** Opening `RelayMux` opens every relay, which disconnects whatever the mux had selected.
- **Power-on state.** Every expander pin starts as an input. The driver clears the outputs before switching the pins to outputs, so no relay closes during setup.

## Documents

- `docs/XL9535_XL9555_Datasheet_Rev2.3.pdf`: Xinluda XL9535/XL9555 datasheet, Rev. 2.3 (2018). The register map is in section 9.2. Downloaded 2026-11-06 from https://atta.szlcsc.com/upload/public/pdf/source/20240911/FFF826D97BDA33B4BE55BC4C2C305DC8.pdf, the LCSC copy for part C561273.
- No documentation for the relay board or the ESP32 module itself was found.
