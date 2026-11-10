"""Ophyd signals backed by plain driver methods.

Each instrument under src/instruments has a driver with no Bluesky imports and an
ophyd device that is only a list of Components built from these classes. Exposing a
driver feature to Bluesky is then one line:

    class Meter(DriverDevice):
        reading = Cpt(MeasurementSignal, getter="measure", kind="hinted")
        rate = Cpt(DriverSignal, getter="rate", setter="set_rate", kind="config")

`getter` and `setter` name an attribute of the device's driver, or are callables that
take the driver. A getter may be a property or a method with no arguments. `units`
may also be a callable taking the driver, for readings whose units follow a setting.
"""

from __future__ import annotations

import functools
import logging
import operator
import threading
from collections.abc import Callable
from typing import Any

from ophyd import Device, Signal
from ophyd.status import Status
from ophyd.utils import ReadOnlyError

__all__ = ["DriverDevice", "DriverSignal", "MeasurementSignal"]

logger = logging.getLogger(__name__)

Accessor = str | Callable[[Any], Any]


class DriverDevice(Device):
    """An ophyd Device that wraps one driver instance.

    trigger() takes a fresh reading on every MeasurementSignal that is part of read(),
    so plans like count and scan measure once per event.
    """

    def __init__(self, driver: Any, *, name: str, **kwargs):
        self.driver = driver
        super().__init__(name=name, **kwargs)

    def trigger(self) -> Status:
        statuses = [
            signal.trigger()
            for attr in self.read_attrs
            if isinstance(signal := getattr(self, attr), MeasurementSignal)
        ]
        done = Status(self)
        done.set_finished()
        return functools.reduce(operator.and_, statuses, done)


class DriverSignal(Signal):
    """A value read from, and optionally written to, the parent device's driver.

    Writes block until the driver returns, then read back what the instrument holds,
    so the recorded value is the instrument's and not the requested one.
    """

    def __init__(
        self,
        *,
        getter: Accessor,
        setter: Accessor | None = None,
        units: str | Callable[[Any], str] | None = None,
        **kwargs,
    ):
        self._getter = getter
        self._setter = setter
        self._units = units
        super().__init__(**kwargs)

    @property
    def driver(self) -> Any:
        return self.parent.driver

    @property
    def write_access(self) -> bool:
        return self._setter is not None

    def _read_driver(self) -> Any:
        if callable(self._getter):
            return self._getter(self.driver)
        value = getattr(self.driver, self._getter)
        return value() if callable(value) else value

    def _store(self, value: Any) -> Any:
        Signal.put(self, value, force=True)
        return value

    def get(self, **kwargs) -> Any:
        return self._store(self._read_driver())

    def put(self, value: Any, **kwargs) -> None:
        if self._setter is None:
            raise ReadOnlyError(f"{self.name} is read-only.")
        if callable(self._setter):
            self._setter(self.driver, value)
        else:
            getattr(self.driver, self._setter)(value)
        self.get()

    def _set_and_wait(self, value: Any, timeout: float | None, **kwargs) -> None:
        # put() already blocks until the driver is done, and the readback may round.
        self.put(value)

    def describe(self) -> dict:
        description = super().describe()
        units = self._units(self.driver) if callable(self._units) else self._units
        if units:
            description[self.name]["units"] = units
        return description


class MeasurementSignal(DriverSignal):
    """A reading taken when the signal is triggered, not each time it is read.

    Reading it returns the last measurement, so read() and describe() never cause an
    extra conversion on the instrument.
    """

    def get(self, **kwargs) -> Any:
        return self._readback

    def trigger(self) -> Status:
        status = Status(self)

        def measure() -> None:
            try:
                self._store(self._read_driver())
            except Exception as error:  # noqa: BLE001
                # Anything the driver raises must reach the RunEngine through the status.
                # Combined statuses hide the exception, so it is also logged here.
                logger.error("%s failed: %r", self.name, error)
                status.set_exception(error)
            else:
                status.set_finished()

        threading.Thread(target=measure, daemon=True).start()
        return status
