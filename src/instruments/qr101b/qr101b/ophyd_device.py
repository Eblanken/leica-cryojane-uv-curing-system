"""Bluesky device for the QR101B. All behaviour lives in driver.py."""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd_adapters import DriverDevice, DriverSignal


class Qr101bDevice(DriverDevice):
    """Move `setpoint` to change the output. Each change waits out the driver's rate limit."""

    setpoint = Cpt(
        DriverSignal,
        getter="setpoint_ohm",
        setter="set_resistance",
        units="ohm",
        kind="hinted",
    )
    calculated = Cpt(DriverSignal, getter="calculated_ohm", units="ohm", kind="normal")
    internal_temperature = Cpt(
        DriverSignal, getter="internal_temperature_c", units="degC", kind="normal"
    )
    limit = Cpt(
        DriverSignal, getter="limit_ohm", setter="set_limit", units="ohm", kind="config"
    )
    user_calibration_enabled = Cpt(
        DriverSignal,
        getter="user_calibration_enabled",
        setter="set_user_calibration_enabled",
        kind="config",
    )
    device_type = Cpt(DriverSignal, getter="device_type", kind="config")
    serial_number = Cpt(DriverSignal, getter="serial_number", kind="config")
    firmware = Cpt(DriverSignal, getter="firmware_version", kind="config")
    hardware = Cpt(DriverSignal, getter="hardware_version", kind="config")
    production_date = Cpt(DriverSignal, getter="production_date", kind="config")
    tcr = Cpt(DriverSignal, getter="tcr_ppm", units="ppm/K", kind="config")
    port = Cpt(DriverSignal, getter="port", kind="config")
