"""A stand-in for the meter's serial port, for tests and dry runs of plans.

It answers the commands Gdm8341 sends, using the reply formats in the manual.
`source` supplies the value the meter would see for a given function.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable

from gdm8341.driver import Function

_RANGES = {
    Function.RESISTANCE: (5e2, 5e3, 5e4, 5e5, 5e6, 5e7),
    Function.VOLTAGE_DC: (0.5, 5.0, 50.0, 500.0, 1000.0),
}


class SimulatedGdm8341:
    def __init__(self, source: Callable[[Function], float] = lambda function: 0.0):
        self.source = source
        self.function = Function.VOLTAGE_DC
        self.auto_range = True
        self.range = 5.0
        self.rate = "SLOW"
        self.impedance_10g = False
        self.errors: deque[str] = deque()
        self.commands: list[str] = []
        self._replies: deque[bytes] = deque()

    # Serial-like interface used by the driver.

    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip()
        self.commands.append(command)
        reply = self._handle(command)
        if reply is not None:
            self._replies.append((reply + "\r\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        return self._replies.popleft() if self._replies else b""

    def reset_input_buffer(self) -> None:
        self._replies.clear()

    def close(self) -> None:
        pass

    # Meter behaviour.

    def _select(self, function: Function, argument: str) -> None:
        self.function = function
        ranges = _RANGES.get(function)
        if argument and ranges:
            self.auto_range = False
            self.range = min(
                (r for r in ranges if r >= float(argument)), default=ranges[-1]
            )
        else:
            self.auto_range = True

    def _reading(self) -> float:
        value = self.source(self.function)
        ranges = _RANGES.get(self.function)
        if ranges and self.auto_range:
            self.range = min((r for r in ranges if r >= abs(value)), default=ranges[-1])
        if ranges and abs(value) > self.range * 1.1:
            return 9.9e37
        return value

    def _handle(self, command: str) -> str | None:
        upper = command.upper()
        if upper == "*IDN?":
            return "GWInstek,GDM8341,GES000000,1.02"
        if upper in ("*RST", "*CLS"):
            return None
        if upper == "*OPC?":
            return "1"
        if upper == "SYST:ERR?":
            return self.errors.popleft() if self.errors else '+0,"No error"'
        if upper == "CONF:FUNC?":
            return f'"{self.function.reply}"'
        if upper == "CONF:RANG?":
            return f"{self.range:E}"
        if upper == "CONF:AUTO?":
            return "1" if self.auto_range else "0"
        if upper.startswith("CONF:AUTO "):
            self.auto_range = upper.endswith("ON")
            return None
        if upper == "SENS:DET:RATE?":
            return self.rate
        if upper.startswith("SENS:DET:RATE "):
            self.rate = {"S": "SLOW", "M": "MID", "F": "FAST"}[upper[-1]]
            return None
        if upper == "INP:IMP:AUTO?":
            return "1" if self.impedance_10g else "0"
        if upper.startswith("INP:IMP:AUTO "):
            self.impedance_10g = upper.endswith("ON")
            return None
        if match := re.fullmatch(r"(CONF|MEAS):([A-Z:]+?)(\?)?(?: (\S+))?", upper):
            kind, path, _, argument = match.groups()
            function = next((f for f in Function if f.path == path), None)
            if function is not None:
                self._select(function, argument or "")
                return f"{self._reading():+.4E}" if kind == "MEAS" else None
        if upper == "READ?":
            return f"{self._reading():+.4E}"
        self.errors.append('-113,"Undefined header"')
        return None
