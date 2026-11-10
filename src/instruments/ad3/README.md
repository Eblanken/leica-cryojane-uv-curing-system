# Digilent Analog Discovery 3

USB oscilloscope and logic analyzer, used here to watch a live board without touching it.

- **Driver:** `ad3.driver.Ad3` starts the scope and the logic analyzer together and reduces each capture to duty, frequency and voltages. `capture` returns the raw samples. `call` reaches any other SDK function.
- **Listen-only:** the driver has no methods for the waveform generators, power supplies or digital outputs, because the AD3 is clipped onto powered boards. It refuses to continue if it opens with digital outputs enabled.
- **Bluesky:** `ad3.ophyd_device.Ad3Device`. Each trigger takes one capture, and every reading in that event comes from it. Each descriptor records the window, pin, range, model, serial number and SDK version.
- **Simulator:** `ad3.sim.SimulatedDwf` stands in for the SDK library.

## Setup

- Install WaveForms from Digilent, which includes the SDK and `dwf.dll` (`libdwf.so` on Linux). On Linux, also install the Adept Runtime, and use the `arm64` packages on a 64-bit Raspberry Pi.
- Close the WaveForms application first, since only one program can hold the device.

## Quirks

- The capture uses each instrument's full buffer, so the sample rate is the buffer size divided by the window. A PWM period longer than the window shows fewer than two rising edges, which reports a frequency of 0 and a high fraction that depends on where the capture landed.
- The scope reads volts at its input. It doesn't know about probe attenuation, so a x10 probe reads a tenth of the real voltage.
- Digital inputs default to LVCMOS 3.3 V and tolerate 5 V.

## Documents

- `docs/WaveForms_SDK_Reference_Manual.pdf`: copied unmodified from the WaveForms SDK 3.22.2 installation, `C:\Program Files (x86)\Digilent\WaveFormsSDK\WaveForms SDK Reference Manual.pdf`. The driver's calls were checked against that installation's `inc/dwf.h`.
