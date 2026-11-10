# Eastwood QR101B

Programmable resistance substitution box, set from its keypad or over a CH340 USB serial port with an AT command set.

- **Driver:** `qr101b.driver.Qr101b` covers the manual's full AT command table:
  - setpoint, including relative steps
  - PV, the safety limit and internal temperature
  - user calibration enable and info
  - device type, serial number, firmware, hardware, production date and TCR

  `query` sends any other command.
- **Bluesky:** `qr101b.ophyd_device.Qr101bDevice`.
  - Move `setpoint` to change the output.
  - Each event records the setpoint, PV and internal temperature.
  - Every descriptor records the limit, calibration source and identity.
- **Simulator:** `qr101b.sim.SimulatedQr101b`.

## Quirks and safety

- **PV is not a measurement.** It is the value the box calculates from its calibration table for the relays it actually closed. If PV differs from SP, the box is not producing the setpoint, so compare the two before blaming the meter.
- **The output can briefly open or short while relays switch** (under 95 ms on a QR101). Changing the setpoint disturbs the circuit on its terminals.
- **Rate limit.** Relay life is 1e5 cycles under load. The manual recommends 1 Hz, so the driver waits at least `min_change_interval_s` (1 s by default) between output changes.
- **Limits.** Setpoints below the safety limit (`RLimit`) or below about 1 ohm are refused. The box cannot short or open its output.
- **Isolation.** The manual asks for a USB isolator when the output sees 36 V or more.
- **Port.** The CH340 is generic, so the driver confirms the port with `AT+DEV.TYPE?` before using it.

## Known gaps on S/N 00000302

This box cannot make every value in its range. It sets the nearest value it can make and still echoes the requested setpoint, so check PV. Runs `a4026e1f` and `d080e0ce` of `scripts/instrument_checks/qr101b_gap_probe/` found these, with `*` marking an edge calculated from the 5,105.86 ohm width:

| Gap (ohm) |
|---|
| 12,154.75 to 17,260.61 |
| 34,277.54 to 39,383.39 |
| 53,233.8* to 58,339.71 |
| 75,356.63 to 80,462.49 |
| 94,680.52 to 99,786.4* |
| 135,759.6* to 140,865.47 |

Each gap is the first one offset by a sum of about 22,122.79, 41,079.10 and 82,525.77 ohm, which suggests one element of about 5.1 kohm is unusable. The cause is not confirmed. The runs sampled the range sparsely, so there may be others, and the pattern also predicts one at about 116.8 to 121.9 kohm that has not been tested.

## Documents

- `docs/QR10x_Spec_Manual_V3.0.pdf`: QR10x Specification & User's Manual, Rev. 3.0, June 2023. The AT command set is on pages 16-17. Downloaded 2026-11-06 from https://media.tindie.com/datasheets/28870/2023-06-15-14-06-39/QR10x_Spec_Manual_V3.0.pdf, the copy linked from Eastwood's Tindie listing.
