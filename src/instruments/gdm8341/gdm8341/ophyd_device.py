"""Bluesky device for the GDM-8341. All behaviour lives in driver.py."""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd_adapters import DriverDevice, DriverSignal, MeasurementSignal

from gdm8341.driver import Function, Gdm8341


def _configure(driver: Gdm8341, function_name: str) -> None:
    driver.configure(Function[function_name])


class Gdm8341Device(DriverDevice):
    """One reading of display 1 per trigger, in whatever function `function` selects.

    Set `function` to a Function name, such as "RESISTANCE", before reading.
    """

    reading = Cpt(
        MeasurementSignal,
        getter="measure_active",
        units=lambda driver: driver.function().units,
        kind="hinted",
    )
    range = Cpt(DriverSignal, getter="range", kind="normal")
    function = Cpt(
        DriverSignal,
        getter=lambda driver: driver.function().name,
        setter=_configure,
        kind="config",
    )
    auto_range = Cpt(DriverSignal, getter="auto_range", kind="config")
    rate = Cpt(
        DriverSignal,
        getter=lambda driver: driver.rate.name,
        setter=lambda driver, name: setattr(driver, "rate", name[0]),
        kind="config",
    )
    input_impedance_10g = Cpt(DriverSignal, getter="input_impedance_10g", kind="config")
    model = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.model, kind="config"
    )
    serial_number = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.serial_number, kind="config"
    )
    firmware = Cpt(
        DriverSignal, getter=lambda driver: driver.identity.version, kind="config"
    )
    port = Cpt(DriverSignal, getter="port", kind="config")
