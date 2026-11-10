"""Bluesky device for the Analog Discovery 3. All behaviour lives in driver.py."""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd_adapters import DriverDevice, DriverSignal, MeasurementSignal


def _last(field: str, units: str, kind: str = "normal") -> Cpt:
    return Cpt(
        DriverSignal,
        getter=lambda driver: getattr(driver.last, field),
        units=units,
        kind=kind,
    )


class Ad3Device(DriverDevice):
    """One capture per trigger. The other readings come from that same capture."""

    pin_high_fraction = Cpt(
        MeasurementSignal,
        getter=lambda driver: driver.measure().pin_high_fraction,
        kind="hinted",
    )
    pin_frequency_hz = _last("pin_frequency_hz", "Hz")
    pin_rising_edges = _last("pin_rising_edges", "")
    ch1_mean_v = _last("ch1_mean_v", "V")
    ch1_min_v = _last("ch1_min_v", "V")
    ch1_max_v = _last("ch1_max_v", "V")
    ch2_mean_v = _last("ch2_mean_v", "V")
    ch2_min_v = _last("ch2_min_v", "V")
    ch2_max_v = _last("ch2_max_v", "V")
    window_s = Cpt(DriverSignal, getter="window_s", units="s", kind="config")
    digital_pin = Cpt(DriverSignal, getter="digital_pin", kind="config")
    range_v = Cpt(DriverSignal, getter="range_v", units="V", kind="config")
    model = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.model, kind="config"
    )
    serial_number = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.serial_number, kind="config"
    )
    sdk_version = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.sdk_version, kind="config"
    )
