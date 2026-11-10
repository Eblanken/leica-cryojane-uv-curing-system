"""Bluesky device for the relay mux. All behaviour lives in driver.py."""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd_adapters import DriverDevice, DriverSignal

OPEN = -1  # Event data cannot hold None, so -1 stands for every relay open.
ROUTE_GROUP = "route"


def _relay_or_open(relay: int | None) -> int:
    return OPEN if relay is None else relay


class RelayMuxDevice(DriverDevice):
    """Move `channel` to close one relay, or to OPEN. Unstaging opens every relay."""

    channel = Cpt(
        DriverSignal,
        getter=lambda driver: _relay_or_open(driver.selected),
        setter=lambda driver, channel: driver.select(
            None if channel == OPEN else int(channel)
        ),
        kind="hinted",
    )
    target_address = Cpt(DriverSignal, getter="target_address", kind="config")
    port = Cpt(DriverSignal, getter="port", kind="config")

    def unstage(self):
        self.driver.open_all()
        return super().unstage()


class RoutedRelayMuxDevice(RelayMuxDevice):
    """Adds `route`, which moves a second relay group named ROUTE_GROUP."""

    route = Cpt(
        DriverSignal,
        getter=lambda driver: _relay_or_open(driver.selected_in(ROUTE_GROUP)),
        setter=lambda driver, relay: driver.select(
            None if relay == OPEN else int(relay), ROUTE_GROUP
        ),
        kind="hinted",
    )
