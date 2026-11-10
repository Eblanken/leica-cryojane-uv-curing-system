"""Exercises the QR101B over serial, then restores its original setpoint.

This actuates the box's relays, and the output can briefly open or short while
they switch, so disconnect it from anything sensitive first.

Passing shows the serial link works and the box accepted each setpoint. PV is
the box's calculated value, so this does not prove the actual resistance.
Measure that with a meter.

    pixi run python scripts/instrument_checks/verify_qr101b.py [COM6]
"""

from __future__ import annotations

import sys
import time

from qr101b.driver import Qr101b, find_ports

TEST_POINTS_OHM = (1000.0, 470.0, 750.5)
MAX_CALCULATED_ERROR_OHM = 0.5


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else next(iter(find_ports()), None)
    if port is None:
        print("No CH340 serial port found.")
        return 1

    failures = 0
    with Qr101b(port) as box:
        print(f"{box.device_type} S/N {box.serial_number}, FW {box.firmware_version}, HW {box.hardware_version}")
        original = box.setpoint_ohm
        print(f"Original setpoint {original} ohm, limit {box.limit_ohm} ohm, {box.internal_temperature_c} C")
        try:
            for target in TEST_POINTS_OHM:
                state = box.set_resistance(target)
                time.sleep(1.0)  # Recommended maximum relay rate is 1 Hz.
                readback = box.setpoint_ohm
                calculated = box.calculated_ohm
                ok = abs(readback - target) < 0.01 and abs(calculated - target) < MAX_CALCULATED_ERROR_OHM
                failures += not ok
                print(f"{'ok  ' if ok else 'FAIL'} set {target}: SP {readback}, PV {calculated}, UMax {state.max_voltage_v} V")
        finally:
            box.set_resistance(original)
            print(f"Restored setpoint to {box.setpoint_ohm} ohm")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
