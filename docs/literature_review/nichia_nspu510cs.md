# Nichia NSPU510CS (LED used by Pinskiy et al. 2015)

Source: Nichia specification STS-DA1-2745A (Cat.No.130520, 2013), via a Conrad-hosted copy. A later revision, STS-DA1-2745E (Cat.No.161031), exists and was not read. Check it before ordering.

5 mm round through-hole UV LED, epoxy lens partly loaded with diffuser, built-in ESD protection device, Ag-plated copper leadframe.

## Absolute maximum ratings (TA = 25 °C)

| Item | Rating |
|---|---|
| Forward current IF | 20 mA |
| Pulse forward current IFP | 80 mA (pulse width ≤ 10 ms, duty ≤ 10%) |
| Allowable reverse current IR | 85 mA |
| Power dissipation PD | 80 mW |
| Operating temperature | -30 to 85 °C |
| Storage temperature | -40 to 100 °C |
| Junction temperature | 100 °C |

Derating: allowable forward current is 20 mA up to 25 °C ambient, falling linearly to 4 mA at 85 °C. A 5 to 30 s cure is continuous operation for this part, so the 80 mA pulse rating does not apply. Plan on 20 mA maximum, 15 mA nominal.

## Typical characteristics (TA = 25 °C, IF = 15 mA)

| Item | Typ |
|---|---|
| Forward voltage | 3.3 V |
| Radiant flux | 15100 µW (CIE 127:2007) |
| Peak wavelength | 375 nm |
| Spectral half width | 9.0 nm |

The datasheet gives no viewing angle as a number. See Directivity below.

## Ranks

| Item | Rank | Range |
|---|---|---|
| Forward voltage | - | 2.9 to 4.0 V (tolerance ±0.05 V) |
| Radiant flux | 7 | 4800 to 6800 µW |
| | 8 | 6800 to 9600 µW |
| | 9 | 9600 to 13600 µW |
| | 10 | 13600 to 19200 µW |
| | 11 | 19200 to 27200 µW |
| Peak wavelength | Ub | 370 to 380 nm (tolerance ±3 nm) |

Radiant flux tolerance is ±10%. Nichia ships any mix of these ranks and decides the mix per shipment. That is a 5.7:1 spread in flux between the lowest and highest rank. Per-LED variation therefore matters for uniformity: order a single flux rank, sort LEDs before assembly, or rely on a dense array to average it out.

## Curves read from the graphs (reference only, not guaranteed)

Values below were read by eye from the plots and are approximate.

- **Spectrum:** a single narrow peak at 375 nm. Emission is essentially zero above about 410 nm and below about 350 nm.
- **Directivity:** a narrow batwing, not Lambertian. Intensity peaks at about ±12° off axis, about 0.7 of peak on axis, and falls to about half of peak at roughly ±17°. Almost nothing beyond about ±25°. The illumination model must use this digitized curve, not a `cos^m` fit.
- **Radiant flux vs ambient temperature (15 mA):** nearly flat. About 0.98 at -30 °C, a broad maximum of about 1.02 near 0 °C, about 1.0 at 25 °C and about 0.92 at 85 °C. At the cryostat chamber's -16 °C, expect roughly the 25 °C value.
- **Radiant flux vs forward current (25 °C):** close to linear. About 1.0 at 15 mA, 1.3 at 20 mA, 5.2 at 80 mA.
- **Forward voltage vs ambient temperature (15 mA):** about 3.45 V at -30 °C, about 3.37 V at 25 °C, about 3.15 V at 85 °C. Roughly -2.5 mV/°C. At -16 °C expect about 3.43 V.
- **Forward voltage vs current (25 °C):** about 3.0 V at 1 mA, about 3.4 V at 15 mA, about 4.0 V at 80 mA.
- **Peak wavelength:** about 0.04 nm/°C. About 373 nm at -30 °C, 375 nm at 25 °C. At -16 °C expect about 373.5 nm. Nearly independent of current up to about 50 mA.

## Reliability notes

- Low-temperature operating life: TA = -30 °C, IF = 15 mA, 1000 hours, 0 of 50 failed. Running inside the cryostat at -16 °C is within the tested envelope.
- Low-temperature storage: -40 °C, 1000 hours, 0 of 50 failed.
- Temperature cycle: -40 to 100 °C, 100 cycles, 0 of 50 failed.
- Failure criteria are radiant flux below half the lower spec limit, or forward voltage above 1.1 times the upper limit.

## Assembly notes

- Hand soldering: 350 °C max, 3 s max, no closer than 3 mm from the base of the lens. Solder only once.
- Do not solder the LED flush to the PCB. Nichia advises a gap between lens and board, and says direct soldering to double-sided boards must be avoided because of heat on the lens.
- Form leads at least 3 mm from the epoxy base, before soldering, at room temperature.
- Bag shelf life is 3 months at < 30 °C and 70% RH. After that, store sealed with desiccant, 1 year maximum.
- Silver-plated leadframe: the datasheet warns that corrosive environments can tarnish it. It names no specific substances. Sulfur-bearing rubbers and foams are a common cause of silver tarnish in general, so check the foam pad and spacers in a Pinskiy-style design.
- Condensation: do not store the LEDs where temperature and humidity swing widely. A board cycled in and out of the cryostat sees exactly that, so a conformal coat or a sealed window over the LEDs is worth considering.
- Cleaning: isopropyl alcohol only. No water, benzine or thinner, and avoid ultrasonic cleaning.
- Drive: Nichia recommends constant current per LED. For constant-voltage supplies it recommends a series resistor per LED (its "Circuit B"), which is what Pinskiy used. Run above 10% of the sorting current (above 1.5 mA) for stable characteristics.
- ESD check: an ESD-damaged LED shows VF < 2.0 V at IF = 0.5 mA.
- The datasheet notes that this LED also emits some visible light.
- Eye safety: Nichia says the LEDs radiate intense UV and must not be viewed directly, even through optics. Wear UV protective glasses where reflections are possible. The packaging labels say Class 3B (box) and Class 1M (bag). The enclosure must keep the operator from seeing the LEDs directly.

## Estimate of the Pinskiy array's irradiance

The paper does not state drive current or irradiance. An upper-bound estimate, assuming typical flux:

- 65 LEDs x 15.1 mW at 15 mA = about 0.98 W per slide.
- Slide area 2.5 x 7.5 cm = 18.75 cm².
- If all of that light landed on the slide: about 52 mW/cm². An 8 to 10 s cure would then deliver about 0.4 to 0.5 J/cm².

Real irradiance is lower. Some light misses the slide or is lost in the glass plate, and their resistor values and flux ranks are unknown. With the geometry in `pinskiy_2015_tape_transfer.md` (0.25 in pitch, LED tips about 6 to 8 mm below the slide), roughly 70% of the light should land on the slide. That gives a slide-average of about 30 to 40 mW/cm² at 15 mA and typical rank. This is an estimate. Measure a built array before fixing the cure time.
