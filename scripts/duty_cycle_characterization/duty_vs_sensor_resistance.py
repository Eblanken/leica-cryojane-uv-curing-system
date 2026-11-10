"""Bluesky plan: heater duty cycle against sensor resistance, on two target devices.

This repeats the heater_characterization readings (sense and low-side voltage through the
mux, on the GDM-8341) with a known resistance in place of each thermistor and a 10 kohm
dummy load in place of each heater. The wiring is in bench.py. One QR101B in series with
a fixed resistor of about 9.1 kohm serves both devices through routing relays. The fixed
resistor keeps the box below its first output gap (12,154.75 to 17,260.6 ohm, see
qr101b_gap_probe) up to a total of about 21.2 kohm. Totals from about 26.4 to 43 kohm use
the box's next good range, and those in between can't be reached.

Run characterize_series_resistor.py first. This plan uses its latest result unless
--series-ohm is given.

Each device gets up to three runs, with a `phase` and a shared `campaign_uid` in their
metadata:

    locate_coarse   One way across the whole range, to bracket the switching point.
    locate_extended Only if still off at the top: one way across higher totals.
    locate_fine     One way across that bracket, to narrow it.
    sweep           Up and back down, with coarse steps plus fine steps across the bracket.

A device whose switching point is not found gets no sweep, since it would only record 0%
and 100%. --locate-only stops after the locate phases. --bracket skips them, using a
bracket from an earlier run, for a single device.

Every run checks the bench as it goes. A failed check raises BenchFault, which opens every
relay and restores the box:

    Before connecting    Sense near 0 V on the normally closed contact, low side at the rail.
    After connecting     The meter stays on sense through each dwell, and sense must stay
                         near the voltage the board's pull-up predicts.

The meter cannot watch sense while it reads the low side, so a fault that starts then is
caught at the next step.

Duty comes from the averaged low-side voltage, V = V_off (1 - D) + V_sat D. V_off is read
with the device idle, and V_sat is the run's lowest reading. That is only right if the
sweep reaches full-on, and if the meter averages the PWM evenly. Two options test the
second assumption during the sweep, and add time to each step:

    --ad3          Measures duty and frequency directly with the AD3, on device 0 only.
                   A capture that fails is retried after reopening the AD3, until one
                   works by default. With --ad3-attempts N, a step whose N captures all
                   fail records NaN rather than stopping the sweep.
    --pwm-checks   Reads the low side's AC RMS, which a square wave of duty D sets to
                   (V_off - V_sat) sqrt(D (1 - D)). The meter's AC range is rated from
                   20 Hz, so this says little about slower PWM.

The dummy load draws far less current than a heater, so V_sat will differ from the
heater_characterization readings, and any sense-voltage shift caused by heater current
will not appear.

    pixi run python scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py
    pixi run python scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py --locate-only
    pixi run python scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py --ad3 --pwm-checks
    pixi run python scripts/duty_cycle_characterization/duty_vs_sensor_resistance.py --simulate
"""

from __future__ import annotations

import argparse
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from bench import (
    AD3_DEVICE,
    AD3_WIRING,
    DEVICES,
    IDLE_SENSE_OHM,
    RUNS_DIR,
    expected_sense_v,
    open_bench,
)
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from characterize_series_resistor import latest_measurement
from cryojane_bench.runs import load_run, make_run_engine, run_file_name
from ophyd import Signal
from xl9535_relay_mux.ophyd_device import OPEN

PLAN_NAME = "duty_vs_sensor_resistance"
SWEEP_LOW_OHM, SWEEP_TOP_OHM = 10_000.0, 22_000.0  # The bench procedure's sweep.
COARSE_STEP_OHM = 1_000.0
LOCATE_FINE_STEPS = 9  # Inside the coarse bracket.
SWEEP_FINE_STEPS = 15  # Across the located bracket, with one more pitch on each side.
LOCATE_DWELL_S = 5.0  # About five time constants of C101 and R103.
LOCATE_READINGS = 3
# Good box setpoints: its limit to its first gap, then up to its second (run a4026e1f).
BOX_GOOD_OHM = ((36.0, 12_154.0), (17_261.0, 34_277.0))
# Tried only when a device is still off at the top of the coarse steps.
EXTENDED_LOW_OHM, EXTENDED_TOP_OHM, EXTENDED_STEP_OHM = 27_000.0, 43_000.0, 2_000.0
MISSED_LIMIT_OHM = 0.1  # The QR10x manual allows under one nominal step of SP to PV.

IDLE_READINGS = 3
IDLE_SENSE_LIMIT_V = 0.5
MINIMUM_RAIL_V = 10.0  # An idle low side below this means the board has no rail.
SENSE_FLOOR_V = -0.2  # Checked on every reading, since sense should never go negative.
SENSE_TOLERANCE_V = 0.5  # Allows for the 0.28 V seen on device 1.
SENSE_SETTLE_S = 3.0  # The tolerance applies this long after a change.
FULLY_ON_BELOW_V = 2.0  # The transistor measured 0.64 V when fully on.
FULLY_OFF_WITHIN_V = 0.5  # Of the idle low side.

AC_READINGS = 3
AC_STREAM, AD3_STREAM = "heater_ac", "ad3"


class BenchFault(RuntimeError):
    """The bench is not in a state that is safe or useful to continue from."""


@dataclass
class DeviceSweepResult:
    """What one run saw: idle low side, and (total ohm, mean low side) per step."""

    v_off: float
    steps: list[tuple[float, float]] = field(default_factory=list)


def box_setpoint_ohm(total_ohm: float, series_ohm: float) -> float:
    """The box's share of a total. Refuses anything outside its gap-free ranges."""
    setpoint = total_ohm - series_ohm
    if not any(low <= setpoint <= high for low, high in BOX_GOOD_OHM):
        raise ValueError(
            f"{total_ohm} ohm needs {setpoint:.1f} ohm from the box, outside its "
            f"gap-free ranges of {BOX_GOOD_OHM} ohm."
        )
    return setpoint


def _reachable(points, series_ohm: float) -> list[float]:
    rounded = {float(round(float(point))) for point in points}
    return sorted(
        point
        for point in rounded
        if any(low <= point - series_ohm <= high for low, high in BOX_GOOD_OHM)
    )


def coarse_points_ohm(series_ohm: float) -> list[float]:
    """The bench procedure's sweep, as far as the box reaches."""
    return _reachable(
        np.arange(SWEEP_LOW_OHM, SWEEP_TOP_OHM + 1, COARSE_STEP_OHM), series_ohm
    )


def extended_points_ohm(series_ohm: float) -> list[float]:
    """Totals above the box's first gap, for a device the coarse steps never turned on."""
    return _reachable(
        np.arange(EXTENDED_LOW_OHM, EXTENDED_TOP_OHM + 1, EXTENDED_STEP_OHM), series_ohm
    )


def locate_fine_points_ohm(
    bracket: tuple[float, float], series_ohm: float
) -> list[float]:
    """Evenly spaced points strictly inside a bracket."""
    return _reachable(np.linspace(*bracket, LOCATE_FINE_STEPS + 2)[1:-1], series_ohm)


def sweep_points_ohm(bracket: tuple[float, float], series_ohm: float) -> list[float]:
    """Coarse steps for the full range, plus fine steps across the switching bracket."""
    low, high = bracket
    pitch = (high - low) / (SWEEP_FINE_STEPS - 1)
    fine = np.linspace(low - pitch, high + pitch, SWEEP_FINE_STEPS + 2)
    return _reachable([*coarse_points_ohm(series_ohm), *fine], series_ohm)


def find_transition(
    steps: list[tuple[float, float]], v_off: float
) -> tuple[float, float] | None:
    """(highest total still fully off, lowest total fully on), or None if not bracketed."""
    ordered = sorted(steps)
    first_on = next((total for total, v in ordered if v < FULLY_ON_BELOW_V), None)
    if first_on is None:
        return None
    still_off = [
        total
        for total, v in ordered
        if total < first_on and v_off - v < FULLY_OFF_WITHIN_V
    ]
    return (max(still_off), first_on) if still_off else None


def check_sense(value: float, expected: float, *, settled: bool = True) -> None:
    """Raises BenchFault if a routed device's sense input is not where it should be."""
    if value < SENSE_FLOOR_V or (settled and abs(value - expected) > SENSE_TOLERANCE_V):
        raise BenchFault(
            f"Sense read {value:.4f} V, expected about {expected:.3f} V. "
            "Check the common GND connection to each board's P1.2."
        )


def _read_meter(meter):
    yield from bps.trigger(meter, wait=True)
    return (yield from bps.rd(meter.reading))


def _watch_sense(meter, expected: float, dwell_s: float):
    """Waits out a dwell with the meter on sense, checking every reading."""
    start = time.monotonic()
    while True:
        value = yield from _read_meter(meter)
        elapsed = time.monotonic() - start
        check_sense(value, expected, settled=elapsed >= SENSE_SETTLE_S)
        if elapsed >= dwell_s:
            return


def device_sweep(
    box,
    meter,
    mux,
    device: int,
    series_ohm: float,
    totals_ohm: list[float],
    *,
    phase: str = "sweep",
    return_sweep: bool = True,
    in_given_order: bool = False,
    readings_per_block: int = 10,
    relay_settle_s: float = 0.5,
    dwell_s: float = 30.0,
    pwm_checks: bool = False,
    ad3=None,
    md: dict | None = None,
):
    """One run: checks the idle device, connects it, then steps its sensor resistance.

    It starts at the lowest resistance, and goes back down too if `return_sweep`.
    Unstaging the mux at the end opens every relay, which holds the device's sense input
    at GND. Returns a DeviceSweepResult.

    `pwm_checks` adds the low side's AC reading at each step, in the heater_ac stream, and
    `ad3` adds one capture per step, in the ad3 stream.
    """
    relays = DEVICES[device]
    sensor_relay, heater_relay = relays["sensor_relay"], relays["heater_relay"]
    sensor_name, heater_name = f"Sensor ({device})", f"Heater ({device})"
    if in_given_order:
        sweep = list(totals_ohm)
    else:
        upward = sorted(totals_ohm)
        sweep = upward + upward[-2::-1] if return_sweep else upward
    setpoints = [box_setpoint_ohm(total, series_ohm) for total in sweep]
    channel_name = Signal(name="channel_name", value="", kind="hinted")
    sensor_ohm = Signal(name="sensor_ohm", value=IDLE_SENSE_OHM, kind="hinted")
    detectors = [meter, mux, box, channel_name, sensor_ohm]
    _md = {
        "plan_name": PLAN_NAME,
        "phase": phase,
        "experiment": "duty_cycle_characterization",
        "purpose": "Heater duty cycle against sensor resistance, from GDM-8341 voltages.",
        "target_device": device,
        "regulation_estimate_ohm": relays["regulation_ohm"],
        "heater_load": "10 kohm dummy load",
        "series_resistor_ohm": series_ohm,
        "idle_sense_ohm": IDLE_SENSE_OHM,
        "relays": {str(sensor_relay): sensor_name, str(heater_relay): heater_name},
        "route_relay": relays["route_relay"],
        "totals_ohm": sweep,
        "readings_per_block": readings_per_block,
        "relay_settle_s": relay_settle_s,
        "dwell_s": dwell_s,
        "pwm_checks": pwm_checks,
        "ad3_wiring": None if ad3 is None else AD3_WIRING,
        "sense_limits_v": {
            "idle": IDLE_SENSE_LIMIT_V,
            "floor": SENSE_FLOOR_V,
            "tolerance": SENSE_TOLERANCE_V,
            "settle_s": SENSE_SETTLE_S,
        },
        "hints": {"dimensions": [([sensor_ohm.name], "primary")]},
        **(md or {}),
    }
    result = DeviceSweepResult(v_off=float("nan"))

    def block(relay, name, count, stream="primary"):
        yield from bps.mv(mux.channel, relay, channel_name, name)
        yield from bps.sleep(relay_settle_s)
        values = []
        for _ in range(count):
            event = yield from bps.trigger_and_read(detectors, name=stream)
            values.append(event[meter.reading.name]["value"])
        return values

    def optional_readings():
        """The low side's AC reading, and an AD3 capture alongside a DC reading."""
        if pwm_checks:
            yield from bps.mv(meter.function, "VOLTAGE_AC")
            yield from block(heater_relay, heater_name, AC_READINGS, AC_STREAM)
            yield from bps.mv(meter.function, "VOLTAGE_DC")
        if ad3 is not None:
            yield from bps.trigger_and_read([*detectors, ad3], name=AD3_STREAM)

    @bpp.stage_decorator([box, meter, mux, *([] if ad3 is None else [ad3])])
    @bpp.run_decorator(md=_md)
    def steps():
        yield from bps.mv(meter.function, "VOLTAGE_DC")
        yield from bps.mv(mux.route, OPEN)

        idle = yield from block(sensor_relay, f"Idle sense ({device})", IDLE_READINGS)
        if max(abs(value) for value in idle) > IDLE_SENSE_LIMIT_V:
            raise BenchFault(
                f"Device {device} idle sense read {idle} V and should be near 0 V, "
                "so the box was not connected."
            )
        low_side = yield from block(
            heater_relay, f"Idle heater ({device})", IDLE_READINGS
        )
        result.v_off = float(np.mean(low_side))
        if result.v_off < MINIMUM_RAIL_V:
            raise BenchFault(
                f"Device {device} idle low side read {result.v_off:.3f} V, so its "
                "board does not seem to be powered."
            )

        # Set the box before connecting it, so the device never sees its old value, and
        # put the meter on sense first, so it sees the connection.
        yield from bps.mv(box.setpoint, setpoints[0])
        yield from bps.mv(mux.channel, sensor_relay, channel_name, sensor_name)
        yield from bps.sleep(relay_settle_s)
        yield from bps.mv(mux.route, relays["route_relay"])

        for total, setpoint in zip(sweep, setpoints):
            yield from bps.mv(mux.channel, sensor_relay, channel_name, sensor_name)
            yield from bps.mv(box.setpoint, setpoint)
            calculated = yield from bps.rd(box.calculated)
            if abs(calculated - setpoint) > MISSED_LIMIT_OHM:
                print(f"Box missed: {setpoint:.1f} ohm set, {calculated:.3f} ohm PV")
            actual = series_ohm + calculated
            yield from bps.mv(sensor_ohm, actual)
            expected = expected_sense_v(actual)
            yield from _watch_sense(meter, expected, dwell_s)
            sense = yield from block(sensor_relay, sensor_name, readings_per_block)
            for value in sense:
                check_sense(value, expected)
            low_side = yield from block(heater_relay, heater_name, readings_per_block)
            result.steps.append((total, float(np.mean(low_side))))
            yield from optional_readings()
            print(
                f"  Device {device} {phase}: {actual:9.1f} ohm, sense "
                f"{np.median(sense):.3f} V, low side {np.mean(low_side):.3f} "
                f"+/- {np.std(low_side):.3f} V"
            )

    yield from steps()
    return result


def locate_transition(
    box,
    meter,
    mux,
    device: int,
    series_ohm: float,
    *,
    dwell_s: float = LOCATE_DWELL_S,
    readings_per_block: int = LOCATE_READINGS,
    relay_settle_s: float = 0.5,
    md: dict | None = None,
):
    """Coarse then fine one-way runs. Returns the switching bracket, or None."""
    settings = {
        "return_sweep": False,
        "dwell_s": dwell_s,
        "readings_per_block": readings_per_block,
        "relay_settle_s": relay_settle_s,
        "md": md,
    }
    coarse = yield from device_sweep(
        box,
        meter,
        mux,
        device,
        series_ohm,
        coarse_points_ohm(series_ohm),
        phase="locate_coarse",
        **settings,
    )
    steps = list(coarse.steps)
    bracket = find_transition(steps, coarse.v_off)
    if bracket is None and all(v >= FULLY_ON_BELOW_V for _, v in steps):
        extended = yield from device_sweep(
            box,
            meter,
            mux,
            device,
            series_ohm,
            extended_points_ohm(series_ohm),
            phase="locate_extended",
            **settings,
        )
        steps += extended.steps
        bracket = find_transition(steps, extended.v_off)
    if bracket is None:
        return None
    # A bracket can span the box's gap, which leaves nothing to try inside it.
    points = locate_fine_points_ohm(bracket, series_ohm)
    if not points:
        return bracket
    fine = yield from device_sweep(
        box,
        meter,
        mux,
        device,
        series_ohm,
        points,
        phase="locate_fine",
        **settings,
    )
    return find_transition(steps + fine.steps, fine.v_off)


def duty_vs_sensor_resistance(
    box,
    meter,
    mux,
    devices: list[int],
    series_ohm: float,
    *,
    locate_only: bool = False,
    locate_dwell_s: float = LOCATE_DWELL_S,
    ad3=None,
    known_brackets: dict[int, tuple[float, float]] | None = None,
    md: dict | None = None,
    **sweep_settings,
):
    """Locates each device's switching point and sweeps it, restoring the box at the end.

    Only the sweep of AD3_DEVICE uses `ad3`, since it is wired to no other device. A device
    in `known_brackets` skips the locate phases and sweeps that bracket. Returns
    {device: bracket or None}.
    """
    original = yield from bps.rd(box.setpoint)
    brackets = {}

    def sweeps():
        for device in devices:
            device_md = {**(md or {}), "campaign_uid": str(uuid.uuid4())}
            if device in (known_brackets or {}):
                bracket = tuple(known_brackets[device])
                brackets[device] = bracket
                print(f"Device {device}: using the given bracket {bracket} ohm")
                yield from device_sweep(
                    box,
                    meter,
                    mux,
                    device,
                    series_ohm,
                    sweep_points_ohm(bracket, series_ohm),
                    ad3=ad3 if device == AD3_DEVICE else None,
                    md={
                        **device_md,
                        "transition_bracket_ohm": list(bracket),
                        "bracket_source": "given",
                    },
                    **sweep_settings,
                )
                continue
            bracket = yield from locate_transition(
                box,
                meter,
                mux,
                device,
                series_ohm,
                dwell_s=locate_dwell_s,
                relay_settle_s=sweep_settings.get("relay_settle_s", 0.5),
                md=device_md,
            )
            brackets[device] = bracket
            if bracket is None:
                print(f"Device {device}: no switching point found, so no sweep.")
                continue
            print(
                f"Device {device}: fully off at {bracket[0]:.0f} ohm, "
                f"fully on at {bracket[1]:.0f} ohm"
            )
            if locate_only:
                continue
            yield from device_sweep(
                box,
                meter,
                mux,
                device,
                series_ohm,
                sweep_points_ohm(bracket, series_ohm),
                ad3=ad3 if device == AD3_DEVICE else None,
                md={**device_md, "transition_bracket_ohm": list(bracket)},
                **sweep_settings,
            )

    def restore():
        yield from bps.mv(box.setpoint, original)

    yield from bpp.finalize_wrapper(sweeps(), restore())
    return brackets


def continuation_totals(run_path: Path, from_ohm: float) -> tuple[dict, list[float]]:
    """A sweep run's start document, and its steps from the first `from_ohm` on the way down.

    Used to rerun the rest of a sweep that stopped partway down.
    """
    start = load_run(run_path).start
    totals = start["totals_ohm"]
    top = totals.index(max(totals))
    down = totals[top:]
    if from_ohm not in down:
        raise SystemExit(
            f"{from_ohm} ohm is not on the down sweep of {run_path.name}: {down}"
        )
    return start, down[down.index(from_ohm) :]


def continue_sweep(
    box,
    meter,
    mux,
    device: int,
    series_ohm: float,
    totals,
    *,
    ad3=None,
    md=None,
    **settings,
):
    """Steps through `totals` in order, restoring the box afterwards."""
    original = yield from bps.rd(box.setpoint)

    def restore():
        yield from bps.mv(box.setpoint, original)

    return (
        yield from bpp.finalize_wrapper(
            device_sweep(
                box,
                meter,
                mux,
                device,
                series_ohm,
                totals,
                phase="sweep_continuation",
                in_given_order=True,
                ad3=ad3 if device == AD3_DEVICE else None,
                md=md,
                **settings,
            ),
            restore(),
        )
    )


def summarize(path: Path | str) -> pd.DataFrame:
    """One row per step of a `sweep` run: sense, low side and the duty they imply.

    Runs with the optional readings also get `ac_rms_v`, with the `ac_expected_v` that the
    duty predicts for a clean square wave, and `ad3_duty` and `ad3_frequency_hz`.
    """
    run = load_run(path)
    everything = run.table()
    v_off = everything.gdm_reading[
        everything.channel_name.str.startswith("Idle heater")
    ].mean()
    table = everything[~everything.channel_name.str.startswith("Idle")]
    step = (table.qr101b_setpoint.diff() != 0).cumsum().rename("step")
    direction = pd.Series(
        np.where(step <= (step.max() + 1) // 2, "up", "down"),
        index=table.index,
        name="direction",
    )
    keys = [step, direction]
    is_heater = table.channel_name.str.startswith("Heater")
    is_sense = table.channel_name.str.startswith("Sensor")
    sense = table.gdm_reading[is_sense].groupby([key[is_sense] for key in keys])
    low_side = table.gdm_reading[is_heater].groupby([key[is_heater] for key in keys])
    summary = pd.DataFrame(
        {
            "sensor_ohm": table.sensor_ohm.groupby(keys).first(),
            "sense_v": sense.median(),
            "low_side_v": low_side.mean(),
            "low_side_std_v": low_side.std(),
        }
    )
    v_sat = summary.low_side_v.min()
    summary["duty"] = (v_off - summary.low_side_v) / (v_off - v_sat)

    def by_step(stream: str) -> pd.DataFrame:
        rows = run.table(stream)
        return rows.groupby((rows.qr101b_setpoint.diff() != 0).cumsum().rename("step"))

    if AC_STREAM in run.stream_names:
        ac = by_step(AC_STREAM).gdm_reading.mean().rename("ac_rms_v")
        summary = summary.join(ac, on="step")
        duty = summary.duty.clip(0.0, 1.0)
        summary["ac_expected_v"] = (v_off - v_sat) * np.sqrt(duty * (1 - duty))
    if AD3_STREAM in run.stream_names:
        captures = by_step(AD3_STREAM)
        # D0 is on U101.6, which is low when the transistor is on.
        summary = summary.join(
            (1 - captures.ad3_pin_high_fraction.mean()).rename("ad3_duty"), on="step"
        )
        summary = summary.join(
            captures.ad3_pin_frequency_hz.mean().rename("ad3_frequency_hz"), on="step"
        )
    return summary.reset_index(level="step", drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--devices",
        type=int,
        nargs="+",
        choices=sorted(DEVICES),
        default=sorted(DEVICES),
    )
    parser.add_argument(
        "--series-ohm", type=float, help="Default: the latest measurement."
    )
    parser.add_argument(
        "--simulate", action="store_true", help="Use simulated instruments."
    )
    parser.add_argument(
        "--dwell", type=float, default=30.0, help="Seconds at each step."
    )
    parser.add_argument("--readings", type=int, default=10, help="Readings per node.")
    parser.add_argument(
        "--settle", type=float, default=0.5, help="Seconds after a relay."
    )
    parser.add_argument(
        "--locate-only",
        action="store_true",
        help="Find each switching point and stop, without the full sweep.",
    )
    parser.add_argument(
        "--locate-dwell",
        type=float,
        default=LOCATE_DWELL_S,
        help="Seconds at each locate step.",
    )
    parser.add_argument(
        "--ad3",
        action="store_true",
        help=f"Add an AD3 capture per sweep step on device {AD3_DEVICE}, which it's wired to.",
    )
    parser.add_argument(
        "--ad3-window", type=float, default=4.0, help="Seconds per AD3 capture."
    )
    parser.add_argument(
        "--ad3-attempts",
        type=int,
        default=0,
        help="AD3 captures tried per step, reopening it between them, before recording "
        "NaN. 0, the default, retries until one works.",
    )
    parser.add_argument(
        "--bracket",
        type=float,
        nargs=2,
        metavar=("FULLY_OFF_OHM", "FULLY_ON_OHM"),
        help="Skip the locate phases and sweep this bracket. Needs a single --devices.",
    )
    parser.add_argument(
        "--bracket-source",
        help="Where --bracket came from, such as a run uid, for the run metadata.",
    )
    parser.add_argument(
        "--pwm-checks",
        action="store_true",
        help="Add the low side's AC reading at each sweep step, on every device.",
    )
    parser.add_argument(
        "--continue-run",
        type=Path,
        metavar="RUN_JSONL",
        help="Rerun the down sweep of this sweep run from --continue-from on, with its "
        "device, bracket and step list. Its own settings are used, not this run's.",
    )
    parser.add_argument(
        "--continue-from",
        type=float,
        metavar="OHM",
        help="The down sweep total to start from. Start where the device was fully on, "
        "so it approaches the band from above as it did in the original run.",
    )
    parser.add_argument("--qr-port", help="Default: the only CH340 port.")
    parser.add_argument("--output", type=Path, default=RUNS_DIR)
    args = parser.parse_args()
    if args.bracket is not None and len(args.devices) != 1:
        parser.error("--bracket needs exactly one device in --devices.")
    if args.bracket is not None and args.locate_only:
        parser.error(
            "--bracket skips the locate phases, so it can't go with --locate-only."
        )

    output = args.output / "simulated" if args.simulate else args.output
    series_ohm = args.series_ohm
    source = "--series-ohm"
    if series_ohm is None:
        measurement = latest_measurement(output)
        if measurement is None:
            raise SystemExit(
                "No series resistor measurement found. Run "
                "characterize_series_resistor.py, or pass --series-ohm."
            )
        series_ohm, source = measurement.ohm, f"run {measurement.run_uid}"

    print(f"Series resistor {series_ohm:.1f} ohm, from {source}")

    if args.continue_run is not None:
        if args.continue_from is None:
            parser.error("--continue-run needs --continue-from.")
        original, totals = continuation_totals(args.continue_run, args.continue_from)
        device = original["target_device"]
        if abs(original.get("series_resistor_ohm", float("nan")) - series_ohm) > 0.05:
            raise SystemExit(
                f"{args.continue_run.name} used a {original.get('series_resistor_ohm')} "
                f"ohm series resistor, not {series_ohm}."
            )
        print(f"Continuing run {original['uid'][:8]} on device {device}: {totals}")
        bench = open_bench(args.simulate, args.qr_port, ad3=args.ad3)
        if bench.ad3 is not None:
            bench.ad3.driver.window_s = args.ad3_window
            bench.ad3.driver.attempts = args.ad3_attempts
            bench.ad3.driver.tolerate_failures = True
        starts = []
        try:
            make_run_engine(output)(
                continue_sweep(
                    bench.box,
                    bench.meter,
                    bench.mux,
                    device,
                    series_ohm,
                    totals,
                    ad3=bench.ad3,
                    pwm_checks=original["pwm_checks"],
                    readings_per_block=original["readings_per_block"],
                    relay_settle_s=original["relay_settle_s"],
                    dwell_s=original["dwell_s"],
                    md={
                        "series_resistor_source": source,
                        "continues_run": original["uid"],
                        "campaign_uid": original.get("campaign_uid"),
                        "transition_bracket_ohm": original.get(
                            "transition_bracket_ohm"
                        ),
                        "continue_from_ohm": args.continue_from,
                    },
                ),
                lambda name, doc: starts.append(doc) if name == "start" else None,
            )
        except BenchFault as fault:
            raise SystemExit(f"STOPPED, relays open: {fault}") from fault
        finally:
            bench.close()
        for start in starts:
            print(f"\nWrote {output / run_file_name(start)}.jsonl")
        return

    bench = open_bench(args.simulate, args.qr_port, ad3=args.ad3)
    if bench.ad3 is not None:
        bench.ad3.driver.window_s = args.ad3_window
        bench.ad3.driver.attempts = args.ad3_attempts
        bench.ad3.driver.tolerate_failures = True
    starts = []
    try:
        make_run_engine(output)(
            duty_vs_sensor_resistance(
                bench.box,
                bench.meter,
                bench.mux,
                args.devices,
                series_ohm,
                locate_only=args.locate_only,
                locate_dwell_s=args.locate_dwell,
                ad3=bench.ad3,
                known_brackets=(
                    None if args.bracket is None else {args.devices[0]: args.bracket}
                ),
                pwm_checks=args.pwm_checks,
                readings_per_block=args.readings,
                relay_settle_s=args.settle,
                dwell_s=args.dwell,
                md={
                    "series_resistor_source": source,
                    **(
                        {"given_bracket_source": args.bracket_source}
                        if args.bracket_source
                        else {}
                    ),
                },
            ),
            lambda name, doc: starts.append(doc) if name == "start" else None,
        )
    except BenchFault as fault:
        raise SystemExit(f"STOPPED, relays open: {fault}") from fault
    finally:
        bench.close()

    for start in starts:
        path = output / f"{run_file_name(start)}.jsonl"
        print(f"\nDevice {start['target_device']} {start['phase']}: {path}")
        if start["phase"] == "sweep":
            print(summarize(path).round(3).to_string())


if __name__ == "__main__":
    main()
