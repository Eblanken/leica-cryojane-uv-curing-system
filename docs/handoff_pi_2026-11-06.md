# Handoff to the Raspberry Pi, 2026-11-06

Written when the bench moved from the Windows PC to the Pi. Check anything here against the hardware before relying on it.

## Standing rules

- Don't commit or push unless Erick asks. Nothing has been committed since `897c125`.
- Check with Erick before actuating the mux or QR101B, unless he has already approved that run. He approved finishing the protocol on device 0 with `--ad3 --pwm-checks`, and one overnight repeat of both devices if the data looks good.
- Ask before deleting existing notebooks, or running Black on files that aren't from the Bluesky migration, including `hobo.py`.
- Don't overwrite `results/fit_summary.csv` with a different schema.
- Don't change the wiring, and don't work around a guard that trips. Stop and report.

## The Pi

Raspberry Pi 3 B+, 905 MB of RAM, 64-bit Raspberry Pi OS Lite (Trixie), hostname `cryojane-bench`, on TRT-Guest over WPA3. The environment is built, with WaveForms 3.25.1 and Adept 2.30.1 installed. Swap is 905 MB zram plus a 2 GB file. A heavy job makes the Pi very slow, so don't run tests during a sweep. `pixi.toml` and `pixi.lock` here have `linux-aarch64` added, which the PC's copy doesn't have yet.

Run long jobs in `tmux`, so they survive a dropped connection. Instruments are found by USB ID, not COM number.

## The bench

Wiring is in `scripts/duty_cycle_characterization/bench.py`. Series resistor: 9,083.7 ohm (run `ffc3c379`). The AD3 is on device 0 only: D0 on U101.6 (low means the transistor is on), CH1 on the low side through a probe assumed to be x10, CH2 not connected.

## Results so far

Regulation estimates in `bench.py` look swapped between the devices and should be corrected: device 0 behaves like the 11 kohm board and device 1 like the 17.58 kohm board.

**Device 1** (run `d7432557`, complete, up and down): on/off switching with no PWM band seen, within one 7 ohm step, near 13.90 kohm and sense 3.177 V. Low side 23.99 V off, 0.058 V on, unusually low for the Darlington in the schematic. Its pull-up looks like about 8 kohm rather than 10 kohm. An unexplained 8 mV sense step sits near 13.93 to 13.95 kohm. The relay 3 wire on device 1 was fixed before this run.

**Device 0**: PWM at 27 to 37 Hz, switching between about 10.3 and 10.9 kohm. Run `e47b1737` is the sweep that was stopped partway to move to the Pi: its up sweep is complete and its down sweep is not, and it has no stop document. Don't count it as a finished result. Run `46f8bd0a` is also a stopped partial, and `4280755f` failed on a meter dropout.

**Duty measurement**: the meter's DC reading aliases against the PWM and is off by up to about 10 duty points at low duty. The AD3 is the reference, and the AC reading tracks it above about 10% duty. `9e4553c9` is an invalid series resistor run, all overloads.

## Open problems

- The meter sometimes doesn't answer `MEAS:VOLT:DC?` within 5 s in device 0's band. The driver now retries once, and it fired 3 times on the PC. The cause is unconfirmed, with slow autoranging on PWM the best guess.
- Before the driver cleared its input buffer, a late reply could have been recorded against the wrong node. Earlier runs still need checking for this.
- The AD3 driver has only run on Windows so far, not with a real AD3 on Linux.
- Whether device 1 has any proportional band with a real heater and thermistor is unknown.

## Next steps

1. With the USB cables moved, check that all four instruments appear: meter, QR101B, mux bridge and AD3.
2. Run device 0, in `tmux`:
   `pixi run python -u scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py --devices 0 --ad3 --pwm-checks`
3. Analyze it: AD3 against AC against meter duty, including the steps that needed retries.
4. If it looks good, repeat both devices once for repeatability (`--devices 1 0 --ad3 --pwm-checks`), then leave a summary.
5. Later, with Erick: move the AD3 to device 1, correct the regulation estimates, add `--resume` and a stop-after-this-step option, and set up `pre-commit`, `CLAUDE.md` and the data rules. He still has to say whether the repo is public or private, and which email to commit under.
