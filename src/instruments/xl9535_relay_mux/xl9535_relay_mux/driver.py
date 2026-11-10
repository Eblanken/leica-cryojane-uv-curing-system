"""16-channel relay mux: an XL9535 relay board driven by a USB-to-I2C bridge MCU.

Three layers, each usable on its own:
  - I2cBridge: the ESP32 bridge firmware in firmware/, over simpleRPC.
  - Xl9535: the I/O expander's registers, from the datasheet in docs/.
  - RelayMux: the 16-relay board, which closes at most one relay per group.

The bridge firmware can only write to I2C, so register values are the last ones
written, not read back. Opening RelayMux clears every relay, which disconnects
whatever the mux had selected.

Firmware 2.0 adds a host watchdog. The bridge keeps a safe state for the relays in flash,
and writes it at boot, on request, and when the watchdog is armed and the host goes
quiet for longer than its timeout. After a trip the bridge refuses I2C writes, and
write_i2c raises WatchdogTripped, until the host clears it. RelayMux arms the watchdog
when given `watchdog_s`, and feeds it from a background thread. The safe state changes
only through RelayMux.program_safe_state, which stages it, checks it, and commits it with
the token the bridge returned.
"""

from __future__ import annotations

import threading
import time
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Self

from serial.tools import list_ports

ESPRESSIF_VID = 0x303A  # The bridge MCU's native USB.
BAUD_RATE = 115200  # Matches Serial.begin() in the firmware.
CHANNEL_COUNT = 16
BASE_ADDRESS = 0x20  # A0-A2 set the lower 3 bits.
DEFAULT_GROUP = "default"

WATCHDOG_FIRMWARE = (2, 0, 0)  # The first firmware with the watchdog and safe state.
WRITE_TRIPPED = 0xFE  # write_i2c's status after a watchdog trip.
STATUS_ARMED, STATUS_TRIPPED = 0x01, 0x02
STAGED_VALID = 1 << 24
KICKS_PER_TIMEOUT = 4
# Wire.endTransmission status codes, from the Arduino ESP32 core.
I2C_STATUS = {
    1: "data too long for the buffer",
    2: "address not acknowledged",
    3: "data not acknowledged",
    4: "other error",
    5: "timeout",
}


class RelayMuxError(RuntimeError):
    pass


class WatchdogTripped(RelayMuxError):
    """The bridge applied its safe state and refuses writes until the trip is cleared."""


class SafeStateError(RelayMuxError):
    pass


class Register(IntEnum):
    """XL9535 command bytes. Ports 0 and 1 each hold 8 I/O pins."""

    INPUT_PORT_0 = 0x00
    INPUT_PORT_1 = 0x01
    OUTPUT_PORT_0 = 0x02
    OUTPUT_PORT_1 = 0x03
    POLARITY_INVERSION_PORT_0 = 0x04
    POLARITY_INVERSION_PORT_1 = 0x05
    CONFIGURATION_PORT_0 = 0x06  # 1 is an input, the power-on default. 0 is an output.
    CONFIGURATION_PORT_1 = 0x07


POWER_ON_DEFAULTS = {
    Register.OUTPUT_PORT_0: 0xFF,
    Register.OUTPUT_PORT_1: 0xFF,
    Register.POLARITY_INVERSION_PORT_0: 0x00,
    Register.POLARITY_INVERSION_PORT_1: 0x00,
    Register.CONFIGURATION_PORT_0: 0xFF,
    Register.CONFIGURATION_PORT_1: 0xFF,
}


@dataclass(frozen=True)
class SafeState:
    """What the bridge writes on a trip: the board's address and its two output ports."""

    address: int
    port0: int
    port1: int

    @classmethod
    def unpack(cls, packed: int) -> SafeState:
        return cls((packed >> 16) & 0xFF, packed & 0xFF, (packed >> 8) & 0xFF)

    @classmethod
    def closing(cls, relays: Iterable[int], address: int = BASE_ADDRESS) -> SafeState:
        port0 = port1 = 0
        for relay in relays:
            relay_0, relay_1 = channel_to_output_bytes(relay)
            port0, port1 = port0 | relay_0, port1 | relay_1
        return cls(address, port0, port1)

    @property
    def closed_relays(self) -> list[int]:
        bits = self.port0 | self.port1 << 8
        return [relay for relay in range(CHANNEL_COUNT) if bits & 1 << relay]


@dataclass(frozen=True)
class WatchdogStatus:
    armed: bool
    tripped: bool
    timeout_s: float
    trip_count: int


def find_port() -> str | None:
    """COM port of the first attached Espressif MCU."""
    return next(
        (p.device for p in list_ports.comports() if p.vid == ESPRESSIF_VID), None
    )


def channel_to_output_bytes(channel: int | None) -> tuple[int, int]:
    """Port 0 and port 1 output values that close only `channel`. None opens every relay."""
    if channel is None:
        return 0x00, 0x00
    if channel in range(8):
        return 1 << channel, 0x00
    if channel in range(8, CHANNEL_COUNT):
        return 0x00, 1 << (channel - 8)
    raise ValueError(f"Channel {channel} is invalid for this board.")


OPEN_ATTEMPTS = 3


def _open_interface(com_port: str) -> Any:
    """Opens simpleRPC, retrying when the bridge is still booting and sends noise."""
    from simple_rpc import Interface

    for attempt in range(1, OPEN_ATTEMPTS + 1):
        try:
            return Interface(device=com_port, baudrate=BAUD_RATE)
        except ValueError:  # "invalid protocol header"
            if attempt == OPEN_ATTEMPTS:
                raise
            time.sleep(1.0)
    raise AssertionError("unreachable")


class I2cBridge:
    """The bridge MCU's RPC calls.

    Args:
        com_port: Serial port of the bridge. Ignored when `interface` is given.
        interface: An object with the firmware's RPC methods, such as a simulator.
    """

    def __init__(self, com_port: str | None = None, *, interface: Any = None):
        if interface is None:
            if com_port is None:
                raise ValueError("Give a COM port or an interface.")
            interface = _open_interface(com_port)
        self.port = com_port or "simulated"
        self._interface = interface
        # The watchdog's feeding thread shares the serial port with the caller.
        self._lock = threading.RLock()

    def _call(self, name: str, *args) -> Any:
        with self._lock:
            return getattr(self._interface, name)(*args)

    def write_i2c(self, address: int, payload: bytes) -> None:
        """Raises WatchdogTripped after a trip, and RelayMuxError if not acknowledged.

        Firmware before 2.0 returns nothing, and its failures go unnoticed.
        """
        if len(payload) > 255:
            raise ValueError("The firmware sends at most 255 bytes per transaction.")
        status = self._call("write_i2c", address, len(payload), bytearray(payload))
        if status == WRITE_TRIPPED:
            raise WatchdogTripped(
                "The bridge's watchdog tripped and applied its safe state. Clear it "
                "with RelayMux.recover_from_trip() before switching again."
            )
        if status:
            raise RelayMuxError(
                f"I2C write to {address:#x} failed: "
                f"{I2C_STATUS.get(status, f'status {status}')}."
            )

    def set_led(self, on: bool) -> None:
        self._call("set_led", int(on))

    def digital_read(self, pin: int) -> int:
        return self._call("digital_read", pin)

    def digital_write(self, pin: int, value: int) -> None:
        self._call("digital_write", pin, value)

    def pin_mode(self, pin: int, mode: int) -> None:
        """Arduino pin mode constant for the ESP32 core, such as OUTPUT or INPUT_PULLUP."""
        self._call("pin_mode", pin, mode)

    # Firmware 2.0 and later.

    def firmware_version(self) -> tuple[int, int, int] | None:
        """(major, minor, patch), or None for firmware too old to say."""
        try:
            packed = self._call("firmware_version")
        except AttributeError:
            return None
        return (packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF

    @property
    def supports_watchdog(self) -> bool:
        version = self.firmware_version()
        return version is not None and version >= WATCHDOG_FIRMWARE

    def watchdog_arm(self, timeout_s: float) -> None:
        result = self._call("watchdog_arm", round(timeout_s * 1000))
        if result == 1:
            raise ValueError("The watchdog timeout must be 0.1 to 600 s.")
        if result == 2:
            raise WatchdogTripped("Clear the watchdog's trip before arming it again.")

    def watchdog_kick(self) -> None:
        self._call("watchdog_kick")

    def watchdog_disarm(self) -> None:
        self._call("watchdog_disarm")

    def watchdog_status(self) -> WatchdogStatus:
        with self._lock:
            flags = self._call("watchdog_status")
            return WatchdogStatus(
                bool(flags & STATUS_ARMED),
                bool(flags & STATUS_TRIPPED),
                self._call("watchdog_timeout_ms") / 1000,
                self._call("watchdog_trip_count"),
            )

    def watchdog_clear(self) -> None:
        """Clears a trip. Raises if the safe state write that trip made failed."""
        status = self._call("watchdog_clear")
        if status:
            raise RelayMuxError(
                "The bridge's last safe state write failed: "
                f"{I2C_STATUS.get(status, f'status {status}')}."
            )

    def apply_safe_state(self) -> None:
        status = self._call("apply_safe_state")
        if status:
            raise RelayMuxError(
                "Safe state write failed: "
                f"{I2C_STATUS.get(status, f'status {status}')}."
            )

    def safe_state(self) -> SafeState:
        return SafeState.unpack(self._call("get_safe_state"))

    def stage_safe_state(self, state: SafeState) -> int:
        token = self._call("stage_safe_state", state.address, state.port0, state.port1)
        if not token:
            raise SafeStateError(f"The bridge refused to stage {state}.")
        return token

    def staged_safe_state(self) -> SafeState | None:
        packed = self._call("get_staged_safe_state")
        return SafeState.unpack(packed) if packed & STAGED_VALID else None

    def commit_safe_state(self, token: int) -> None:
        result = self._call("commit_safe_state", token)
        if result == 1:
            raise SafeStateError(
                "The bridge rejected the token, so nothing was stored."
            )
        if result:
            raise SafeStateError("The bridge could not store the safe state in flash.")

    def close(self) -> None:
        with self._lock:
            self._interface.close()


class WatchdogFeeder:
    """Feeds the bridge's watchdog from a thread, KICKS_PER_TIMEOUT times per timeout.

    It feeds while this process runs, so it covers the host dying, losing USB or being
    killed, but not a plan that hangs while Python keeps running.
    """

    def __init__(self, bridge: I2cBridge, timeout_s: float):
        self.bridge = bridge
        self.interval_s = timeout_s / KICKS_PER_TIMEOUT
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="relay-mux-watchdog", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.bridge.watchdog_kick()
            except Exception as error:  # noqa: BLE001
                # Stopping lets the bridge trip, which is the point if the port is gone.
                self.error = error
                return

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


class Xl9535:
    """Register access to one XL9535 on the bridge's I2C bus."""

    def __init__(self, bridge: I2cBridge, address: int = BASE_ADDRESS):
        if address not in range(BASE_ADDRESS, BASE_ADDRESS + 8):
            raise ValueError(f"XL9535 addresses are 0x20 to 0x27, not {address:#x}.")
        self.bridge = bridge
        self.address = address
        self.written: dict[Register, int] = dict(POWER_ON_DEFAULTS)

    def write_register(self, register: Register, value: int) -> None:
        if register in (Register.INPUT_PORT_0, Register.INPUT_PORT_1):
            raise ValueError("Input port registers are read-only.")
        self.bridge.write_i2c(self.address, bytes([register, value & 0xFF]))
        self.written[Register(register)] = value & 0xFF

    def set_outputs(self, port: int, value: int) -> None:
        self.write_register(Register.OUTPUT_PORT_0 + port, value)

    def set_directions(self, port: int, inputs_mask: int) -> None:
        """Bits set to 1 make pins inputs, and 0 makes them outputs."""
        self.write_register(Register.CONFIGURATION_PORT_0 + port, inputs_mask)

    def set_polarity_inversion(self, port: int, inverted_mask: int) -> None:
        self.write_register(Register.POLARITY_INVERSION_PORT_0 + port, inverted_mask)


class RelayMux:
    """The 16-relay board. At most one relay per group is closed at a time.

    By default every relay is in one group. Pass `groups` for wiring where relays do
    different jobs, such as one set routing a signal and another selecting what a meter
    reads. Each switch opens the group's old relay before closing the new one.

    Args:
        com_port: Serial port of the bridge MCU. Ignored when `bridge` is given.
        target_address: I2C address of the relay board.
        bridge: An I2cBridge to use instead of opening `com_port`.
        groups: Relay indices of each group, by name. DEFAULT_GROUP is what select()
            and `selected` use when no group is named, so it must be one of them.
        watchdog_s: Arms the bridge's watchdog with this timeout, and feeds it until
            close(). Needs firmware 2.0. A trip left over from an earlier host is cleared
            with a warning, since opening sets every relay anyway.
    """

    def __init__(
        self,
        com_port: str | None = None,
        target_address: int = BASE_ADDRESS,
        *,
        bridge: I2cBridge | None = None,
        groups: dict[str, Iterable[int]] | None = None,
        watchdog_s: float | None = None,
    ):
        self.groups = {
            name: frozenset(relays)
            for name, relays in (
                groups or {DEFAULT_GROUP: range(CHANNEL_COUNT)}
            ).items()
        }
        if DEFAULT_GROUP not in self.groups:
            raise ValueError(f"Relay groups must include '{DEFAULT_GROUP}'.")
        assigned = [relay for relays in self.groups.values() for relay in relays]
        if len(assigned) != len(set(assigned)):
            raise ValueError("A relay can be in only one group.")
        for relay in assigned:
            channel_to_output_bytes(relay)  # Rejects relays off the board.

        self.bridge = bridge or I2cBridge(com_port)
        self.expander = Xl9535(self.bridge, target_address)
        self._selected: dict[str, int | None] = dict.fromkeys(self.groups)
        self.watchdog_s = watchdog_s
        self._feeder: WatchdogFeeder | None = None

        if watchdog_s is not None:
            if not self.bridge.supports_watchdog:
                raise RelayMuxError(
                    f"The bridge runs firmware {self.bridge.firmware_version()}, which "
                    f"has no watchdog. Flash {'.'.join(map(str, WATCHDOG_FIRMWARE))} "
                    "or later."
                )
            status = self.bridge.watchdog_status()
            if status.tripped:
                warnings.warn(
                    f"The bridge's watchdog had tripped ({status.trip_count} trips "
                    "since it booted), so an earlier host stopped feeding it. Clearing "
                    "it and opening every relay.",
                    stacklevel=2,
                )
                self.bridge.watchdog_clear()

        # Every expander pin is an input after power-on reset. Clear the outputs
        # before enabling them so that no relay closes during configuration.
        self.expander.set_outputs(0, 0x00)
        self.expander.set_outputs(1, 0x00)
        self.expander.set_directions(0, 0x00)
        self.expander.set_directions(1, 0x00)

        if watchdog_s is not None:
            self.bridge.watchdog_arm(watchdog_s)
            self._feeder = WatchdogFeeder(self.bridge, watchdog_s)

    @property
    def port(self) -> str:
        return self.bridge.port

    @property
    def target_address(self) -> int:
        return self.expander.address

    @property
    def selected(self) -> int | None:
        return self._selected[DEFAULT_GROUP]

    def selected_in(self, group: str) -> int | None:
        return self._selected[group]

    def _write_outputs(self, selected: dict[str, int | None]) -> None:
        port_0, port_1 = 0x00, 0x00
        for relay in selected.values():
            relay_0, relay_1 = channel_to_output_bytes(relay)
            port_0, port_1 = port_0 | relay_0, port_1 | relay_1
        self.expander.set_outputs(0, port_0)
        self.expander.set_outputs(1, port_1)

    def select(self, channel: int | None, group: str = DEFAULT_GROUP) -> None:
        """Closes exactly one relay of `group`, or opens all of its relays for None."""
        if channel is not None and channel not in self.groups[group]:
            raise ValueError(f"Relay {channel} is not in the '{group}' group.")
        # Open the group's old relay in a separate write, so that two relays of a
        # group are never closed together, even across the two expander ports.
        self._write_outputs({**self._selected, group: None})
        self._selected[group] = channel
        self._write_outputs(self._selected)

    def open_all(self) -> None:
        self._selected = dict.fromkeys(self.groups)
        self._write_outputs(self._selected)

    # Watchdog and safe state, with firmware 2.0 and later.

    @property
    def watchdog_feeder_error(self) -> Exception | None:
        """Why the feeding thread stopped, if it did."""
        return None if self._feeder is None else self._feeder.error

    def safe_state(self) -> SafeState:
        return self.bridge.safe_state()

    def recover_from_trip(self) -> None:
        """Clears a trip, opens every relay, and arms the watchdog again if it was."""
        self.bridge.watchdog_clear()
        self.open_all()
        if self.watchdog_s is not None:
            if self._feeder is not None:
                self._feeder.stop()
            self.bridge.watchdog_arm(self.watchdog_s)
            self._feeder = WatchdogFeeder(self.bridge, self.watchdog_s)

    def program_safe_state(
        self, relays: Iterable[int] = (), *, confirm: bool = False
    ) -> SafeState:
        """Stores which relays the bridge closes on a trip or at boot. None by default.

        Takes effect on the next trip or boot, not now. It allows at most one relay per
        group. `confirm=True` is required, since this persists across power cycles. The
        bridge stages it and returns a token, the staged copy is read back and checked,
        then the token commits it to flash, and the stored copy is checked too.
        """
        relays = sorted(set(relays))
        for name, members in self.groups.items():
            in_group = [relay for relay in relays if relay in members]
            if len(in_group) > 1:
                raise ValueError(f"Relays {in_group} are all in the '{name}' group.")
        unknown = set(relays) - {r for members in self.groups.values() for r in members}
        if unknown:
            raise ValueError(f"Relays {sorted(unknown)} are in no group.")
        state = SafeState.closing(relays, self.target_address)
        if not confirm:
            raise SafeStateError(
                f"This stores {state} (relays {relays} closed) in the bridge's flash, "
                "where it lasts across power cycles. Pass confirm=True to store it."
            )
        token = self.bridge.stage_safe_state(state)
        staged = self.bridge.staged_safe_state()
        if staged != state:
            raise SafeStateError(
                f"Staged {staged}, not {state}, so nothing was stored."
            )
        self.bridge.commit_safe_state(token)
        stored = self.bridge.safe_state()
        if stored != state:
            raise SafeStateError(f"The bridge stored {stored}, not {state}.")
        return stored

    def close(self) -> None:
        """Opens every relay, disarms the watchdog and closes the port."""
        try:
            if self._feeder is not None:
                self._feeder.stop()
                self._feeder = None
            try:
                self.open_all()
            except WatchdogTripped:
                pass  # The bridge already applied its safe state.
            if self.watchdog_s is not None:
                self.bridge.watchdog_disarm()
        finally:
            self.bridge.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
