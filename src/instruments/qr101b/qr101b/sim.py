"""A stand-in for the box's serial port, for tests and dry runs of plans.

Replies follow the examples in the manual's AT command table.
"""

from __future__ import annotations

import re
from collections import deque


class SimulatedQr101b:
    def __init__(
        self, device_type: str = "QR101B-1M-R1", calculated_error_ppm: float = 20.0
    ):
        self.device_type = device_type
        self.calculated_error_ppm = calculated_error_ppm
        self.setpoint = 1000.0
        self.limit = 0.0
        self.user_calibration = False
        self.temperature_c = 27.5
        self.timeout: float | None = 2.0
        self.commands: list[str] = []
        self._replies: deque[bytes] = deque()

    @property
    def calculated(self) -> float:
        return self.setpoint * (1 + self.calculated_error_ppm * 1e-6)

    # Serial-like interface used by the driver.

    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip()
        self.commands.append(command)
        for line in self._handle(command):
            self._replies.append((line + "\r\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        return self._replies.popleft() if self._replies else b""

    def reset_input_buffer(self) -> None:
        self._replies.clear()

    def close(self) -> None:
        pass

    # Box behaviour.

    def _status(self) -> list[str]:
        return [
            "+OK.",
            f"SP(R)={self.setpoint:.3f}",
            f"PV(R)={self.calculated:.3f}",
            "UMax(V)=1.5",
            f"RLimit(R)={self.limit:.3f}",
            f"InnerT(C)={self.temperature_c:.2f}",
        ]

    def _handle(self, command: str) -> list[str]:
        queries = {
            "AT+DEV.TYPE?": f"+DEV.TYPE={self.device_type}",
            "AT+DEV.SN?": "+DEV.SN=00000127",
            "AT+DEV.FW?": "+DEV.FW=5.963KS",
            "AT+DEV.HW?": "+DEV.HW=5.1N",
            "AT+DEV.PROD?": "+DEV.PROD=20230615",
            "AT+DEV.TCR?": "+DEV.TCR=25",
            "AT+USER.SP?": f"+USER.SP={self.setpoint:.4f}",
            "AT+USER.PV?": f"+USER.PV={self.calculated:.3f}",
            "AT+USER.RLIMIT?": f"+USER.RLIMIT={self.limit:.4f}",
            "AT+USER.T_SENSOR?": f"+USER.T_SENSOR={self.temperature_c:.2f}",
            "AT+UCAL.EN?": f"+UCAL.EN={int(self.user_calibration)}",
        }
        if command in queries:
            return [queries[command]]
        if command == "AT+UCAL.INFO?":
            return [
                "+UCAL.INFO:",
                "USEN =0",
                "DATE=20221025",
                "TEMP=27.13",
                "MIN =1.0120",
            ]
        if match := re.fullmatch(r"AT\+USER\.SP([+-]?)=([\d.]+)", command):
            sign, value = match[1], float(match[2])
            target = {
                "": value,
                "+": self.setpoint + value,
                "-": self.setpoint - value,
            }[sign]
            self.setpoint = max(target, self.limit, 1.0)
            return self._status()
        if match := re.fullmatch(r"AT\+USER\.RLIMIT=([\d.]+)", command):
            self.limit = float(match[1])
            self.setpoint = max(self.setpoint, self.limit)
            return self._status()
        if match := re.fullmatch(r"AT\+UCAL\.EN=([01])", command):
            self.user_calibration = match[1] == "1"
            return ["+OK."]
        return ["+ERR."]
