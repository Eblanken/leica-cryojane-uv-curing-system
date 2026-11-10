"""A stand-in for the WaveForms SDK library, for tests and dry runs of plans.

It answers the SDK calls Ad3 makes, with the same ctypes arguments. `source` takes an array
of times in seconds and returns (channel 1 volts, channel 2 volts, digital pin 0).
"""

from __future__ import annotations

import ctypes as c
from collections.abc import Callable

import numpy as np

CLOCK_HZ = 100e6
ANALOG_BUFFER = 8_192
DIGITAL_BUFFER = 16_384


def _default_source(times: np.ndarray):
    zeros = np.zeros_like(times)
    return zeros, zeros, zeros


class SimulatedDwf:
    def __init__(self, source: Callable = _default_source):
        self.source = source
        self.output_mask = 0
        self.open_count = 0
        self.fail_next: str | None = None
        self.calls: list[str] = []
        self._analog_hz = 1_000.0
        self._analog_samples = ANALOG_BUFFER
        self._digital_divider = 1
        self._digital_samples = DIGITAL_BUFFER

    def __getattr__(self, name: str):
        """Records every call, and fails the one named by `fail_next`."""
        method = getattr(type(self), f"_{name}", None)
        if method is None:
            raise AttributeError(name)

        def call(*args) -> int:
            self.calls.append(name)
            if self.fail_next == name:
                self.fail_next = None
                return 0
            method(self, *args)
            return 1

        return call

    # A failing call's message is read without going through the failure check.
    def FDwfGetLastErrorMsg(self, buffer) -> int:
        buffer.value = b"simulated failure"
        return 1

    def _FDwfGetVersion(self, buffer):
        buffer.value = b"3.22.2"

    def _FDwfEnum(self, _filter, count):
        count._obj.value = 1

    def _FDwfEnumDeviceName(self, _index, buffer):
        buffer.value = b"Analog Discovery 3"

    def _FDwfEnumSN(self, _index, buffer):
        buffer.value = b"SN:SIMULATED"

    def _FDwfDeviceOpen(self, _index, handle):
        handle._obj.value = 1
        self.open_count += 1

    def _FDwfDeviceClose(self, _handle):
        self.open_count -= 1

    def _FDwfDigitalIOOutputEnableGet(self, _handle, mask):
        mask._obj.value = self.output_mask

    def _FDwfDigitalIOStatus(self, _handle):
        pass

    def _FDwfDigitalIOInputStatus(self, _handle, bits):
        bits._obj.value = int(self.source(np.zeros(1))[2][0])

    # Scope.

    def _FDwfAnalogInReset(self, _handle):
        pass

    def _FDwfAnalogInBufferSizeInfo(self, _handle, low, high):
        low._obj.value, high._obj.value = 16, ANALOG_BUFFER

    def _FDwfAnalogInChannelCount(self, _handle, count):
        count._obj.value = 2

    def _FDwfAnalogInFrequencySet(self, _handle, hz):
        self._analog_hz = hz.value

    def _FDwfAnalogInFrequencyGet(self, _handle, hz):
        hz._obj.value = self._analog_hz

    def _FDwfAnalogInBufferSizeSet(self, _handle, samples):
        self._analog_samples = samples.value

    def _FDwfAnalogInChannelEnableSet(self, _handle, _channel, _enable):
        pass

    def _FDwfAnalogInChannelRangeSet(self, _handle, _channel, _volts):
        pass

    def _FDwfAnalogInChannelOffsetSet(self, _handle, _channel, _volts):
        pass

    def _FDwfAnalogInConfigure(self, _handle, _reconfigure, _start):
        pass

    def _FDwfAnalogInStatus(self, _handle, _read, state):
        state._obj.value = 2

    def _FDwfAnalogInStatusData(self, _handle, channel, data, samples):
        times = np.arange(samples.value) / self._analog_hz
        values = self.source(times)[channel.value]
        np.frombuffer(data, dtype=np.float64)[:] = values

    # Logic analyzer.

    def _FDwfDigitalInReset(self, _handle):
        pass

    def _FDwfDigitalInBufferSizeInfo(self, _handle, high):
        high._obj.value = DIGITAL_BUFFER

    def _FDwfDigitalInInternalClockInfo(self, _handle, hz):
        hz._obj.value = CLOCK_HZ

    def _FDwfDigitalInDividerInfo(self, _handle, divider):
        divider._obj.value = 2**31

    def _FDwfDigitalInDividerSet(self, _handle, divider):
        self._digital_divider = divider.value

    def _FDwfDigitalInSampleFormatSet(self, _handle, _bits):
        pass

    def _FDwfDigitalInBufferSizeSet(self, _handle, samples):
        self._digital_samples = samples.value

    def _FDwfDigitalInAcquisitionModeSet(self, _handle, _mode):
        pass

    def _FDwfDigitalInConfigure(self, _handle, _reconfigure, _start):
        pass

    def _FDwfDigitalInStatus(self, _handle, _read, state):
        state._obj.value = 2

    def _FDwfDigitalInStatusData(self, _handle, data, _bytes):
        samples = c.sizeof(data) // c.sizeof(c.c_uint16)
        times = np.arange(samples) * self._digital_divider / CLOCK_HZ
        pin = np.asarray(self.source(times)[2], dtype=np.uint16)
        np.frombuffer(data, dtype=np.uint16)[:] = pin
