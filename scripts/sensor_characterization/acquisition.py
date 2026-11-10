"""Bluesky plan for the passive sensor sweep: read each mux channel in turn with the GDM-8341.

Run it while the thermal chamber steps through its profile, with the HOBO logger in
the chamber as the reference. The boards are unpowered, so the meter reads resistance.
Each reading is one event in the run's primary stream, with its mux channel and channel
name. Every reading is flushed to disk as it is taken, so an interrupted run keeps its
data, and Ctrl-C ends the run cleanly.

Wiring:
- DMM negative goes to the common line.
- All relay center contacts go to DMM positive. Normally closed contacts are unused.
- Each normally open contact goes to the node being measured.

    pixi run python scripts/sensor_characterization/acquisition.py
    pixi run python scripts/sensor_characterization/acquisition.py --simulate --cycles 2
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from ophyd import Signal

PLAN_NAME = "mux_sweep"
RUNS_DIR = Path(__file__).parent / "runs"

# Relay index to channel name, as wired for the October 2026 runs.
CHANNELS = {0: "Sensor (0)", 1: "Heater (0)", 2: "Sensor (1)", 3: "Heater (1)"}


def mux_sweep(
    mux,
    meter,
    channels: dict[int, str],
    *,
    function: str = "RESISTANCE",
    readings_per_block: int = 10,
    settle_s: float = 0.5,
    cycles: int | None = None,
    md: dict | None = None,
):
    """Cycles through `channels`, `cycles` times or until stopped.

    Args:
        function: GDM-8341 function name. RESISTANCE for a passive run, VOLTAGE_DC
            for a powered one.
        readings_per_block: Readings per channel visit.
        settle_s: Wait after each relay change before reading.
    """
    channel_name = Signal(name="channel_name", value="", kind="hinted")
    _md = {
        "plan_name": PLAN_NAME,
        "experiment": "sensor_characterization",
        "channels": {str(index): name for index, name in channels.items()},
        "function": function,
        "readings_per_block": readings_per_block,
        "settle_s": settle_s,
        "hints": {"dimensions": [(["time"], "primary")]},
        **(md or {}),
    }

    @bpp.stage_decorator([mux, meter])
    @bpp.run_decorator(md=_md)
    def sweep():
        yield from bps.mv(meter.function, function)
        cycle = 0
        while cycles is None or cycle < cycles:
            for index, name in channels.items():
                yield from bps.mv(mux.channel, index, channel_name, name)
                yield from bps.sleep(settle_s)
                for _ in range(readings_per_block):
                    yield from bps.trigger_and_read([meter, mux, channel_name])
            cycle += 1

    return (yield from sweep())


def _simulated_instruments():
    import numpy as np
    from gdm8341.driver import Gdm8341
    from gdm8341.sim import SimulatedGdm8341
    from xl9535_relay_mux.driver import I2cBridge, RelayMux
    from xl9535_relay_mux.sim import SimulatedBridge

    bridge = SimulatedBridge()
    nominal = {0: 2_400.0, 1: 126.0, 2: 2_500.0, 3: 121.0}
    rng = np.random.default_rng(0)

    def measured(function):
        closed = bridge.closed_channels
        if not closed:
            return 9.9e37
        return nominal.get(closed[0], 9.9e37) * (1 + rng.normal(0, 1e-4))

    mux = RelayMux(bridge=I2cBridge(interface=bridge))
    return mux, Gdm8341(transport=SimulatedGdm8341(measured))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--simulate", action="store_true", help="Use simulated instruments."
    )
    parser.add_argument(
        "--voltage", action="store_true", help="Read DC volts, for powered runs."
    )
    parser.add_argument("--cycles", type=int, help="Stop after this many passes.")
    parser.add_argument("--output", type=Path, default=RUNS_DIR)
    args = parser.parse_args()

    from cryojane_bench.runs import make_run_engine
    from gdm8341.driver import Gdm8341
    from gdm8341.driver import find_port as find_gdm_port
    from gdm8341.ophyd_device import Gdm8341Device
    from xl9535_relay_mux.driver import RelayMux
    from xl9535_relay_mux.driver import find_port as find_mux_port
    from xl9535_relay_mux.ophyd_device import RelayMuxDevice

    if args.simulate:
        mux_driver, meter_driver = _simulated_instruments()
        output = args.output / "simulated"
    else:
        mux_driver, meter_driver = RelayMux(find_mux_port()), Gdm8341(find_gdm_port())
        output = args.output

    mux = RelayMuxDevice(mux_driver, name="mux")
    meter = Gdm8341Device(meter_driver, name="gdm")
    print(
        f"{meter_driver.identity.model} on {meter_driver.port}, mux on {mux_driver.port}"
    )
    RE = make_run_engine(output)
    try:
        RE(
            mux_sweep(
                mux,
                meter,
                CHANNELS,
                function="VOLTAGE_DC" if args.voltage else "RESISTANCE",
                cycles=args.cycles,
            )
        )
    except KeyboardInterrupt:
        RE.stop()
    finally:
        mux_driver.close()
        meter_driver.close()
    print(f"Runs are in {output}")


if __name__ == "__main__":
    main()
