"""Wiring of the duty-cycle bench, in one place, and how to open its instruments.

One QR101B in series with one fixed resistor of about 9.1 kohm stands in for the
thermistor of either target device. Both devices have 10 kohm dummy heater loads.

Node names: A is QR101B terminal A. M is the junction of QR101B B and the resistor.

    A ---- QR101B ---- M ---- 9.1 kohm ---- common GND

    Common GND ---- DMM COM, and GND (P1.2) of both boards

    Meter relays: every COM goes to DMM V/ohm. Each NO goes to the node below.
      Relay 0   Device 0 HEATER_SENSE (P1.3)
      Relay 1   Device 0 HEATER_LOAD (P1.4)
      Relay 2   Device 1 HEATER_SENSE (P1.3)
      Relay 3   Device 1 HEATER_LOAD (P1.4)
      Relay 6   A, to read the QR101B and resistor together
      Relay 7   M, to read the resistor alone

    Routing relays: keep these apart from DMM V/ohm.
      Relay 4   COM to device 0 HEATER_SENSE, NO to A, NC to GND
      Relay 5   COM to device 1 HEATER_SENSE, NO to A, NC to GND

    Each device: +rail (P1.1) ---- 10 kohm dummy load ---- HEATER_LOAD (P1.4)

Unplug both thermistors and both heaters. Pin numbers are for the PAD sheet.

A device that is not routed has its sense input held at GND through the normally closed
contact. That is below the lowest comparator setting (1.25 V, 3.33 kohm), so its heater
stays off, and this also holds if the mux loses power or a run stops. The board's 10 kohm
pull-up limits the current to 0.5 mA.

The mux bridge's watchdog is armed with MUX_WATCHDOG_S. If this host stops talking to
it, because it crashed, lost power or lost USB, the bridge opens every relay, its stored
safe state. That needs bridge firmware 2.0.

Check before powering the boards:
- The dummy loads see the full rail. The schematic labels it +60 V, which is 0.36 W, so
  use resistors rated for at least 0.5 W.
- This wiring ties the boards' grounds together. Confirm that is safe for their supplies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

RUNS_DIR = Path(__file__).parent / "runs"

DEVICES = {
    0: {
        "sensor_relay": 0,
        "heater_relay": 1,
        "route_relay": 4,
        "regulation_ohm": 17_580.0,
    },
    1: {
        "sensor_relay": 2,
        "heater_relay": 3,
        "route_relay": 5,
        "regulation_ohm": 11_000.0,
    },
}
BOX_AND_RESISTOR_RELAY = 6
RESISTOR_RELAY = 7
MUX_GROUPS = {
    "default": (0, 1, 2, 3, RESISTOR_RELAY, BOX_AND_RESISTOR_RELAY),
    "route": (4, 5),
}
IDLE_SENSE_OHM = 0.0
# The AD3 is only wired to this device. It listens, and common GND is its reference.
AD3_DEVICE = 0
AD3_WIRING = {
    "d0": "U101.6, HEATER_PWM-INV, low means the transistor is on",
    "ch1": "HEATER_LOAD (P1.4), through a probe assumed to be x10",
    "ch2": "not connected",
}
# The board's sense divider. Device 1 measured about 0.28 V above this on 2026-11-06.
SENSE_SUPPLY_V, SENSE_PULL_UP_OHM = 5.0, 10_000.0

_SIMULATED_SERIES_OHM = 9_087.0


def expected_sense_v(total_ohm: float) -> float:
    """The sense voltage the board's pull-up should give with this resistance."""
    return SENSE_SUPPLY_V * total_ohm / (total_ohm + SENSE_PULL_UP_OHM)


@dataclass
class Bench:
    box: Any
    meter: Any
    mux: Any
    simulated: bool
    ad3: Any = None

    def close(self) -> None:
        self.mux.driver.close()
        self.meter.driver.close()
        self.box.driver.close()
        if self.ad3 is not None:
            self.ad3.driver.close()


MUX_WATCHDOG_S = 5.0
# The meter sometimes misses replies in device 0's PWM band. Retries wait 2 s, and from
# the second on also reopen the port, so this rides out about half a minute.
METER_READ_RETRIES = 8
AD3_OPEN_ATTEMPTS = 5  # Opening fails now and then on the Pi, as captures do.


def _open_ad3(ad3_class):
    for attempt in range(1, AD3_OPEN_ATTEMPTS + 1):
        try:
            return ad3_class()
        except Exception as error:
            if attempt == AD3_OPEN_ATTEMPTS:
                raise
            print(f"AD3 open {attempt} failed, retrying: {str(error).splitlines()[0]}")
            time.sleep(2.0)
    raise AssertionError("unreachable")


def _simulated_drivers():
    """Made-up boards, and a meter that reads low with a small offset, like the real one."""
    from ad3.driver import Ad3
    from ad3.sim import SimulatedDwf
    from gdm8341.driver import Function, Gdm8341
    from gdm8341.sim import SimulatedGdm8341
    from qr101b.driver import Qr101b
    from qr101b.sim import SimulatedQr101b
    from xl9535_relay_mux.driver import I2cBridge, RelayMux
    from xl9535_relay_mux.sim import SimulatedBridge

    box_sim = SimulatedQr101b(calculated_error_ppm=0.0)
    bridge = SimulatedBridge()
    rng = np.random.default_rng(0)
    band_ohm, v_off, v_sat, pwm_hz = 1_500.0, 24.0, 0.5, 100.0

    def duty_of(relays, ohms):
        on = (ohms - relays["regulation_ohm"]) / band_ohm + 0.5
        return float(np.clip(on, 0.0, 1.0))

    def scope(times):
        """Device 0's PWM, and the low side through a x10 probe. CH2 is not connected."""
        relays = DEVICES[AD3_DEVICE]
        routed = relays["route_relay"] in bridge.closed_channels
        ohms = _SIMULATED_SERIES_OHM + box_sim.calculated if routed else IDLE_SENSE_OHM
        transistor_on = ((times * pwm_hz) % 1.0) < duty_of(relays, ohms)
        low_side = np.where(transistor_on, v_sat, v_off) / 10
        return low_side, np.full_like(times, 0.03), (~transistor_on).astype(int)

    def measured(function):
        closed = bridge.closed_channels
        total = _SIMULATED_SERIES_OHM + box_sim.calculated
        if function is Function.VOLTAGE_AC:
            for relays in DEVICES.values():
                if relays["heater_relay"] in closed:
                    routed = relays["route_relay"] in closed
                    duty = duty_of(relays, total if routed else IDLE_SENSE_OHM)
                    return (v_off - v_sat) * np.sqrt(duty * (1 - duty))
            return 0.0
        if function is Function.RESISTANCE:
            if RESISTOR_RELAY in closed:
                ohms = _SIMULATED_SERIES_OHM
            elif BOX_AND_RESISTOR_RELAY in closed:
                ohms = total
            else:
                return 9.9e37
            return round(ohms * (1 - 600e-6) + 0.2 + rng.normal(0, 0.3))
        for relays in DEVICES.values():
            ohms = total if relays["route_relay"] in closed else IDLE_SENSE_OHM
            if relays["sensor_relay"] in closed:
                return expected_sense_v(ohms) + rng.normal(0, 1e-3)
            if relays["heater_relay"] in closed:
                duty = duty_of(relays, ohms)
                return v_off * (1 - duty) + v_sat * duty + rng.normal(0, 0.02)
        return 0.0

    return (
        Qr101b(transport=box_sim, min_change_interval_s=0.0),
        Gdm8341(transport=SimulatedGdm8341(measured)),
        RelayMux(bridge=I2cBridge(interface=bridge), groups=MUX_GROUPS),
        Ad3(dwf=SimulatedDwf(scope)),
    )


def open_bench(
    simulate: bool = False, qr_port: str | None = None, ad3: bool = False
) -> Bench:
    from ad3.driver import Ad3
    from ad3.ophyd_device import Ad3Device
    from gdm8341.driver import Gdm8341
    from gdm8341.driver import find_port as find_gdm_port
    from gdm8341.ophyd_device import Gdm8341Device
    from qr101b.driver import Qr101b, find_ports
    from qr101b.ophyd_device import Qr101bDevice
    from xl9535_relay_mux.driver import RelayMux
    from xl9535_relay_mux.driver import find_port as find_mux_port
    from xl9535_relay_mux.ophyd_device import RoutedRelayMuxDevice

    if simulate:
        box, meter, mux, scope = _simulated_drivers()
    else:
        ports = [qr_port] if qr_port else find_ports()
        if len(ports) != 1:
            raise SystemExit(f"Expected one CH340 port, found {ports}. Pass --qr-port.")
        box = Qr101b(ports[0])
        meter = Gdm8341(find_gdm_port(), read_retries=METER_READ_RETRIES)
        mux = RelayMux(find_mux_port(), groups=MUX_GROUPS, watchdog_s=MUX_WATCHDOG_S)
        scope = _open_ad3(Ad3) if ad3 else None
    return Bench(
        Qr101bDevice(box, name="qr101b"),
        Gdm8341Device(meter, name="gdm"),
        RoutedRelayMuxDevice(mux, name="mux"),
        simulate,
        Ad3Device(scope, name="ad3") if ad3 else None,
    )
