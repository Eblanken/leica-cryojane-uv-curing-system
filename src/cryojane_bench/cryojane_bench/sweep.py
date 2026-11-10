"""Mux-and-meter sweep: read each mux channel in turn and append to a results log."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Protocol

from cryojane_bench.logs import format_result_line


class QuantityType(Enum):
    VOLTAGE = auto()
    RESISTANCE = auto()


@dataclass(frozen=True)
class SweepChannel:
    name: str
    quantity: QuantityType
    relay_index: int


class Meter(Protocol):
    def read_voltage(self) -> float: ...
    def read_resistance(self) -> float: ...


class Mux(Protocol):
    def select(self, channel: int | None) -> None: ...


def run_sweep(
    mux: Mux,
    meter: Meter,
    channels: list[SweepChannel],
    output_path: Path | str,
    repetitions_per_block: int = 10,
    settle_s: float = 0.5,
    keep_running: Callable[[], bool] = lambda: True,
) -> None:
    """Cycles through `channels` until `keep_running` returns False, flushing every reading.

    Each cycle is flushed as it goes, so a long run keeps its data if it is interrupted.
    """
    readers = {QuantityType.VOLTAGE: meter.read_voltage, QuantityType.RESISTANCE: meter.read_resistance}
    with open(output_path, mode="a", newline="", encoding="utf-8") as file:
        while keep_running():
            for channel in channels:
                mux.select(channel.relay_index)
                time.sleep(settle_s)
                for _ in range(repetitions_per_block):
                    value = readers[channel.quantity]()
                    file.write(format_result_line(datetime.now(), channel.name, value, channel.quantity.name))
                    file.flush()
