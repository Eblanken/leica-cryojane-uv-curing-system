# GW Instek GDM-8341

50,000-count dual-display bench multimeter, controlled over its rear USB port as a virtual COM port.

- **Driver:** `gdm8341.driver.Gdm8341` covers the manual's command set: CONFigure and MEASure on both displays, SENSe, CALCulate, triggering, system, status and IEEE 488.2 commands. `write` and `query` pass anything else through.
- **Bluesky:** `gdm8341.ophyd_device.Gdm8341Device`.
  - Set `function` to a `Function` name, such as `"RESISTANCE"`. Each trigger then takes one reading of display 1.
  - Each event records the reading and the range it was taken on.
  - Every descriptor records the function, auto range, rate, input impedance, model, serial number and firmware.
- **Simulator:** `gdm8341.sim.SimulatedGdm8341`.

## Setup

- Install the GW Instek USB VCP driver from https://www.gwinstek.com/en-US/products/downloadSeriesDownNew/15101/673
- On the meter, MENU > I/O > USB sets the baud rate. The driver defaults to 115200.
- The meter shows RMT while under remote control. LOCAL/2ND, or `local()`, returns it to the front panel.

## Quirks

- Settings commands get no reply. Call `errors()` after configuring to see whether the meter rejected anything.
- `MEAS:...?` selects the function on every call. After a range change, the first conversion can take a few seconds, which is why the read timeout is 5 s.
- An open circuit on resistance reads about 9.9E37. Treat anything above `OVERLOAD_THRESHOLD` as overload.
- The manual lists no MEASure command for capacitance. Configure it and use `read()` instead.

## Documents

- `docs/GDM-834X_User_Manual.pdf`: GDM-834X Series User Manual, GW Instek part number 82DM-83420E01. The remote command reference is in the Command Overview chapter (manual page 106). Downloaded 2026-11-06 from https://www.manufakturet.create.aau.dk/digitalAssets/858/858521_gdm8341-gdm-8342-um.pdf, because GW Instek's own download page (https://www.gwinstek.com/en-global/products/downloadSeriesDownNew/9644/670, Rev. D) blocks scripted downloads.
