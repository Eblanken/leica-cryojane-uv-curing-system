"""GW Instek GDM-8341 bench multimeter over its USB virtual COM port.

Commands follow the "Command Overview" chapter of the GDM-834X Series User Manual,
in docs/. The USB driver is at
https://www.gwinstek.com/en-US/products/downloadSeriesDownNew/15101/673

Settings commands get no reply from the meter, so errors only show up in the error
queue. Call errors() after a configuration change to check that it was accepted.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Protocol, Self

import serial
from serial.tools import list_ports

USB_VID_PID = (0x2184, 0x0030)
BAUD_RATES = (9600, 19200, 38400, 57600, 115200)
# Reported for open circuits and other overloads.
OVERLOAD_THRESHOLD = 1e30


class Transport(Protocol):
    """The part of serial.Serial the driver uses, so tests can substitute a simulator."""

    def write(self, data: bytes) -> int | None: ...
    def readline(self) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


class Function(Enum):
    """Measurement functions: (SCPI path, CONFigure:FUNCtion? reply, units, display 2 support)."""

    VOLTAGE_DC = ("VOLT:DC", "VOLT", "V", True)
    VOLTAGE_AC = ("VOLT:AC", "VOLT:AC", "V", True)
    VOLTAGE_DCAC = ("VOLT:DCAC", "VOLT:DCAC", "V", False)
    CURRENT_DC = ("CURR:DC", "CURR", "A", True)
    CURRENT_AC = ("CURR:AC", "CURR:AC", "A", True)
    CURRENT_DCAC = ("CURR:DCAC", "CURR:DCAC", "A", False)
    RESISTANCE = ("RES", "RES", "ohm", True)
    FREQUENCY = ("FREQ", "FREQ", "Hz", True)
    PERIOD = ("PER", "PER", "s", True)
    CONTINUITY = ("CONT", "CONT", "ohm", False)
    DIODE = ("DIOD", "DIOD", "V", False)
    TEMPERATURE = ("TEMP:TCO", "TEMP", "degC", False)
    CAPACITANCE = ("CAP", "CAP", "F", False)

    def __init__(self, path: str, reply: str, units: str, on_display_2: bool):
        self.path = path
        self.reply = reply
        self.units = units
        self.on_display_2 = on_display_2

    @classmethod
    def from_reply(cls, reply: str) -> Function:
        reply = reply.strip().strip('"').upper()
        for function in cls:
            if reply in (function.reply, function.path):
                return function
        raise ValueError(f"Unknown GDM-8341 function {reply!r}.")


class Rate(StrEnum):
    SLOW = "S"
    MEDIUM = "M"
    FAST = "F"


class TriggerSource(StrEnum):
    INTERNAL = "INT"
    EXTERNAL = "EXT"


class CalculateFunction(StrEnum):
    OFF = "OFF"
    MINMAX = "MIN"
    MAXIMUM = "MAX"
    HOLD = "HOLD"
    RELATIVE = "REL"
    COMPARE = "COMP"
    DB = "DB"
    DBM = "DBM"
    MX_PLUS_B = "MXB"
    INVERSE = "INV"
    PERCENT = "REF"


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    serial_number: str
    version: str


@dataclass(frozen=True)
class ScpiError:
    code: int
    message: str


def parse_identity(reply: str) -> Identity:
    fields = [field.strip() for field in reply.split(",")]
    if len(fields) != 4:
        raise ValueError(f"Unexpected *IDN? reply: {reply!r}")
    return Identity(*fields)


def parse_error(reply: str) -> ScpiError:
    match = re.fullmatch(r'\s*([-+]?\d+)\s*,?\s*"?(.*?)"?\s*', reply)
    if not match:
        raise ValueError(f"Unexpected SYSTem:ERRor? reply: {reply!r}")
    return ScpiError(int(match[1]), match[2])


def parse_reading(reply: str) -> float:
    """First number of a reply such as '+1.1937E+3' or '+0.333E-4,V DC'."""
    return float(reply.split(",")[0])


def find_port() -> str | None:
    """COM port of the first attached GDM-834x, by its USB identity."""
    ports = list_ports.comports()
    return next((p.device for p in ports if (p.vid, p.pid) == USB_VID_PID), None)


def _display_suffix(display: int) -> str:
    if display not in (1, 2):
        raise ValueError(f"Display must be 1 or 2, not {display}.")
    return "" if display == 1 else "2"


def _on_off(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


class Gdm8341:
    """Driver for the GDM-8341 (and GDM-8342) over USB serial.

    Args:
        com_port: Serial port, for example "COM5". Ignored when `transport` is given.
        baud_rate: Must match the meter's MENU > I/O > USB setting.
        timeout_s: Read timeout. 5 s leaves room for a conversion after a range change.
        transport: An open serial-like object to use instead of opening `com_port`.
        read_retries: How many times measure_active retries a missed reply. From the
            second retry on, it also closes and reopens the port first.
    """

    def __init__(
        self,
        com_port: str | None = None,
        *,
        baud_rate: int = 115200,
        timeout_s: float = 5.0,
        transport: Transport | None = None,
        read_retries: int = 1,
    ):
        self._serial_settings = {
            "port": com_port,
            "baudrate": baud_rate,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "timeout": timeout_s,
            "xonxoff": False,
            "rtscts": False,
            "dsrdtr": False,
        }
        self._owns_port = transport is None
        if transport is None:
            if com_port is None:
                raise ValueError("Give a COM port or a transport.")
            transport = serial.Serial(**self._serial_settings)
        self.read_retries = read_retries
        self.port = com_port or "simulated"
        self._interface = transport
        self._interface.reset_input_buffer()
        self.identity = parse_identity(
            self.query("*IDN?")
        )  # Fails fast on the wrong port.

    # Raw access, for anything the methods below do not cover.

    def write(self, command: str) -> None:
        self._interface.write((command + "\r\n").encode("ascii"))

    def query(self, command: str) -> str:
        # A reply that arrived after an earlier timeout must not answer this command.
        self._interface.reset_input_buffer()
        self.write(command)
        reply = self._interface.readline().decode("ascii", errors="replace").strip()
        if not reply:
            raise TimeoutError(
                f"No response from GDM-8341 on {self.port} to '{command}'. "
                "Check that the meter's interface is set to USB."
            )
        return reply

    def _query_float(self, command: str) -> float:
        return float(self.query(command))

    def _query_bool(self, command: str) -> bool:
        return self.query(command).strip() == "1"

    # IEEE 488.2 common commands and status.

    def reset(self) -> None:
        """Recalls the default panel setup (*RST)."""
        self.write("*RST")

    def clear_status(self) -> None:
        self.write("*CLS")

    def wait_until_complete(self) -> None:
        self.query("*OPC?")

    def errors(self) -> list[ScpiError]:
        """Empties the error queue. An empty list means every command so far was accepted."""
        found = []
        for _ in range(32):
            error = parse_error(self.query("SYST:ERR?"))
            if error.code == 0:
                break
            found.append(error)
        return found

    @property
    def event_status_register(self) -> int:
        return int(self.query("*ESR?"))

    @property
    def status_byte(self) -> int:
        return int(self.query("*STB?"))

    @property
    def questionable_event_register(self) -> int:
        return int(self.query("STAT:QUES:EVEN?"))

    # Configuration of either display.

    def configure(
        self,
        function: Function,
        range_: float | str | None = None,
        display: int = 1,
    ) -> None:
        """Selects a function and range. None gives auto range, or MIN, MAX or DEF."""
        if display == 2 and not function.on_display_2:
            raise ValueError(f"{function.name} is not available on display 2.")
        argument = "" if range_ is None else f" {range_}"
        if function is Function.TEMPERATURE:
            argument = ""  # Its argument is a thermocouple type. See thermocouple_type.
        self.write(f"CONF{_display_suffix(display)}:{function.path}{argument}")

    def turn_off_display_2(self) -> None:
        self.write("CONF2:OFF")

    def function(self, display: int = 1) -> Function | None:
        """The active function, or None when display 2 is off."""
        reply = self.query(f"CONF{_display_suffix(display)}:FUNC?")
        return None if reply.strip('"').upper() == "NON" else Function.from_reply(reply)

    def range(self, display: int = 1) -> float:
        """Full-scale value of the active range, in the function's units."""
        return self._query_float(f"CONF{_display_suffix(display)}:RANG?")

    def auto_range(self, display: int = 1) -> bool:
        return self._query_bool(f"CONF{_display_suffix(display)}:AUTO?")

    def set_auto_range(self, enabled: bool, display: int = 1) -> None:
        self.write(f"CONF{_display_suffix(display)}:AUTO {_on_off(enabled)}")

    # Measurements.

    def measure(
        self,
        function: Function,
        range_: float | str | None = None,
        display: int = 1,
    ) -> float:
        """Switches to `function` if needed and returns one reading.

        Overloads, such as an open circuit on resistance, return a value above
        OVERLOAD_THRESHOLD.
        """
        if function is Function.CAPACITANCE:
            raise ValueError("The GDM-834x has no MEASure command for capacitance.")
        if display == 2 and not function.on_display_2:
            raise ValueError(f"{function.name} is not available on display 2.")
        argument = "" if range_ is None else f" {range_}"
        suffix = _display_suffix(display)
        return parse_reading(self.query(f"MEAS{suffix}:{function.path}?{argument}"))

    def reconnect(self) -> None:
        """Closes and reopens the serial port. Does nothing for a given transport."""
        if not self._owns_port:
            return
        # A port that has already dropped can fail to close. Reopening is what matters.
        with contextlib.suppress(serial.SerialException, OSError):
            self._interface.close()
        self._interface = serial.Serial(**self._serial_settings)
        self._interface.reset_input_buffer()

    def measure_active(
        self, retries: int | None = None, retry_wait_s: float = 2.0
    ) -> float:
        """One reading in the current function, retrying if the meter misses a reply.

        Autoranging can take longer than the read timeout, especially on a PWM signal. A
        retry waits first, so the late reply lands before query() clears it. From the
        second retry on, it also reopens the port. `retries` defaults to `read_retries`.
        """
        retries = self.read_retries if retries is None else retries
        for attempt in range(retries + 1):
            try:
                return self.measure(self.function())
            except (TimeoutError, ValueError, serial.SerialException) as error:
                if attempt == retries:
                    raise
                print(
                    f"GDM-8341 read {attempt + 1} failed, retrying: {error}", flush=True
                )
                time.sleep(retry_wait_s)
                if attempt >= 1:
                    try:
                        self.reconnect()
                    except serial.SerialException as reopen_error:
                        print(f"GDM-8341 reopen failed: {reopen_error}", flush=True)
        raise AssertionError("unreachable")

    def read_voltage(self) -> float:
        """DC voltage in volts."""
        return self.measure(Function.VOLTAGE_DC)

    def read_resistance(self) -> float:
        """Two-wire resistance in ohms. Open circuit returns a very large overload value."""
        return self.measure(Function.RESISTANCE)

    def read(self) -> list[float]:
        """The current reading of each active display, without changing the setup."""
        return [parse_reading(field) for field in self.query("READ?").split(",")[:2]]

    def stored_values(self, count: int, display: int = 1) -> list[float]:
        """Takes `count` samples on one display and returns them."""
        self.sample_count = count
        self.write(f"VAL{display}?")
        lines = [
            self._interface.readline().decode("ascii").strip() for _ in range(count)
        ]
        if not all(lines):
            raise TimeoutError(
                f"GDM-8341 returned {sum(map(bool, lines))} of {count} samples."
            )
        return [parse_reading(line) for line in lines]

    # Sense settings.

    @property
    def rate(self) -> Rate:
        return {"SLOW": Rate.SLOW, "MID": Rate.MEDIUM, "FAST": Rate.FAST}[
            self.query("SENS:DET:RATE?").upper()
        ]

    @rate.setter
    def rate(self, rate: Rate) -> None:
        self.write(f"SENS:DET:RATE {Rate(rate).value}")

    @property
    def input_impedance_10g(self) -> bool:
        """DCV input impedance on the 500 mV and 5 V ranges: True is 10 Gohm, False 10 Mohm."""
        return self._query_bool("INP:IMP:AUTO?")

    @input_impedance_10g.setter
    def input_impedance_10g(self, enabled: bool) -> None:
        self.write(f"INP:IMP:AUTO {_on_off(enabled)}")

    @property
    def thermocouple_type(self) -> str:
        return self.query("SENS:TEMP:TCO:TYPE?")

    @thermocouple_type.setter
    def thermocouple_type(self, kind: str) -> None:
        if kind not in ("J", "K", "T"):
            raise ValueError("Thermocouple type must be J, K or T.")
        self.write(f"SENS:TEMP:TCO:TYPE {kind}")

    @property
    def simulated_junction_c(self) -> float:
        return self._query_float("SENS:TEMP:RJUN:SIM?")

    @simulated_junction_c.setter
    def simulated_junction_c(self, celsius: float) -> None:
        if not 0.0 <= celsius <= 50.0:
            raise ValueError("Simulated junction temperature must be 0 to 50 C.")
        self.write(f"SENS:TEMP:RJUN:SIM {celsius:.2f}")

    @property
    def temperature_unit(self) -> str:
        return self.query("SENS:UNIT?")

    @temperature_unit.setter
    def temperature_unit(self, unit: str) -> None:
        if unit not in ("C", "F"):
            raise ValueError("Temperature unit must be C or F.")
        self.write(f"SENS:UNIT {unit}")

    @property
    def continuity_threshold_ohm(self) -> float:
        return self._query_float("SENS:CONT:THR?")

    @continuity_threshold_ohm.setter
    def continuity_threshold_ohm(self, ohms: float) -> None:
        if not 0.0 <= ohms <= 1000.0:
            raise ValueError("Continuity threshold must be 0 to 1000 ohm.")
        self.write(f"SENS:CONT:THR {ohms:g}")

    def set_frequency_input(self, jack: int) -> None:
        """Input for frequency: 0 is the volt jack, 1 the 500 mA jack, 2 the 10 A jack."""
        self.write(f"SENS:FREQ:INP {jack}")

    def frequency_input(self) -> str:
        return self.query("SENS:FREQ:INP?")

    def set_period_input(self, jack: int) -> None:
        """Input for period: 0 is the volt jack, 1 the 500 mA jack, 2 the 10 A jack."""
        self.write(f"SENS:PER:INP {jack}")

    def period_input(self) -> str:
        return self.query("SENS:PER:INP?")

    # Advanced (CALCulate) functions.

    @property
    def calculate_function(self) -> str:
        return self.query("CALC:FUNC?")

    @calculate_function.setter
    def calculate_function(self, function: CalculateFunction) -> None:
        self.write(f"CALC:FUNC {CalculateFunction(function).value}")

    @property
    def calculate_enabled(self) -> bool:
        return self._query_bool("CALC:STAT?")

    @calculate_enabled.setter
    def calculate_enabled(self, enabled: bool) -> None:
        self.write(f"CALC:STAT {_on_off(enabled)}")

    @property
    def minimum(self) -> float:
        return self._query_float("CALC:MIN?")

    @property
    def maximum(self) -> float:
        return self._query_float("CALC:MAX?")

    def calculate_setting(self, name: str) -> float:
        """Reads a CALCulate reference, such as 'REL:REF', 'LIM:LOW' or 'MATH:MMF'."""
        return self._query_float(f"CALC:{name}?")

    def set_calculate_setting(self, name: str, value: float | str) -> None:
        """Sets a CALCulate reference: HOLD:REF, REL:REF, LIM:LOW, LIM:UPP, DB:REF,
        DBM:REF, MATH:MMF, MATH:MBF, MATH:PERC or NULL:OFFS. MIN and MAX also work."""
        self.write(f"CALC:{name} {value}")

    # Triggering.

    @property
    def trigger_source(self) -> str:
        return self.query("TRIG:SOUR?")

    @trigger_source.setter
    def trigger_source(self, source: TriggerSource) -> None:
        self.write(f"TRIG:SOUR {TriggerSource(source).value}")

    @property
    def trigger_auto(self) -> bool:
        return self._query_bool("TRIG:AUTO?")

    @trigger_auto.setter
    def trigger_auto(self, enabled: bool) -> None:
        self.write(f"TRIG:AUTO {_on_off(enabled)}")

    @property
    def sample_count(self) -> int:
        return int(float(self.query("SAMP:COUN?")))

    @sample_count.setter
    def sample_count(self, count: int) -> None:
        if not 1 <= count <= 9999:
            raise ValueError("Sample count must be 1 to 9999.")
        self.write(f"SAMP:COUN {count}")

    @property
    def trigger_count(self) -> int:
        return int(float(self.query("TRIG:COUN?")))

    @trigger_count.setter
    def trigger_count(self, count: int) -> None:
        if not 1 <= count <= 9999:
            raise ValueError("Trigger count must be 1 to 9999.")
        self.write(f"TRIG:COUN {count}")

    def trigger(self) -> None:
        """Software trigger (*TRG)."""
        self.write("*TRG")

    # System.

    @property
    def version(self) -> str:
        return self.query("SYST:VERS?")

    @property
    def serial_number(self) -> str:
        return self.query("SYST:SER?")

    @property
    def display_enabled(self) -> bool:
        return self._query_bool("SYST:DISP?")

    @display_enabled.setter
    def display_enabled(self, enabled: bool) -> None:
        self.write(f"SYST:DISP {_on_off(enabled)}")

    def set_beeper(self, mode: int) -> None:
        """0 never beeps, 1 beeps on a compare pass, 2 on a compare fail."""
        self.write(f"SYST:BEEP:STAT {mode}")

    def set_beep_on_error(self, enabled: bool) -> None:
        self.write(f"SYST:BEEP:ERR {_on_off(enabled)}")

    @property
    def scpi_mode(self) -> str:
        """NORMAL, or COMPATIBLE with the GDM-8246 command set."""
        return self.query("SYST:SCP:MODE?")

    def local(self) -> None:
        """Hands control back to the front panel."""
        self.write("SYST:LOC")

    def remote(self) -> None:
        self.write("SYST:REM")

    def close(self) -> None:
        self._interface.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
