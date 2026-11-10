"""A stand-in for the bridge firmware's RPC interface, for tests and dry runs."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from xl9535_relay_mux.driver import (
    POWER_ON_DEFAULTS,
    STAGED_VALID,
    STATUS_ARMED,
    STATUS_TRIPPED,
    WRITE_TRIPPED,
    Register,
)

FIRMWARE_VERSION = 0x020000


class SimulatedBridge:
    """Implements the firmware's RPC calls against one simulated XL9535 relay board.

    It follows firmware 2.0: the watchdog trips when it is next called after its timeout,
    rather than on its own, which is enough to test the host's side. `clock` replaces
    time.monotonic, and `watchdog=False` behaves like firmware 1, without the calls.
    """

    def __init__(
        self,
        board_address: int = 0x20,
        *,
        watchdog: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.board_address = board_address
        self.registers = {int(k): v for k, v in POWER_ON_DEFAULTS.items()}
        self.transactions: list[tuple[int, bytes]] = []
        self.led = 0
        self.has_watchdog = watchdog
        self.clock = clock
        self.safe = (board_address, 0x00, 0x00)
        self.armed = False
        self.tripped = False
        self.timeout_ms = 0
        self.trip_count = 0
        self.kicks = 0
        self._staged: tuple[int, int, int] | None = None
        self._token = 0
        self._last_call = clock()

    def __getattribute__(self, name: str):
        # Firmware 1 has none of the watchdog calls, so they don't exist on its interface.
        if name in _WATCHDOG_CALLS and not object.__getattribute__(
            self, "has_watchdog"
        ):
            raise AttributeError(name)
        return object.__getattribute__(self, name)

    def _heard(self) -> None:
        now = self.clock()
        if self.armed and (now - self._last_call) * 1000 > self.timeout_ms:
            self.armed = False
            self.tripped = True
            self.trip_count += 1
            self._write_safe_state()
        self._last_call = now

    def _write_registers(self, register: int, values: bytes) -> None:
        # The register pointer advances within a port pair, as the datasheet describes.
        for value in values:
            self.registers[register] = value
            register ^= 1

    def _write_safe_state(self) -> int:
        address, port0, port1 = self.safe
        if address != self.board_address:
            return 2  # Not acknowledged.
        self._write_registers(Register.OUTPUT_PORT_0, bytes([port0, port1]))
        self._write_registers(Register.CONFIGURATION_PORT_0, bytes([0, 0]))
        return 0

    def write_i2c(self, address: int, length: int, payload: bytearray) -> int | None:
        if self.has_watchdog:
            self._heard()
            if self.tripped:
                return WRITE_TRIPPED
        payload = bytes(payload[:length])
        self.transactions.append((address, payload))
        if address == self.board_address and len(payload) >= 2:
            self._write_registers(payload[0], payload[1:])
        if not self.has_watchdog:
            return None  # Firmware 1 returned nothing.
        return 0 if address == self.board_address else 2

    # Firmware 2.0.

    def firmware_version(self) -> int:
        self._heard()
        return FIRMWARE_VERSION

    def watchdog_arm(self, timeout_ms: int) -> int:
        self._heard()
        if not 100 <= timeout_ms <= 600_000:
            return 1
        if self.tripped:
            return 2
        self.timeout_ms, self.armed = timeout_ms, True
        return 0

    def watchdog_kick(self) -> None:
        self._heard()
        self.kicks += 1

    def watchdog_disarm(self) -> None:
        self._heard()
        self.armed = False

    def watchdog_status(self) -> int:
        self._heard()
        return (STATUS_ARMED if self.armed else 0) | (
            STATUS_TRIPPED if self.tripped else 0
        )

    def watchdog_timeout_ms(self) -> int:
        self._heard()
        return self.timeout_ms

    def watchdog_trip_count(self) -> int:
        self._heard()
        return self.trip_count

    def watchdog_clear(self) -> int:
        self._heard()
        self.tripped = False
        return 0

    def apply_safe_state(self) -> int:
        self._heard()
        return self._write_safe_state()

    def last_safe_state_status(self) -> int:
        self._heard()
        return 0

    def get_safe_state(self) -> int:
        self._heard()
        address, port0, port1 = self.safe
        return address << 16 | port1 << 8 | port0

    def stage_safe_state(self, address: int, port0: int, port1: int) -> int:
        self._heard()
        self._token = 0
        if not 0x20 <= address <= 0x27:
            return 0
        self._staged = (address, port0, port1)
        self._token = random.randint(1, 2**32 - 1)
        return self._token

    def get_staged_safe_state(self) -> int:
        self._heard()
        if not self._token or self._staged is None:
            return 0
        address, port0, port1 = self._staged
        return STAGED_VALID | address << 16 | port1 << 8 | port0

    def commit_safe_state(self, token: int) -> int:
        self._heard()
        if not self._token or token != self._token or self._staged is None:
            self._token = 0
            return 1
        self._token = 0
        self.safe = self._staged
        return 0

    def reboot(self) -> None:
        """What the firmware does at boot: everything resets, then the safe state."""
        self.armed = self.tripped = False
        self.timeout_ms = self.trip_count = 0
        self._token = 0
        self._write_safe_state()

    def set_led(self, value: int) -> None:
        self.led = value

    def digital_read(self, pin: int) -> int:
        return 0

    def digital_write(self, pin: int, value: int) -> None:
        pass

    def pin_mode(self, pin: int, mode: int) -> None:
        pass

    def close(self) -> None:
        pass

    @property
    def closed_channels(self) -> list[int]:
        """Relays energised by output pins that are configured as outputs."""
        closed = []
        for port in (0, 1):
            outputs = self.registers[Register.OUTPUT_PORT_0 + port]
            driven = ~self.registers[Register.CONFIGURATION_PORT_0 + port] & 0xFF
            closed += [
                8 * port + bit for bit in range(8) if outputs & driven & (1 << bit)
            ]
        return closed


_WATCHDOG_CALLS = frozenset(
    name
    for name in vars(SimulatedBridge)
    if name.startswith(
        (
            "watchdog_",
            "firmware_",
            "apply_safe",
            "last_safe",
            "get_s",
            "stage_",
            "commit_",
        )
    )
)
