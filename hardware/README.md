# Hardware

- `reverse_engineered/`: captures of existing boards. Treat these as a record of what the board is, and keep design changes out of them.
- `products/`: KiCad projects for the replacement design.

Each KiCad project gets its own folder. Put libraries used by one project inside its folder. Add a shared `hardware/libraries/` when a second project needs them.

## reverse_engineered/leica_cryojane_model_500_pad_heater

The ECU heater board. The root schematic instantiates `heater.kicad_sch` twice, as the `PAD` and `BATH` sheets.

Sense and drive for each channel:
- The thermistor connects between `HEATER_SENSE` (P1.3 on PAD) and GND (P1.2), with a 10 kohm pull-up to +5 V (R101).
- The heater connects between the +60 V net (P1.1) and `HEATER_LOAD` (P1.4), switched low-side by a CMPTA14 Darlington.
- A 20 kohm trimmer (R106) in a 10 k / 20 k / 10 k divider sets the regulation point, 1.25 V to 3.75 V at the sense comparator.
