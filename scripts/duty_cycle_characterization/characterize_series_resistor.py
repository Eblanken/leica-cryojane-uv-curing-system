"""Bluesky plan: measure the bench's series resistor through the mux, with no lead changes.

The meter reads the resistor alone, then the QR101B and resistor together at several box
values. The slope of those readings against the box's PV is the meter's gain, using the
box as the reference, and dividing the resistor's reading by that gain gives its value.
The wiring is in bench.py. Every routing relay stays open, so neither board is connected
to the meter's resistance path.

The result is limited by the box's own accuracy (class B, 0.02%, or about 2 ohm here).
Relay and lead resistance stay in the reading, but they should be well under 1 ohm.

    pixi run python scripts/duty_cycle_characterization/characterize_series_resistor.py
    pixi run python scripts/duty_cycle_characterization/characterize_series_resistor.py --simulate
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from bench import BOX_AND_RESISTOR_RELAY, RESISTOR_RELAY, RUNS_DIR, open_bench
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from cryojane_bench.runs import load_run, make_run_engine, run_file_name
from gdm8341.driver import OVERLOAD_THRESHOLD
from ophyd import Signal
from xl9535_relay_mux.ophyd_device import OPEN

PLAN_NAME = "series_resistor"
ALONE, TOGETHER = "Series resistor", "QR101B and series resistor"
# Box values below its first gap, keeping the total on the meter's 50 kohm range.
BOX_POINTS_OHM = (6_000.0, 8_000.0, 9_000.0, 10_000.0, 12_000.0)
BOX_ACCURACY_FRACTION = 2e-4  # Class B, from the QR10x manual.
RESOLUTION_OHM = 0.5  # Half a count on the 50 kohm range.


def series_resistor(
    box,
    meter,
    mux,
    box_points_ohm=BOX_POINTS_OHM,
    *,
    readings_per_block: int = 20,
    relay_settle_s: float = 1.0,
    md: dict | None = None,
):
    """Reads the resistor before and after the box values, to show any drift."""
    node = Signal(name="channel_name", value="", kind="hinted")
    detectors = [meter, mux, box, node]
    original = yield from bps.rd(box.setpoint)
    _md = {
        "plan_name": PLAN_NAME,
        "experiment": "duty_cycle_characterization",
        "purpose": "Measure the series resistor, using the QR101B as the reference.",
        "resistor_relay": RESISTOR_RELAY,
        "box_and_resistor_relay": BOX_AND_RESISTOR_RELAY,
        "box_points_ohm": list(box_points_ohm),
        "readings_per_block": readings_per_block,
        "relay_settle_s": relay_settle_s,
        **(md or {}),
    }

    def block(relay, name):
        yield from bps.mv(mux.channel, relay, node, name)
        yield from bps.sleep(relay_settle_s)
        for _ in range(readings_per_block):
            yield from bps.trigger_and_read(detectors)

    @bpp.stage_decorator([box, meter, mux])
    @bpp.run_decorator(md=_md)
    def measure():
        yield from bps.mv(meter.function, "RESISTANCE", mux.route, OPEN)
        yield from block(RESISTOR_RELAY, ALONE)
        for point in box_points_ohm:
            yield from bps.mv(box.setpoint, point)
            yield from block(BOX_AND_RESISTOR_RELAY, TOGETHER)
        yield from block(RESISTOR_RELAY, ALONE)

    def restore():
        yield from bps.mv(box.setpoint, original)

    return (yield from bpp.finalize_wrapper(measure(), restore()))


@dataclass(frozen=True)
class SeriesResistor:
    ohm: float
    uncertainty_ohm: float
    meter_gain: float
    drift_ohm: float
    run_uid: str


def analyze(path: Path | str) -> SeriesResistor:
    run = load_run(path)
    table = run.table()
    alone = table[table.channel_name == ALONE].gdm_reading
    together = table[table.channel_name == TOGETHER]
    if (table.gdm_reading > OVERLOAD_THRESHOLD).any():
        raise ValueError(
            f"Run {run.start['uid']} has overload readings, so the meter saw an open "
            "circuit. Check the relays and wiring, then measure again."
        )
    if table.gdm_range.nunique() != 1:
        raise ValueError(
            "The readings span meter ranges, so one gain does not cover them."
        )
    by_box = together.groupby("qr101b_calculated").gdm_reading.mean()
    gain, _ = np.polyfit(by_box.index.to_numpy(), by_box.to_numpy(), 1)
    ohm = float(alone.mean() / gain)
    half = len(alone) // 2
    return SeriesResistor(
        ohm=ohm,
        uncertainty_ohm=float(np.hypot(BOX_ACCURACY_FRACTION * ohm, RESOLUTION_OHM)),
        meter_gain=float(gain),
        drift_ohm=float(alone.iloc[half:].mean() - alone.iloc[:half].mean()),
        run_uid=run.start["uid"],
    )


def latest_measurement(runs_dir: Path | str = RUNS_DIR) -> SeriesResistor | None:
    """The newest finished measurement in a folder of runs, if there is one."""
    paths = sorted(Path(runs_dir).glob(f"*_{PLAN_NAME}_*.jsonl"))
    for path in reversed(paths):
        if load_run(path).stop["exit_status"] == "success":
            return analyze(path)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--simulate", action="store_true", help="Use simulated instruments."
    )
    parser.add_argument("--readings", type=int, default=20, help="Readings per block.")
    parser.add_argument("--qr-port", help="Default: the only CH340 port.")
    parser.add_argument("--output", type=Path, default=RUNS_DIR)
    args = parser.parse_args()

    bench = open_bench(args.simulate, args.qr_port)
    output = args.output / "simulated" if args.simulate else args.output
    start = {}
    try:
        make_run_engine(output)(
            series_resistor(
                bench.box, bench.meter, bench.mux, readings_per_block=args.readings
            ),
            lambda name, doc: start.update(doc) if name == "start" else None,
        )
    finally:
        bench.close()

    path = output / f"{run_file_name(start)}.jsonl"
    result = analyze(path)
    print(f"Run written to {path}")
    print(f"Series resistor: {result.ohm:.1f} +/- {result.uncertainty_ohm:.1f} ohm")
    print(f"Meter gain against the box: {1e6 * (result.meter_gain - 1):+.0f} ppm")
    print(f"Drift between the first and last readings: {result.drift_ohm:+.2f} ohm")


if __name__ == "__main__":
    main()
