"""Bluesky plan: step the QR101B and read it with the GDM-8341, to check one against the other.

This is stage 1 of docs/setpoint_bench_test.md. The default points cover the
regulation span the heater board's R106 trimmer can set, 1.25 V to 3.75 V at the sense
comparator with the 10 kohm pull-up to +5 V (3.33 kohm to 30 kohm), with 10% overreach
at each end. They are denser within 10% of each machine's measured regulation point
(heater_characterization). The sweep goes up and then back down, to show hysteresis.

Wiring: QR101B terminals straight to the GDM-8341 V/ohm and COM inputs. Pass --relay
to route through a mux relay instead. Keep the QR101B off the heater board: an
ohmmeter reading on a powered circuit is invalid.

The QR101B's relays switch at most once per second (the manual's recommended rate),
and its original setpoint is restored at the end, even if the run fails.

    pixi run python scripts/instrument_checks/qr101b_gdm8341_validation/qr101b_gdm8341_validation.py
    pixi run python scripts/instrument_checks/qr101b_gdm8341_validation/qr101b_gdm8341_validation.py --simulate
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from cryojane_bench.runs import load_run, make_run_engine, run_file_name
from cryojane_bench.thermistor import divider_voltage_to_resistance

OUTPUT_DIR = Path(__file__).parent / "data"
PLAN_NAME = "qr101b_gdm8341_validation"

COMPARATOR_SPAN_V = (1.25, 3.75)  # R106 trimmer range, from the heater board capture.
REGULATION_POINTS_OHM = {"machine 0": 17_580.0, "machine 1": 11_000.0}
OVERREACH = 0.10


def default_points_ohm() -> list[float]:
    """Coarse log steps over the trimmer span plus 2% steps around each regulation point."""
    low, high = divider_voltage_to_resistance(COMPARATOR_SPAN_V)
    coarse = np.geomspace(low * (1 - OVERREACH), high * (1 + OVERREACH), 16)
    fine = [
        point * (1 + offset)
        for point in REGULATION_POINTS_OHM.values()
        for offset in np.linspace(-OVERREACH, OVERREACH, 11)
    ]
    upward = sorted({round(float(p), -1) for p in [*coarse, *fine]})
    return upward + upward[-2::-1]


def resistance_validation(
    box,
    meter,
    points_ohm: list[float],
    *,
    readings_per_point: int = 5,
    dwell_s: float = 2.0,
    mux=None,
    relay: int | None = None,
    md: dict | None = None,
):
    """Steps `box` through `points_ohm`, taking `readings_per_point` meter readings at each."""
    devices = [box, meter] + ([mux] if mux is not None else [])
    _md = {
        "plan_name": PLAN_NAME,
        "experiment": "instrument_checks",
        "purpose": "Compare GDM-8341 2-wire resistance with the QR101B setpoint and PV.",
        "procedure": "docs/setpoint_bench_test.md, stage 1",
        "wiring": (
            "QR101B terminals direct to GDM-8341 V/ohm and COM"
            if mux is None
            else f"QR101B A through mux relay {relay} NO-COM to GDM-8341 V/ohm, B to COM"
        ),
        "points_ohm": list(points_ohm),
        "readings_per_point": readings_per_point,
        "dwell_s": dwell_s,
        "hints": {"dimensions": [([box.setpoint.name], "primary")]},
        **(md or {}),
    }
    original = yield from bps.rd(box.setpoint)

    @bpp.stage_decorator(devices)
    @bpp.run_decorator(md=_md)
    def sweep():
        yield from bps.mv(meter.function, "RESISTANCE")
        if mux is not None:
            yield from bps.mv(mux.channel, relay)
        for point in points_ohm:
            yield from bps.mv(box.setpoint, point)
            yield from bps.sleep(dwell_s)
            for _ in range(readings_per_point):
                yield from bps.trigger_and_read(devices)

    def restore():
        yield from bps.mv(box.setpoint, original)

    return (yield from bpp.finalize_wrapper(sweep(), restore()))


def summarize(path: Path) -> pd.DataFrame:
    """Median meter reading at each step, against the box's setpoint and PV."""
    run = load_run(path)
    table = run.table()
    step = (table.qr101b_setpoint.diff() != 0).cumsum()
    direction = np.where(step <= (step.max() + 1) // 2, "up", "down")
    summary = table.groupby([step, direction]).agg(
        setpoint_ohm=("qr101b_setpoint", "first"),
        calculated_ohm=("qr101b_calculated", "first"),
        gdm_median_ohm=("gdm_reading", "median"),
        gdm_std_ohm=("gdm_reading", "std"),
        gdm_range_ohm=("gdm_range", "first"),
    )
    # The box's PV says which value its relays actually produce, so the two errors
    # separate a box that missed its setpoint from a meter that misread the box.
    summary["pv_vs_setpoint_ppm"] = 1e6 * (
        summary.calculated_ohm / summary.setpoint_ohm - 1
    )
    summary["gdm_vs_pv_ppm"] = 1e6 * (
        summary.gdm_median_ohm / summary.calculated_ohm - 1
    )
    summary["error_ppm"] = 1e6 * (summary.gdm_median_ohm / summary.setpoint_ohm - 1)
    summary.index.names = ["step", "direction"]
    return summary.reset_index(level="step", drop=True)


def _simulated_instruments():
    from gdm8341.driver import Function, Gdm8341
    from gdm8341.sim import SimulatedGdm8341
    from qr101b.driver import Qr101b
    from qr101b.sim import SimulatedQr101b

    box_sim = SimulatedQr101b()
    rng = np.random.default_rng(0)

    def measured(function):
        if function is not Function.RESISTANCE:
            return 0.0
        return box_sim.setpoint * (1 + 150e-6) + 0.25 + rng.normal(0, 0.3)

    return Qr101b(transport=box_sim, min_change_interval_s=0.0), Gdm8341(
        transport=SimulatedGdm8341(measured)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--simulate", action="store_true", help="Use simulated instruments."
    )
    parser.add_argument("--gdm-port", help="Default: found by USB identity.")
    parser.add_argument("--qr-port", help="Default: the only CH340 port.")
    parser.add_argument("--relay", type=int, help="Route through this mux relay.")
    parser.add_argument(
        "--readings", type=int, default=5, help="Meter readings per point."
    )
    parser.add_argument(
        "--dwell", type=float, default=2.0, help="Seconds before reading."
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    from gdm8341.driver import Gdm8341, find_port
    from gdm8341.ophyd_device import Gdm8341Device
    from qr101b.driver import Qr101b, find_ports
    from qr101b.ophyd_device import Qr101bDevice

    mux = None
    if args.simulate:
        box_driver, meter_driver = _simulated_instruments()
        output = args.output / "simulated"
    else:
        qr_ports = [args.qr_port] if args.qr_port else find_ports()
        if len(qr_ports) != 1:
            raise SystemExit(
                f"Expected one CH340 port, found {qr_ports}. Pass --qr-port."
            )
        box_driver = Qr101b(qr_ports[0])
        meter_driver = Gdm8341(args.gdm_port or find_port())
        output = args.output
        if args.relay is not None:
            from xl9535_relay_mux.driver import RelayMux
            from xl9535_relay_mux.driver import find_port as find_mux_port
            from xl9535_relay_mux.ophyd_device import RelayMuxDevice

            mux = RelayMuxDevice(RelayMux(find_mux_port()), name="mux")

    box = Qr101bDevice(box_driver, name="qr101b")
    meter = Gdm8341Device(meter_driver, name="gdm")
    points = default_points_ohm()
    print(
        f"{box_driver.device_type} on {box_driver.port}, {meter_driver.identity.model} on "
        f"{meter_driver.port}: {len(points)} steps, {points[0]:.0f} to {max(points):.0f} ohm"
    )

    RE = make_run_engine(output)
    started = {}

    def progress(name, document):
        if name == "start":
            started.update(document)
        elif name == "event" and document["seq_num"] % args.readings == 0:
            data = document["data"]
            print(
                f"  {document['seq_num'] // args.readings:3d}/{len(points)} "
                f"set {data['qr101b_setpoint']:9.1f}  GDM {data['gdm_reading']:10.2f} ohm",
                flush=True,
            )

    try:
        RE(
            resistance_validation(
                box,
                meter,
                points,
                readings_per_point=args.readings,
                dwell_s=args.dwell,
                mux=mux,
                relay=args.relay,
            ),
            progress,
        )
    finally:
        box_driver.close()
        meter_driver.close()
        if mux is not None:
            mux.driver.close()

    path = output / f"{run_file_name(started)}.jsonl"
    print(f"\nRun written to {path}\n")
    with pd.option_context("display.width", 140, "display.max_rows", 200):
        print(
            summarize(path).round(
                {
                    "gdm_std_ohm": 2,
                    "pv_vs_setpoint_ppm": 0,
                    "gdm_vs_pv_ppm": 0,
                    "error_ppm": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
