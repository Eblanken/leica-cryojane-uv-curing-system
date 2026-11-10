# Pinskiy et al. 2015: LED replacement for the CryoJane UV lamp

Pinskiy V, Jones J, Tolpygo AS, Franciotti N, Weber K, Mitra PP (2015). High-Throughput Method of Whole-Brain Sectioning, Using the Tape-Transfer Technique. PLoS ONE 10(7): e0102363. doi:10.1371/journal.pone.0102363. Open access, CC BY 4.0.

Cold Spring Harbor Laboratory. The corresponding author offers model files and engineering drawings on request. Received from the lab (Mark Bowman) by email on 24 Sep 2026.

## What they built

- A curing platform that sits in the cryostat and holds two 1 x 3 in slides side by side, each in a slot in the top surface.
- Under each slot: 65 Nichia NSPU510CS 5 mm UV LEDs, 5 rows of 13, on a four-layer PCB (Sunstone). Each LED has its own resistor, in a parallel network, to even out intensity.
- Stack from bottom: 3D-printed base enclosure, LED PCB, glass plate, top cover, foam spacers, then a lever door with a foam pad.
- The lever door blocks UV for the operator and presses the slides lightly, which they say helps curing.
- Enclosure, lever door and top cover are PolyJet-printed VeroWhite.
- Both arrays run from one DC supply, an Omron S8VS-03005 (5 V, 30 W), through an on/off latching switch.

Note: Mark's email linked an Omron S8VS-06024 (24 V, 60 W), but the paper names the S8VS-03005. Use the paper's part, or choose a supply to suit our own LED string design.

## Geometry measured from Fig. 2

The text gives no LED-to-slide distance. Fig. 2's front view is a scaled CAD render with callouts: 5.00 in overall width, 5.71 in overall height with the door open, 4.25 in door height, and one ".25 in" callout. The figure was rendered at 600 dpi and measured in pixels, using the 5.00 in callout as scale (about 162 px/in). The measured LED diameter came out at about 5.3 mm, which matches the 5 mm part and supports the scale.

- **LED pitch: 0.25 in (6.35 mm), probably in both directions.** The 5 LEDs across the slide measure 0.26 in apart. At 0.25 in, 5 LEDs span exactly 1 in and 13 LEDs span exactly 3 in, so the outer LED centres line up with the slide edges.
- **LED tip to slide: about 0.25 to 0.30 in (6 to 8 mm).** The ".25 in" callout has a vertical arrow from an LED tip to the slide support surface, and a horizontal arrow between two LEDs, sharing one label. Pixel measurement gives about 0.30 in (7.5 mm) from LED dome tip to the underside of the slide.
- **Stack between LEDs and slide:** a glass plate under a 3D-printed top cover. Neither thickness is stated.
- **LED height above PCB:** about 0.30 in (7.7 mm), so the LEDs sit flush or nearly flush on the board.
- **Die to slide: about 12 to 14 mm.** The emitting die sits several mm below the dome tip inside the lens, so add that to the tip-to-slide distance.
- **Platform footprint:** about 5.0 x 4.5 in.

These figures come from a render, not a drawing, so treat them as ±1 mm. The authors offer the CAD on request.

Uniformity check: the beam's half-intensity half-angle is about 17°. At a 13 mm die-to-slide distance, each LED's half-intensity spot is about 8 mm across, larger than the 6.35 mm pitch, so neighbouring spots overlap. With the outer LEDs at the slide edges, the edges have neighbours on one side only. Expect the edges and corners to receive noticeably less than the centre, probably around half. That is still inside the 5 to 30 s dose window.

## Comparison with the commercial CryoJane (paper's Table 1)

| | CryoJane | LED system |
|---|---|---|
| UV pulse | 8 ms | about 5 to 6 s, can be longer |
| Pulses per slide | 2 or more, rotating the slide between pulses | 1 |
| Interval between pulses | more than 30 s, or the fuse blows | none |
| "Total irradiance per pulse" | 40 | 1600 to 2000 |
| Slides per cure | 1 | 2 |
| Fuse replacement | 2 to 5 times a week | no fuses |
| Throughput | 20 slides/h | 50 slides/h |

The table labels the dose row "uW-sec" with no area. Treat it as a relative figure only: the LED system gives about 40 to 50 times the CryoJane's dose per cure. The paper gives no absolute irradiance (mW/cm²) or LED drive current.

The lab says the original CryoJane bulb is 360 nm. An 8 ms pulse and a fuse suggest a flash lamp, so its spectrum is probably broadband rather than a single 360 nm line. This is an inference, not from the paper.

## Process requirements from the paper

- **Target:** one 1 x 3 in (25 x 75 mm) slide per station, holding up to 3 tape windows. Tissue blocks are trimmed to about 20 x 28 mm.
- **Uniformity:** the array must cure the whole slide in one exposure, without rotating it.
- **Exposure:** 5 s suffices for most samples, and 8 to 10 s is recommended for consistency. The table quotes 5 to 6 s.
- **Under-cure:** tissue stays on the tape when peeled. Increase exposure by a few seconds.
- **Over-cure:** beyond about 30 s the tape becomes hard to peel. Avoid it.
- **Environment:** inside the cryostat chamber, at -16 ± 2 °C. Specimen is at -14 ± 2 °C. Room is 17 to 19 °C, 45 to 60% RH.
- **Thermal limit:** until cured, the section and everything it touches must stay colder than the specimen temperature. A warmed section compresses against the tape and tears.
- **Slide temperature:** slides chilled in the cryostat for more than 30 min cure less effectively. The polymer's UV response is temperature-sensitive.
- **Maintenance:** clean the glass plate over the LEDs with absolute alcohol to remove frost and dust.
- **Operator safety:** UV is shielded by the lever door. Light was still visible through the enclosure vents and reflected off the cryostat's metal.
- **Consumables:** Leica tape windows (39475214), pre-coated slides (39475208), or in-house coating with Solution A (39475270, cleaner) and Solution B (39475272, the UV-activated polymer).

## Requirements this implies for our design

1. **Dose window.** The usable exposure runs from about 5 s (enough) to about 30 s (too much), roughly a 6:1 range. Aiming at 8 to 10 s, the dimmest point on the slide must still get at least about 5 s worth of dose. The brightest point must stay under about 30 s worth. That allows a min/max irradiance ratio of about 0.5 with margin at both ends. The uniformity requirement is therefore loose. A diffuser may be unnecessary if the LED array is designed well.
2. **Irradiance target.** Without an absolute number in the paper, define the target as "match the Pinskiy array". Two ways to get it:
   - Estimate it from 65 x NSPU510CS radiant flux at their drive current over the slide area. Needs the datasheet and their resistor values.
   - Measure a reference unit with a radiometer, if the lab or the authors can lend one or provide the CAD.
3. **Wavelength.** NSPU510CS is a near-UV part. Its peak wavelength needs confirming from the Nichia datasheet; the lab's 360 nm figure for the original bulb is close. The paper reports visible blue light during curing, which suggests the LED emission tail reaches into the visible.
4. **Heat.** The LEDs sit under a glass plate directly beneath the slide, in a -16 °C chamber. Run time is about 10 s per cure at up to 50 slides/h. Check the slide's temperature rise. A temperature rise during curing may be acceptable, because the section is fixed once the polymer cures. Heat soaking into the slide or glass plate *before* the next slide is loaded is the risk. Measure the plate temperature over repeated cycles.
5. **Frost.** The window over the LEDs must be cleanable with absolute alcohol, and ideally resist frost. This favours glass or fused silica over PTFE as the top surface the slide rests on.
6. **Pressure.** Keep a lid that presses the slide flat and blocks UV.
7. **Capacity.** Two slide stations per unit, as in the paper, supports alternating-series collection. The lab wants 3 units.
8. **Timing control.** Their system used a manual latching switch. A timed exposure (default about 8 s, adjustable 3 to 30 s, with an interlock on the lid) would improve consistency and protect against over-cure.

## Questions to resolve

- Resolved: space is not a constraint. The platform replaces the UV station of the lab's Leica CryoJane (Model 500 ECU, reverse-engineered in `hardware/reverse_engineered/`), and the available space is several feet on a side.
- Resolved: the lab uses Leica pre-coated slides (39475208). Their polymer's action spectrum is not published. The original lamp is reported as 360 nm and Pinskiy's 375 nm LEDs cured them, so stay in the 365 to 375 nm range. 395 or 405 nm LEDs are untested on these slides.
- Resolved: NSPU510CS datasheet values are in `nichia_nspu510cs.md`. Its plotted spectrum shows little above about 410 nm, but the datasheet says the LED also emits some visible light, which explains the blue glow the paper reports. Fluorescence from the enclosure plastics may add to it.
- Whether to ask the corresponding author for the CAD and PCB files, as Mark suggested.
