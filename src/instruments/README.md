# Instruments

One folder per bench instrument. Each is its own installable package, listed in `pixi.toml`.

| Folder | Instrument | USB identity | Manual |
|---|---|---|---|
| `gdm8341/` | GW Instek GDM-8341 multimeter | VID:PID 2184:0030 | `docs/GDM-834X_User_Manual.pdf` |
| `qr101b/` | Eastwood QR101B-1M-R1 resistance box | VID:PID 1A86:7523 (generic CH340) | `docs/QR10x_Spec_Manual_V3.0.pdf` |
| `xl9535_relay_mux/` | XL9535 16-relay board behind an ESP32 USB-to-I2C bridge | VID 303A | `docs/XL9535_XL9555_Datasheet_Rev2.3.pdf` |
| `ad3/` | Digilent Analog Discovery 3, listen-only | Through the WaveForms SDK | `docs/WaveForms_SDK_Reference_Manual.pdf` |
| `ophyd_adapters/` | Not an instrument: the shared ophyd signals the devices are built from | | |

## Layout of an instrument package

```
<instrument>/
  pyproject.toml
  README.md            What the instrument is, its quirks, where the manual came from.
  docs/                Manuals and datasheets, as PDFs, unmodified.
  <package>/
    driver.py          Generic driver. No Bluesky imports, so it works from any script.
    sim.py             Stand-in for the instrument's port, for tests and dry runs.
    ophyd_device.py    Bluesky device: only Components that point at driver methods.
  tests/               Need no hardware. They run against sim.py.
```

Rules:
- **Put behaviour in the driver.** It should expose everything the manual's command set offers, plus a raw `write`/`query` for anything else. Checks such as rate limits and range validation belong there too, so a script that skips Bluesky gets them as well.
- **Keep the ophyd device thin.** Each Component names a driver attribute, or takes a lambda for a small conversion such as an enum to its name. If a device needs logic, move the logic into the driver.
- **Choose the Component's kind:**
  - Settings and identity are `kind="config"`. Bluesky records them in every descriptor, which is where a run's instrument metadata comes from.
  - Values that change during a run are `"normal"` or `"hinted"`. Use `MeasurementSignal` for readings, so each event takes exactly one reading.
- **Write the simulator from the manual's examples,** and test the driver against it.
- **Keep manuals in `docs/`** with their source in the instrument's README. Don't edit them.

## Adding an instrument

1. Copy the layout above and add the package to `[pypi-dependencies]` in `pixi.toml`.
2. Write `driver.py` from the manual, then `sim.py`, then tests.
3. Write `ophyd_device.py`, then add a test that runs it through `bluesky.plans.count`.
4. Add it to the table above and run `pixi run format` and `pixi run test`.
