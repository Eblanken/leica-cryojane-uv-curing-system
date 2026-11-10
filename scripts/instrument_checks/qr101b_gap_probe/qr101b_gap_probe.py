"""Bluesky run: map where the QR101B's output misses its setpoint.

Run d80a63b6 found the box missing its setpoint between about 12.15 and 17.26 kohm,
always landing on one of those two values. This run checks the exact edges of that gap,
whether the choice flips at the midpoint between the two values, as it would if the box
picks the nearest value it can make, and whether there are other gaps up to 600 kohm.

Wiring: QR101B terminals straight to the GDM-8341 V/ohm and COM inputs, nothing else.
The relays change at most once per second, and the box's setpoint is restored at the end.

    pixi run python scripts/instrument_checks/qr101b_gap_probe/qr101b_gap_probe.py
    pixi run python scripts/instrument_checks/qr101b_gap_probe/qr101b_gap_probe.py --scan parallel

--scan parallel covers the setpoints a 22 kohm resistor in parallel with the box would
need for a 9.9 to 19.3 kohm total, most closely around the gap that ends at 80.46 kohm.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryojane_bench.runs import load_run, make_run_engine, run_file_name
from gdm8341.driver import Gdm8341, find_port
from gdm8341.ophyd_device import Gdm8341Device
from qr101b.driver import Qr101b, find_ports
from qr101b.ophyd_device import Qr101bDevice

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "qr101b_gdm8341_validation"))
from qr101b_gdm8341_validation import resistance_validation

PLAN_NAME = "qr101b_gap_probe"
MISSED_LIMIT_OHM = 0.1
LOWER_EDGE = [12_140, 12_150, 12_154, 12_155, 12_160, 12_200, 12_400, 13_000, 14_000]
MIDPOINT = [14_700, 14_705, 14_707, 14_708, 14_710, 14_715]
UPPER_EDGE = [17_250, 17_260, 17_261, 17_262, 17_265, 17_270, 17_300, 17_350]
# 29.4 to 34.5 kohm and 46.7 to 51.8 kohm are where the gap would repeat if it came from
# adding the 17.26 kohm value to everything below the gap.
WIDE_SCAN = [
    100,
    1_000,
    2_000,
    29_000,
    29_415,
    30_000,
    31_000,
    32_000,
    34_000,
    34_500,
    35_000,
    40_000,
    46_000,
    47_000,
    48_000,
    50_000,
    52_000,
    60_000,
    80_000,
    100_000,
    150_000,
    200_000,
    300_000,
    400_000,
    500_000,
    600_000,
]
PARALLEL_SCAN = [
    18_000,
    20_000,
    22_000,
    24_000,
    26_000,
    27_000,
    33_000,
    36_000,
    38_000,
    *range(56_000, 70_000, 2_000),
    *range(70_000, 82_000, 1_000),
    *range(82_000, 92_000, 2_000),
    95_000,
    110_000,
    125_000,
    140_000,
    160_000,
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scan", choices=["edges", "parallel"], default="edges")
    args = parser.parse_args()
    (qr_port,) = find_ports()
    box_driver = Qr101b(qr_port)
    meter_driver = Gdm8341(find_port())
    box = Qr101bDevice(box_driver, name="qr101b")
    meter = Gdm8341Device(meter_driver, name="gdm")
    edges = [*LOWER_EDGE, *MIDPOINT, *UPPER_EDGE, *WIDE_SCAN]
    points = [float(p) for p in (edges if args.scan == "edges" else PARALLEL_SCAN)]
    output = HERE / "data"
    started = {}
    try:
        make_run_engine(output)(
            resistance_validation(
                box,
                meter,
                points,
                readings_per_point=1,
                dwell_s=0.5,
                md={
                    "plan_name": PLAN_NAME,
                    "purpose": "Map where the QR101B misses its setpoint.",
                    "procedure": "scripts/instrument_checks/qr101b_gap_probe",
                    "scan": args.scan,
                },
            ),
            lambda name, doc: started.update(doc) if name == "start" else None,
        )
    finally:
        print(f"Setpoint left at {box_driver.setpoint_ohm} ohm")
        box_driver.close()
        meter_driver.close()

    path = output / f"{run_file_name(started)}.jsonl"
    print(f"Run written to {path}")
    print(f"{'setpoint':>10} {'PV':>12} {'GDM':>12} {'PV-SP':>10}")
    for row in load_run(path).table().itertuples():
        error = row.qr101b_calculated - row.qr101b_setpoint
        flag = "  MISSED" if abs(error) > MISSED_LIMIT_OHM else ""
        print(
            f"{row.qr101b_setpoint:10.1f} {row.qr101b_calculated:12.3f} "
            f"{row.gdm_reading:12.2f} {error:+10.3f}{flag}"
        )


if __name__ == "__main__":
    main()
