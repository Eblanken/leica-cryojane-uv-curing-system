"""A stand-in for the bridge firmware's RPC interface, for tests and dry runs."""

from __future__ import annotations

from xl9535_relay_mux.driver import POWER_ON_DEFAULTS, Register


class SimulatedBridge:
    """Implements the firmware's RPC calls against one simulated XL9535 relay board."""

    def __init__(self, board_address: int = 0x20):
        self.board_address = board_address
        self.registers = {int(k): v for k, v in POWER_ON_DEFAULTS.items()}
        self.transactions: list[tuple[int, bytes]] = []
        self.led = 0

    def write_i2c(self, address: int, length: int, payload: bytearray) -> None:
        payload = bytes(payload[:length])
        self.transactions.append((address, payload))
        if address == self.board_address and len(payload) >= 2:
            # The register pointer advances within a port pair, as the datasheet describes.
            register = payload[0]
            for value in payload[1:]:
                self.registers[register] = value
                register ^= 1

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
