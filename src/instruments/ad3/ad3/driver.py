"""Digilent Analog Discovery 3, through the WaveForms SDK. No Bluesky imports here.

This driver only listens: scope inputs, the logic analyzer and the digital I/O inputs. It
has no methods for the waveform generators, power supplies or digital outputs, because the
AD3 is clipped onto live boards. Anything else in the SDK is reachable through `call`.
"""

from __future__ import annotations

import ctypes as c
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self

import numpy as np

STATE_DONE = 2  # DwfStateDone in dwf.h.
ENUM_ALL = 0  # enumfilterAll in dwf.h.
ACQUISITION_SINGLE = 0  # acqmodeSingle in dwf.h.
SAMPLE_BITS = 16
DEFAULT_WINDOW_S = 4.0
DEFAULT_RANGE_V = 50.0  # The widest scope range, so nothing on the bench can clip it.
MARGIN_S = 5.0  # Extra time allowed for a capture to finish.


class Ad3Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    model: str
    serial_number: str
    sdk_version: str


@dataclass(frozen=True)
class EdgeSummary:
    """One logic line. `high_fraction` covers whole periods when there are at least two."""

    high_fraction: float
    frequency_hz: float
    rising_edges: int


@dataclass(frozen=True)
class AnalogSummary:
    mean_v: float
    min_v: float
    max_v: float


@dataclass(frozen=True)
class Capture:
    """A scope and logic capture started together."""

    analog_v: np.ndarray  # One row per channel.
    analog_hz: float
    digital: np.ndarray
    digital_hz: float

    def line(self, pin: int) -> np.ndarray:
        return (self.digital >> pin) & 1


@dataclass(frozen=True)
class Measurement:
    """What one `measure` call reduces its capture to."""

    pin_high_fraction: float = float("nan")
    pin_frequency_hz: float = float("nan")
    pin_rising_edges: int = -1
    ch1_mean_v: float = float("nan")
    ch1_min_v: float = float("nan")
    ch1_max_v: float = float("nan")
    ch2_mean_v: float = float("nan")
    ch2_min_v: float = float("nan")
    ch2_max_v: float = float("nan")


def summarize_edges(bits: np.ndarray, sample_hz: float) -> EdgeSummary:
    """Duty and frequency of a logic line. Frequency is 0 without two rising edges."""
    bits = np.asarray(bits).astype(bool)
    rising = np.flatnonzero(bits[1:] & ~bits[:-1]) + 1
    if len(rising) < 2:
        return EdgeSummary(float(bits.mean()), 0.0, len(rising))
    whole_periods = bits[rising[0] : rising[-1]]
    frequency = (len(rising) - 1) * sample_hz / (rising[-1] - rising[0])
    return EdgeSummary(float(whole_periods.mean()), float(frequency), len(rising))


def summarize_analog(volts: np.ndarray) -> AnalogSummary:
    return AnalogSummary(float(volts.mean()), float(volts.min()), float(volts.max()))


def load_library() -> Any:
    """The SDK's library, which comes with WaveForms from Digilent."""
    return c.cdll.LoadLibrary("dwf.dll" if sys.platform == "win32" else "libdwf.so")


class Ad3:
    def __init__(
        self,
        index: int = -1,
        *,
        dwf: Any = None,
        window_s: float = DEFAULT_WINDOW_S,
        digital_pin: int = 0,
        range_v: float = DEFAULT_RANGE_V,
    ):
        self.dwf = load_library() if dwf is None else dwf
        self.window_s = window_s
        self.digital_pin = digital_pin
        self.range_v = range_v
        self.last = Measurement()
        self._handle = c.c_int()
        self.call("FDwfDeviceOpen", c.c_int(index), c.byref(self._handle))
        if self._handle.value == 0:
            raise Ad3Error(
                "No Analog Discovery available. Close WaveForms if it's open."
            )
        self.identity = self._read_identity(max(index, 0))
        if self.output_enable_mask:
            self.close()
            raise Ad3Error("The AD3 opened with digital outputs enabled.")

    # Plumbing.

    def call(self, function: str, *args) -> None:
        """Calls any SDK function, raising the SDK's own message if it fails."""
        if not getattr(self.dwf, function)(*args):
            message = c.create_string_buffer(512)
            self.dwf.FDwfGetLastErrorMsg(message)
            raise Ad3Error(f"{function}: {message.value.decode(errors='replace')}")

    def _read_identity(self, index: int) -> Identity:
        count = c.c_int()
        self.call("FDwfEnum", c.c_int(ENUM_ALL), c.byref(count))
        version = c.create_string_buffer(32)
        self.call("FDwfGetVersion", version)
        model, serial = c.create_string_buffer(32), c.create_string_buffer(32)
        self.call("FDwfEnumDeviceName", c.c_int(index), model)
        self.call("FDwfEnumSN", c.c_int(index), serial)
        return Identity(
            model.value.decode(), serial.value.decode(), version.value.decode()
        )

    @property
    def handle(self) -> c.c_int:
        return self._handle

    # Digital I/O inputs.

    @property
    def output_enable_mask(self) -> int:
        mask = c.c_uint()
        self.call("FDwfDigitalIOOutputEnableGet", self._handle, c.byref(mask))
        return mask.value

    def read_pins(self) -> int:
        """Every digital pin right now, as one integer."""
        bits = c.c_uint()
        self.call("FDwfDigitalIOStatus", self._handle)
        self.call("FDwfDigitalIOInputStatus", self._handle, c.byref(bits))
        return bits.value

    # Scope.

    def _analog_buffer_max(self) -> int:
        low, high = c.c_int(), c.c_int()
        self.call(
            "FDwfAnalogInBufferSizeInfo", self._handle, c.byref(low), c.byref(high)
        )
        return high.value

    def _channel_count(self) -> int:
        count = c.c_int()
        self.call("FDwfAnalogInChannelCount", self._handle, c.byref(count))
        return count.value

    def _arm_analog(
        self, sample_hz: float, samples: int, range_v: float, offset_v: float = 0.0
    ) -> float:
        handle = self._handle
        self.call("FDwfAnalogInReset", handle)
        self.call("FDwfAnalogInFrequencySet", handle, c.c_double(sample_hz))
        self.call("FDwfAnalogInBufferSizeSet", handle, c.c_int(samples))
        for channel in range(self._channel_count()):
            index = c.c_int(channel)
            self.call("FDwfAnalogInChannelEnableSet", handle, index, c.c_int(1))
            self.call("FDwfAnalogInChannelRangeSet", handle, index, c.c_double(range_v))
            self.call(
                "FDwfAnalogInChannelOffsetSet", handle, index, c.c_double(offset_v)
            )
        actual = c.c_double()
        self.call("FDwfAnalogInFrequencyGet", handle, c.byref(actual))
        return actual.value

    def _fetch_analog(self, samples: int) -> np.ndarray:
        rows = []
        for channel in range(self._channel_count()):
            data = (c.c_double * samples)()
            self.call(
                "FDwfAnalogInStatusData",
                self._handle,
                c.c_int(channel),
                data,
                c.c_int(samples),
            )
            rows.append(np.frombuffer(data, dtype=np.float64).copy())
        return np.array(rows)

    # Logic analyzer.

    def _digital_buffer_max(self) -> int:
        high = c.c_int()
        self.call("FDwfDigitalInBufferSizeInfo", self._handle, c.byref(high))
        return high.value

    def _arm_digital(self, sample_hz: float, samples: int) -> float:
        handle = self._handle
        clock, divider_max = c.c_double(), c.c_uint()
        self.call("FDwfDigitalInReset", handle)
        self.call("FDwfDigitalInInternalClockInfo", handle, c.byref(clock))
        self.call("FDwfDigitalInDividerInfo", handle, c.byref(divider_max))
        divider = min(max(round(clock.value / sample_hz), 1), divider_max.value)
        self.call("FDwfDigitalInDividerSet", handle, c.c_uint(divider))
        self.call("FDwfDigitalInSampleFormatSet", handle, c.c_int(SAMPLE_BITS))
        self.call("FDwfDigitalInBufferSizeSet", handle, c.c_int(samples))
        self.call(
            "FDwfDigitalInAcquisitionModeSet", handle, c.c_int(ACQUISITION_SINGLE)
        )
        return clock.value / divider

    def _fetch_digital(self, samples: int) -> np.ndarray:
        data = (c.c_uint16 * samples)()
        self.call(
            "FDwfDigitalInStatusData", self._handle, data, c.c_int(c.sizeof(data))
        )
        return np.frombuffer(data, dtype=np.uint16).copy()

    # Captures.

    def _wait(self, status_function: str, timeout_s: float) -> None:
        state = c.c_byte()
        deadline = time.monotonic() + timeout_s
        while True:
            self.call(status_function, self._handle, c.c_int(1), c.byref(state))
            if state.value == STATE_DONE:
                return
            if time.monotonic() > deadline:
                raise Ad3Error(f"{status_function}: capture timed out.")
            time.sleep(0.02)

    def capture(self, window_s: float | None = None) -> Capture:
        """The scope and logic analyzer, started together, for the same length of time."""
        window = self.window_s if window_s is None else window_s
        analog_samples, digital_samples = (
            self._analog_buffer_max(),
            self._digital_buffer_max(),
        )
        analog_hz = self._arm_analog(
            analog_samples / window, analog_samples, self.range_v
        )
        digital_hz = self._arm_digital(digital_samples / window, digital_samples)
        self.call("FDwfAnalogInConfigure", self._handle, c.c_int(1), c.c_int(1))
        self.call("FDwfDigitalInConfigure", self._handle, c.c_int(1), c.c_int(1))
        self._wait("FDwfAnalogInStatus", window + MARGIN_S)
        self._wait("FDwfDigitalInStatus", window + MARGIN_S)
        return Capture(
            self._fetch_analog(analog_samples),
            analog_hz,
            self._fetch_digital(digital_samples),
            digital_hz,
        )

    def measure(self, window_s: float | None = None) -> Measurement:
        """One capture reduced to duty, frequency and voltages. Also kept as `last`."""
        capture = self.capture(window_s)
        edges = summarize_edges(capture.line(self.digital_pin), capture.digital_hz)
        channels: Sequence[AnalogSummary] = [
            summarize_analog(row) for row in capture.analog_v
        ]
        self.last = Measurement(
            edges.high_fraction,
            edges.frequency_hz,
            edges.rising_edges,
            channels[0].mean_v,
            channels[0].min_v,
            channels[0].max_v,
            channels[1].mean_v,
            channels[1].min_v,
            channels[1].max_v,
        )
        return self.last

    def close(self) -> None:
        if self._handle.value:
            self.dwf.FDwfDeviceClose(self._handle)
            self._handle = c.c_int()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
