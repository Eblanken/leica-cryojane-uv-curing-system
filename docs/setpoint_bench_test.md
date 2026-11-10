# Setpoint bench test (proposed, not yet run)

Two questions:

1. How does the GDM-8341's averaged heater voltage relate to actual PWM duty?
2. How does GDM-8341 resistance through the mux compare with the QR101B?

The second stage also calibrates sense voltage against known resistance, which removes the +5 V and 10 kohm assumptions from the heater analysis and finds the regulation resistance directly. It also shows whether the steady 2-5 °C gap between each thermistor's reading and the probe on it comes from heat flow or from a sense-voltage shift under heater current.

## Stage 1: Resistance check, board disconnected

```
QR101B terminal A ---- relay 4 NO ---- relay 4 COM ---- DMM +
QR101B terminal B ------------------------------------- DMM -
```

- Keep the QR101B unattached from the board. An ohmmeter reading is invalid on a powered circuit.
- Step the QR101B through 5, 8, 10, 12, 15, 18, 20, 25, 30, 35 and 40 kohm, at most one change per second.
- Read `MEAS:RES?` and record QR101B setpoint, calculated PV and GDM value.
- Short relay 4 NO to DMM - once to measure the mux and lead resistance.

## Stage 2: Substitution, board powered

`scripts/duty_cycle_characterization/` automates the GDM-8341 part of this stage on two devices, with routing relays, a series resistor and 10 kohm dummy loads. Its `bench.py` has that wiring.

This QR101B cannot make every value. See the known gaps in `src/instruments/qr101b/README.md`, and check PV against the setpoint at each step.

Do one channel at a time. Unplug that channel's thermistor and connect the QR101B in its place. Designators below are for the PAD sheet. Machine 0 uses relays 0 and 1, and machine 1 uses relays 2 and 3:

```
QR101B A ------ HEATER_SENSE (P1.3) ------ relay 0 NO     (existing "Sensor")
QR101B B ------ GND (P1.2)
HEATER_LOAD (P1.4) ------------------------ relay 1 NO     (existing "Heater")
Board GND --------------------------------- DMM -

AD3 CH1+ ------ x10 probe ------ HEATER_LOAD (P1.4)
AD3 CH2+ ------ HEATER_PWM-INV (U101.1)    0-5 V comparator output, for a clean duty edge
AD3 CH1-, CH2-, GND ------ board GND, at the same point as DMM -
```

- Sweep the QR101B slowly across the transition, about 10 to 22 kohm. Use fine steps near the 17.6 kohm (machine 0) and 11.0 kohm (machine 1) estimates, and dwell at least 30 s per point.
- At each point, log QR101B setpoint, GDM sense voltage, GDM heater voltage and AD3 duty and frequency, capturing many PWM periods.
- Sweep up and then down to expose hysteresis.

## Safety and validity

- **AD3 input limit.** The schematic labels the rail +60 V and the highest reading so far is about 24 V. AD3 scope inputs are limited to about ±25 V, so use a x10 probe on `HEATER_LOAD` until the rail is measured.
- **Grounds.** AD3 ground is tied to the PC through USB. Confirm the board supply floats, or a ground clip can short it through USB. Clip only to board GND, never to `GND_PWR`.
- **Heater load.** The QR101B makes the board think the pad is cold, so the heater can stay on at room temperature with no cold chamber. Use a dummy load of the same resistance if the pad could overheat, or watch a thermocouple on it.
- **QR101B switching.** The output can open or short briefly while relays switch. An open reads as very cold and can pulse the heater.
- **Relay rate.** Stay at or below 1 Hz. Relay life is 1e5 cycles under load.

## What this settles

- **Duty against GDM voltage.** Average voltage should follow V_off x (1 - duty) + V_sat x duty. Any offset shows the meter is aliasing or missing fast PWM.
- **Sense voltage against known resistance.** This replaces the +5 V and 10 kohm assumptions.
- **Sense-voltage shift with heater state.** The October log shows sense readings spreading from 2.95 V to 3.45 V as the heater starts switching. AD3 CH2 shows whether this follows the heater state, for example ground bounce.
- **Regulation resistance.** The resistance where duty leaves 100% and reaches 50%, read directly for each channel.
