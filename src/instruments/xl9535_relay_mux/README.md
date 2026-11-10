# XL9535 relay mux

A 16-relay board driven by an XL9535 I2C I/O expander, controlled through an ESP32 that passes I2C writes through from USB using simpleRPC.

- **Driver:** `xl9535_relay_mux.driver` has three layers.
  - `I2cBridge` covers every RPC call in the firmware: `write_i2c`, `set_led`, `digital_read`, `digital_write` and `pin_mode`.
  - `Xl9535` gives access to the expander's output, polarity and configuration registers.
  - `RelayMux` closes at most one relay per group, and opens a group's old relay before closing the new one. By default every relay is in one group. Pass `groups` when relays do different jobs.
- **Bluesky:** `xl9535_relay_mux.ophyd_device.RelayMuxDevice`. Move `channel` to a relay index, or to `OPEN` (-1). Unstaging opens every relay.
  `RoutedRelayMuxDevice` adds `route`, for a second group named `route`.
- **Simulator:** `xl9535_relay_mux.sim.SimulatedBridge` stands in for the firmware's RPC interface and tracks which relays are energised.
- **Firmware:** `firmware/main/main.ino`, for the Arduino ESP32 core with the simpleRPC library.

## Quirks

- **No read-back.** The firmware can only write I2C, so the driver records what it last wrote rather than reading registers back. Adding a `read_i2c` call to the firmware would let `Xl9535` read the input and output ports.
- **Opening clears the relays.** Opening `RelayMux` opens every relay, which disconnects whatever the mux had selected.
- **Power-on state.** Every expander pin starts as an input. The driver clears the outputs before switching the pins to outputs, so no relay closes during setup.

## Documents

- `docs/XL9535_XL9555_Datasheet_Rev2.3.pdf`: Xinluda XL9535/XL9555 datasheet, Rev. 2.3 (2018). The register map is in section 9.2. Downloaded 2026-11-06 from https://atta.szlcsc.com/upload/public/pdf/source/20240911/FFF826D97BDA33B4BE55BC4C2C305DC8.pdf, the LCSC copy for part C561273.
- No documentation for the relay board or the ESP32 module itself was found.
