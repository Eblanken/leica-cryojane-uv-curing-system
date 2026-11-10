# leica-cryojane-uv-curing-system

Reverse engineering artifacts and replacement design for Leica's CryoJane UV curing system.

## Layout

| Path | What it holds |
|---|---|
| `hardware/reverse_engineered/` | KiCad captures of existing boards. Each project has its own folder. |
| `hardware/products/` | KiCad projects for the replacement design. |
| `firmware/` | PlatformIO projects for the replacement design. |
| `scripts/` | Bench notebooks, one folder per experiment with `data/` and `results/`. Shared acquisition and mux firmware sit at the top level. |
| `src/cryojane_bench/` | Python shared by the scripts: instrument drivers, log and logger readers, plateau detection, thermistor maths. |
| `tests/` | Tests for `src/`. They need no hardware. |
| `docs/` | Bench procedures and design notes. |

Python belongs in `src/` once a second script needs it. Data lives with the experiment that produced it.

## Setup

Install [pixi](https://pixi.sh), then:

```
pixi install
pixi run test              # Run the tests.
pixi run format            # Apply Black to the Bluesky code.
pixi run list-instruments  # Show attached COM ports.
pixi run lab               # Open the notebooks.
pixi run -e firmware pio --version  # PlatformIO lives in its own environment.
```

## Bench instruments

| Instrument | Package | USB identity |
|---|---|---|
| GW Instek GDM-8341 meter | `src/instruments/gdm8341/` | VID:PID 2184:0030 |
| XL9535 relay mux via ESP32 bridge | `src/instruments/xl9535_relay_mux/` | VID 303A |
| Eastwood QR101B-1M-R1 resistance box | `src/instruments/qr101b/` | VID:PID 1A86:7523 (generic CH340) |

## Experiment data

New acquisition runs through [Bluesky](https://blueskyproject.io/) plans. Each run is one JSON Lines file of event-model documents, written by suitcase-jsonl, holding the data, its units, every instrument's identity and settings, and how the run ended. `cryojane_bench.runs.load_run` reads one back into pandas.

| Experiment | Plan | Runs |
|---|---|---|
| QR101B against GDM-8341 | `scripts/instrument_checks/qr101b_gdm8341_validation/qr101b_gdm8341_validation.py` | `scripts/instrument_checks/qr101b_gdm8341_validation/data/` |
| Passive sensor sweep | `scripts/sensor_characterization/acquisition.py` | `scripts/sensor_characterization/runs/` |
| QR101B output gaps | `scripts/instrument_checks/qr101b_gap_probe/qr101b_gap_probe.py` | `scripts/instrument_checks/qr101b_gap_probe/data/` |
| Series resistor, through the mux | `scripts/duty_cycle_characterization/characterize_series_resistor.py` | `scripts/duty_cycle_characterization/runs/` |
| Duty cycle against sensor resistance, two devices | `scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py` | `scripts/duty_cycle_characterization/runs/` |

The plan scripts take `--simulate` for a dry run without hardware, except the gap probe. The duty-cycle bench's wiring is in `scripts/duty_cycle_characterization/bench.py`. `scripts/instrument_checks/qr101b_gdm8341_validation/qr101b_gdm8341_validation.ipynb` plots the QR101B against the GDM-8341 from a run file.

## Current results

Run the notebooks in this order, because the second reads the first one's `results/fit_summary.csv`:

1. `scripts/sensor_characterization/sensor_characterization.ipynb`: passive Steinhart-Hart and heater resistance fits, referenced to a HOBO logger.
2. `scripts/heater_characterization/heater_characterization.ipynb`: powered behaviour and regulation points, referenced to the chamber probe.

`scripts/sensor_characterization/sensor_characterization_bluesky.ipynb` repeats the first analysis from the sweep's Bluesky run in `runs/`, and checks that it gives the same fits.

`docs/setpoint_bench_test.md` has the planned test to confirm the regulation points.
