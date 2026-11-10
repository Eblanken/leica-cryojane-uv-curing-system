# UV LED illumination: tools, optimization and reflector materials (draft)

Design notes for the replacement UV curing head. Nothing here has been measured yet. Every number marked "approx." is a ballpark from general knowledge, not from a datasheet or a bench run, and should be replaced with measurements before it drives a design decision.

## Requirements

See `pinskiy_2015_tape_transfer.md` for the reference design (Pinskiy et al. 2015) and the requirements derived from it. In short:

- One 1 x 3 in slide per station, two stations per unit. The lab wants 3 units.
- Cure in one exposure with no slide rotation. Usable exposure is about 5 s (enough) to 30 s (too much), so a min/max irradiance ratio of about 0.5 is enough.
- Reference array: 65 Nichia NSPU510CS per slide, 5 x 13. The paper gives no absolute irradiance. An upper-bound estimate is about 52 mW/cm² (see `nichia_nspu510cs.md`).
- Leica pre-coated slides. Stay at 365 to 375 nm, which is known to cure them.
- The NSPU510CS has a narrow batwing beam (peak about ±12°, half intensity about ±17°), not a Lambertian one. Model it from the digitized directivity curve.
- Original CryoJane bulb is 360 nm, according to the lab.
- Operates inside the cryostat at about -16 °C. The section must not warm before it is cured.

## Open items
- Cavity dimensions, LED part number and LED-to-target distance, which the model needs.
- Bench measurements of reflector reflectance and diffuser transmission at the chosen wavelength.

## Free and open-source modelling tools

### Analytic model (start here)

- Treat each LED as a generalized Lambertian source: `E(x, y) = sum I0 * cos^m(theta) * cos(phi) / r^2`, with `m = -ln 2 / ln(cos theta_half)` from the datasheet half-angle.
- Where the datasheet gives a polar radiant-intensity curve, interpolate it instead of using `cos^m`. Lensed UV LEDs often deviate from Lambertian.
- Moreno and Sun published closed-form optimal spacing-to-height ratios for square, linear, ring and hexagonal LED arrays. Use them as starting points. (Citation to confirm.)
- numpy, scipy, and optionally pymoo or CVXPY, cover the optimization. numpy and scipy are already in `pixi.toml`.
- This model ignores wall reflections, windows and diffusers.

### Non-sequential ray tracers (when walls or optics matter)

| Tool | Notes |
|---|---|
| Raysect | Python/Cython, physically based and spectral. Takes meshes and materials, and has irradiance pipelines on surfaces. Best fit for a reflective cavity with LEDs and a target plane. |
| Mitsuba 3 | Spectral renderer that can place an irradiance meter on the target. Differentiable through Dr.Jit, so LED placement could be optimized by gradient. |
| pvtrace | Python Monte Carlo, built for luminescent concentrators. Simple API, trimesh geometry. |
| SolTrace (NREL), Tonatiuh | Solar flux-map Monte Carlo tools. Usable but awkward for small LED geometry. |

Sequential lens-design tools (Optiland, rayoptics) are a poor fit. Blender Cycles and LuxCore are not radiometric enough to trust absolute irradiance.

## Optimization as a convex problem

Free LED positions make the problem non-convex. Fix a grid of candidate LED sites instead:

1. Compute each site's irradiance map on the target sample points, and stack the maps as the columns of `A`.
2. Irradiance is then linear in per-site intensity: `E = A p`, with `p >= 0`.
3. Superposition holds with reflective walls and diffusers too, so `A` can come from one ray trace per site. The optimizer never calls the ray tracer again.

| Goal | Formulation | Class |
|---|---|---|
| Best min/max ratio | maximize `t` subject to `A p >= t`, `A p <= 1`, `p >= 0`, then rescale `p` | LP |
| Match a target profile | minimize `max abs(A p - E*)` | LP |
| Same, smooth error | minimize `sum (A p - E*)^2` | QP |
| Meet minimum dose under a heat or power cap | minimize `sum p` subject to `E_min <= A p <= E_max` | LP |
| Few LEDs | add an L1 penalty, or reweighted L1 | convex heuristic |

Where convexity breaks:

- LEDs in series strings share a current, so each site is populated or empty. This is a MILP: binary `z`, `p = I0 z`, maximize the irradiance floor under a fixed ceiling and `sum z <= N`. `scipy.optimize.milp` (HiGHS) handles a few hundred sites. This is probably the real form of the problem unless every LED gets its own PWM channel.
- LED height changes `A` nonlinearly. Sweep it in an outer loop.
- Thermal derating couples neighbours. Iterate: solve, estimate junction temperature, rescale the columns of `A`, solve again.
- Cure kinetics are nonlinear in dose, but a minimum-dose threshold is still a linear constraint.

Radiotherapy beam weight optimization has the same structure and is a useful reference.

Proposed starting point: candidate grid, analytic `A`, MILP via `scipy.optimize.milp`, in a notebook under `scripts/uv_led_layout/`. Swap in ray-traced columns later.

## Reflector material: PTFE against machined aluminum

### Optical, 365 to 405 nm (approx.)

| | Virgin PTFE, bulk | Machined aluminum |
|---|---|---|
| Reflectance | 90 to 98%, depending on thickness and grade | 60 to 85% as machined. Pristine evaporated aluminum is about 92%. |
| Scatter | Near-Lambertian | Mixed specular and diffuse, anisotropic along tool marks |
| Uniformity | Multiple diffuse bounces blur LED hot spots | Specular walls image the LEDs and can add hot spots |
| Thickness | Several mm or more. Thin sheet transmits light. | Any |
| Pitfalls | Filled grades (glass, carbon) absorb UV. Specify virgin PTFE. | Anodizing lowers UV reflectance, and black anodize absorbs it. Use bare or polished aluminum, or a UV-enhanced sheet such as Alanod MIRO. |

### Mechanical and thermal

- Thermal conductivity: PTFE about 0.25 W/(m K), aluminum about 150 to 200 W/(m K). Aluminum can double as the LED heatsink.
- Thermal expansion: PTFE about 100 to 120 ppm/K, aluminum about 23 ppm/K.
- PTFE has a crystal phase transition near 19 °C with roughly 1% volume change. The chamber crosses it every cycle between room and cryostat temperature, so a PTFE insert will move. Retain it mechanically (lip, screws in slotted holes). Do not rely on a press fit.
- PTFE cold-flows under load.
- PTFE's porous surface absorbs oils and adhesive vapour. Wet-sanding restores it.
- PTFE is hydrophobic and sheds frost better than cold aluminum.

### Modelling

- Aluminum: flat specular walls act as mirror-image LEDs. Add one image source per wall, scaled by reflectance, to each column of `A`.
- PTFE: diffuse walls need a radiosity model with view factors, or a ray trace (Raysect handles Lambertian materials).

Both keep `E = A p` linear.

Recommendation: aluminum housing as structure and heatsink, lined with virgin PTFE a few mm thick and retained so it can expand. Check in the model whether diffuse walls help much. If the cavity is mostly open around the sample, bare aluminum may be enough.

## Cost of a slide-sized PTFE liner (approx., get real quotes)

Assumed part: about 80 x 30 x 5 to 10 mm, one pocket, a few holes, tolerance +/-0.1 to 0.2 mm.

| Route | Cost, quantity 1 |
|---|---|
| Material only | $5 to 20 for a small block, or $30 to 60 for a 300 x 300 mm sheet |
| Chinese online CNC (JLCCNC, PCBWay) | $15 to 50 plus shipping, 1 to 3 weeks |
| US online CNC (Xometry, Protolabs) | $60 to 150. Quantity 10 is roughly 2 to 4 times cheaper per part. |
| Local job shop | $100 to 250 (about 1 h at $80 to 150/h) |
| DIY | Material only. PTFE cuts easily but burrs and holds tolerance poorly. |

Tolerance tighter than +/-0.1 mm adds cost, and PTFE will not hold it anyway. Walls thinner than about 1.5 mm deflect during cutting.

### Adding a diffusion layer

- One piece with a thin internal web (0.5 to 1 mm, pocketed from both sides): about 1.5 to 2 times the plain liner's cost, and some shops will refuse it. Web thickness varies by +/-0.1 mm or more. Diffuse transmission depends strongly on thickness, so that variation becomes a transmission gradient across the slide.
- Recommended: two machined frames clamping a separate diffuser sheet. About 1.3 to 1.6 times the cost of one liner. Skived PTFE film or sheet (0.25 to 1 mm) costs $10 to 40 a sheet, has much more uniform thickness, and can be swapped to tune uniformity against throughput.
- Alternatives: UV fused silica ground-glass diffuser ($40 to 150, Thorlabs or Edmund), porous optical PTFE sheet, or Spectralon (hundreds of dollars, overkill). Soda-lime glass cuts off below about 350 nm, which is acceptable at 395 to 405 nm only.

## PTFE diffuser attenuation (approx., measure before relying on it)

Standalone skived virgin PTFE, total diffuse transmission, around 400 nm:

| Thickness | Transmission |
|---|---|
| 0.25 mm | 50 to 70% |
| 0.5 mm | 35 to 50% |
| 1 mm | 20 to 35% |
| 2 mm | 10 to 20% |
| 3 mm | 5 to 10% |

- Expect lower values at 365 nm than at 405 nm, because PTFE scatters more at shorter wavelengths.
- Sintered or porous optical PTFE scatters far more and transmits much less at the same thickness.
- PTFE barely absorbs in the near UV, so most of the loss is back-reflection. For thin sheets, transmission falls roughly as 1/thickness, not exponentially. Absorption only dominates in thick sheets.

### Recycling in the cavity

Back-reflected light returns to the LED board and walls and gets more chances to pass. Approximate effective throughput: `T_eff = T / (1 - R_sheet * R_cavity)`.

Example, 1 mm sheet with T about 0.3 and R_sheet about 0.68:

| LED-side surface | R_cavity | T_eff |
|---|---|---|
| Green solder mask | about 0.2 | about 35% |
| White solder mask | about 0.6 | about 51% |
| PTFE-lined, LEDs through holes | about 0.9 | about 77% |

LED packages and gaps absorb some light, so real values will be lower. The reflectance of the LED side of the cavity matters as much as the diffuser thickness.

A diffuser also turns a narrow LED beam into a Lambertian emitter. Keep it close to the slide, or make the walls between diffuser and target reflective, so light does not spread past the target.

### Measurement plan

1. Measure irradiance at a fixed point with and without the sheet, for 2 to 3 thicknesses, at the LED wavelength. The ratio is `T`.
2. Repeat inside a mock-up of the full LED cavity to get `T_eff` with recycling included.
3. Use a photodiode with a known response at the LED wavelength. Some cheap UV sensors (GUVA-S12SD class) fall off above about 370 nm, so check the datasheet before using one at 395 to 405 nm.
4. Log through the existing Bluesky stack.

In the model, the diffuser plus cavity recycling is a fixed linear operator on `p`, so the LP and MILP formulations still hold.
