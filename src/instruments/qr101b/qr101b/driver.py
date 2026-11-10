"""Eastwood QR101B programmable resistance box, over its CH340 USB serial port.

Commands come from the "AT command set" section of the QR10x Spec & User's Manual
v3.0, in docs/.

Things that matter when using this on a live circuit:
  - PV is the box's calculated value from its calibration table. It is not a
    measurement, so check it against a meter when the absolute value matters.
  - The output can briefly open or short while relays switch (under 95 ms on a
    QR101), so changing the setpoint disturbs the circuit it is attached to.
  - Relay life is 1e5 cycles under load. The recommended rate is 1 Hz, which the
    driver enforces by default.
  - The box does not support short or open outputs. The minimum is about 1 ohm.
  - The manual asks for a USB isolator when the output sees 36 V or more.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol, Self

import serial
from serial.tools import list_ports

USB_VID_PID = (0x1A86, 0x7523)  # Generic CH340, so confirm with AT+DEV.TYPE?.
BAUD_RATE = 115200
RELAY_SETTLE_S = 0.095
RECOMMENDED_CHANGE_INTERVAL_S = 1.0
MIN_RESISTANCE_OHM = 1.0
_LAST_STATUS_FIELD = "InnerT(C)"


class Transport(Protocol):
    """The part of serial.Serial the driver uses, so tests can substitute a simulator."""

    timeout: float | None

    def write(self, data: bytes) -> int | None: ...
    def readline(self) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class OutputState:
    """The status block the box returns after any setpoint or limit change."""

    setpoint_ohm: float
    calculated_ohm: float
    max_voltage_v: float
    limit_ohm: float
    internal_temperature_c: float


def parse_output_state(lines: list[str]) -> OutputState:
    fields = {}
    for line in lines:
        match = re.fullmatch(r"\s*([\w()]+)=\s*([-+.\d]+)\s*", line)
        if match:
            fields[match[1]] = float(match[2])
    try:
        return OutputState(
            setpoint_ohm=fields["SP(R)"],
            calculated_ohm=fields["PV(R)"],
            max_voltage_v=fields["UMax(V)"],
            limit_ohm=fields["RLimit(R)"],
            internal_temperature_c=fields[_LAST_STATUS_FIELD],
        )
    except KeyError as missing:
        raise ValueError(
            f"QR101B status block is missing {missing}: {lines!r}"
        ) from None


def parse_calibration_info(lines: list[str]) -> dict[str, str]:
    """Key-value lines of the AT+UCAL.INFO? reply, such as 'DATE=20221025'."""
    info = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            info[key.strip()] = value.strip()
    return info


def find_ports() -> list[str]:
    """Every attached CH340 port. Other CH340 adapters match too."""
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == USB_VID_PID]


class Qr101b:
    """Driver for the QR10x family over USB serial.

    Args:
        com_port: Serial port, for example "COM6". Ignored when `transport` is given.
        timeout_s: Read timeout for each reply line.
        min_change_interval_s: Shortest time between output changes. Raise it for
            loaded outputs, or pass 0 for an unloaded box.
        transport: An open serial-like object to use instead of opening `com_port`.
    """

    def __init__(
        self,
        com_port: str | None = None,
        *,
        timeout_s: float = 2.0,
        min_change_interval_s: float = RECOMMENDED_CHANGE_INTERVAL_S,
        transport: Transport | None = None,
    ):
        if transport is None:
            if com_port is None:
                raise ValueError("Give a COM port or a transport.")
            transport = serial.Serial(
                port=com_port, baudrate=BAUD_RATE, timeout=timeout_s
            )
            time.sleep(0.2)  # The CH340 can emit a stray byte when the port opens.
        self.port = com_port or "simulated"
        self.min_change_interval_s = min_change_interval_s
        self._last_change = float("-inf")
        self._interface = transport
        self._interface.reset_input_buffer()
        self.device_type = self._get(
            "AT+DEV.TYPE?", "+DEV.TYPE"
        )  # Fails fast on the wrong port.
        if not self.device_type.startswith("QR10"):
            raise ConnectionError(
                f"{self.port} reports '{self.device_type}', not a QR10x box."
            )

    # Protocol.

    def _send(self, command: str) -> None:
        self._interface.reset_input_buffer()
        self._interface.write((command + "\r\n").encode("ascii"))

    def _read_line(self, command: str) -> str:
        line = self._interface.readline().decode("ascii", errors="replace").strip()
        if not line:
            raise TimeoutError(
                f"No response from QR101B on {self.port} to '{command}'."
            )
        return line

    def _read_until_quiet(self, quiet_s: float = 0.3) -> list[str]:
        """Lines of a reply whose length the manual does not give."""
        previous, self._interface.timeout = self._interface.timeout, quiet_s
        try:
            lines = []
            while (
                line := self._interface.readline()
                .decode("ascii", errors="replace")
                .strip()
            ):
                lines.append(line)
            return lines
        finally:
            self._interface.timeout = previous

    def _get(self, command: str, prefix: str) -> str:
        self._send(command)
        line = self._read_line(command)
        if not line.startswith(prefix + "="):
            raise ValueError(f"Unexpected reply to '{command}': {line!r}")
        return line.split("=", 1)[1].strip()

    def _expect_ok(self, command: str) -> None:
        self._send(command)
        first = self._read_line(command)
        if first != "+OK.":
            raise ValueError(f"QR101B rejected '{command}': {first!r}")

    def _change_output(self, command: str) -> OutputState:
        wait = self._last_change + self.min_change_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._expect_ok(command)
        self._last_change = time.monotonic()
        lines: list[str] = []
        while not (lines and lines[-1].startswith(_LAST_STATUS_FIELD)):
            lines.append(self._read_line(command))
        return parse_output_state(lines)

    def query(self, command: str) -> list[str]:
        """Sends any AT command and returns every reply line."""
        self._send(command)
        return self._read_until_quiet(quiet_s=self._interface.timeout or 1.0)

    # Device information.

    @property
    def serial_number(self) -> str:
        return self._get("AT+DEV.SN?", "+DEV.SN")

    @property
    def firmware_version(self) -> str:
        return self._get("AT+DEV.FW?", "+DEV.FW")

    @property
    def hardware_version(self) -> str:
        return self._get("AT+DEV.HW?", "+DEV.HW")

    @property
    def production_date(self) -> str:
        return self._get("AT+DEV.PROD?", "+DEV.PROD")

    @property
    def tcr_ppm(self) -> float:
        return float(self._get("AT+DEV.TCR?", "+DEV.TCR"))

    @property
    def internal_temperature_c(self) -> float:
        return float(self._get("AT+USER.T_SENSOR?", "+USER.T_SENSOR"))

    # User calibration.

    @property
    def user_calibration_enabled(self) -> bool:
        """True when the output follows the user calibration table, False for factory."""
        return self._get("AT+UCAL.EN?", "+UCAL.EN") == "1"

    def set_user_calibration_enabled(self, enabled: bool) -> None:
        self._expect_ok(f"AT+UCAL.EN={int(enabled)}")
        self._read_until_quiet()

    def user_calibration_info(self) -> dict[str, str]:
        """Date, temperature, limits and per-relay values of the user calibration."""
        self._send("AT+UCAL.INFO?")
        return parse_calibration_info(self._read_until_quiet())

    # Output.

    @property
    def setpoint_ohm(self) -> float:
        return float(self._get("AT+USER.SP?", "+USER.SP"))

    @property
    def calculated_ohm(self) -> float:
        """Value calculated from the calibration table, not a measurement."""
        return float(self._get("AT+USER.PV?", "+USER.PV"))

    @property
    def limit_ohm(self) -> float:
        """Safety output limit. The box will not go below it."""
        return float(self._get("AT+USER.RLIMIT?", "+USER.RLIMIT"))

    def set_limit(self, ohms: float) -> OutputState:
        return self._change_output(f"AT+USER.RLIMIT={ohms:.4f}")

    def set_resistance(self, ohms: float, settle: bool = True) -> OutputState:
        """Sets the output and returns the box's status block.

        Raises ValueError below the box's safety limit, where the box would
        otherwise hold a different value from the one requested.
        """
        floor = max(MIN_RESISTANCE_OHM, self.limit_ohm)
        if ohms < floor:
            raise ValueError(f"{ohms} ohm is below the {floor} ohm limit of this box.")
        state = self._change_output(f"AT+USER.SP={ohms:.4f}")
        if settle:
            time.sleep(RELAY_SETTLE_S)
        return state

    def step_resistance(self, delta_ohm: float, settle: bool = True) -> OutputState:
        """Moves the output by `delta_ohm`, up or down."""
        sign = "+" if delta_ohm >= 0 else "-"
        state = self._change_output(f"AT+USER.SP{sign}={abs(delta_ohm):.4f}")
        if settle:
            time.sleep(RELAY_SETTLE_S)
        return state

    def close(self) -> None:
        self._interface.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
