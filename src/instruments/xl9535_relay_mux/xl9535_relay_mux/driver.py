"""16-channel relay mux: an XL9535 relay board driven by a USB-to-I2C bridge MCU.

Three layers, each usable on its own:
  - I2cBridge: the ESP32 bridge firmware in firmware/, over simpleRPC.
  - Xl9535: the I/O expander's registers, from the datasheet in docs/.
  - RelayMux: the 16-relay board, which closes at most one relay per group.

The bridge firmware can only write to I2C, so register values are the last ones
written, not read back. Opening RelayMux clears every relay, which disconnects
whatever the mux had selected.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum
from typing import Any, Self

from serial.tools import list_ports

ESPRESSIF_VID = 0x303A  # The bridge MCU's native USB.
BAUD_RATE = 115200  # Matches Serial.begin() in the firmware.
CHANNEL_COUNT = 16
BASE_ADDRESS = 0x20  # A0-A2 set the lower 3 bits.
DEFAULT_GROUP = "default"


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
            from simple_rpc import Interface

            interface = Interface(device=com_port, baudrate=BAUD_RATE)
        self.port = com_port or "simulated"
        self._interface = interface

    def write_i2c(self, address: int, payload: bytes) -> None:
        if len(payload) > 255:
            raise ValueError("The firmware sends at most 255 bytes per transaction.")
        self._interface.write_i2c(address, len(payload), bytearray(payload))

    def set_led(self, on: bool) -> None:
        self._interface.set_led(int(on))

    def digital_read(self, pin: int) -> int:
        return self._interface.digital_read(pin)

    def digital_write(self, pin: int, value: int) -> None:
        self._interface.digital_write(pin, value)

    def pin_mode(self, pin: int, mode: int) -> None:
        """Arduino pin mode constant for the ESP32 core, such as OUTPUT or INPUT_PULLUP."""
        self._interface.pin_mode(pin, mode)

    def close(self) -> None:
        self._interface.close()


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
    """

    def __init__(
        self,
        com_port: str | None = None,
        target_address: int = BASE_ADDRESS,
        *,
        bridge: I2cBridge | None = None,
        groups: dict[str, Iterable[int]] | None = None,
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

        # Every expander pin is an input after power-on reset. Clear the outputs
        # before enabling them so that no relay closes during configuration.
        self.expander.set_outputs(0, 0x00)
        self.expander.set_outputs(1, 0x00)
        self.expander.set_directions(0, 0x00)
        self.expander.set_directions(1, 0x00)

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

    def close(self) -> None:
        self.open_all()
        self.bridge.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
