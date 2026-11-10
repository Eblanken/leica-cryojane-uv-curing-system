"""Screen candidate UV LEDs for the curing board: irradiance, uniformity, cost and fit.

Geometry, per slide station: the LED array fills a rectangle the size of the slide
(25.4 x 76.2 mm) and faces the slide from h = 12.7 mm away. The board has two stations,
so totals (LED count, cost, power) are doubled.

Beam model: each LED is a generalized Lambertian source, I(theta) = I0 cos^m(theta),
with m = -ln 2 / ln cos(viewing_angle / 2). Irradiance on the slide plane from one LED
is E = I0 h^(m+1) / r^(m+3). The total flux fixes I0 = flux (m + 1) / (2 pi).

Walls: each station can sit in a mirror-walled box (the mask) running from the board to
the slide, with walls on the slide's outline. Specular walls are modelled with image
LEDs up to --wall-orders reflections, each weighted by --wall-reflectance per bounce.
Light the walls absorb, or that escapes past the last modelled order, counts as spill.
Set --wall-reflectance 0 for an open array with no mask.

Optional diffuser: an engineered diffuser just above the LEDs widens each beam. It is
modelled as adding its FWHM in quadrature to the LED's, at a fixed transmission.

Flux at max current scales linearly from the test current, times a droop factor when
the max current is above the test current. Every input in candidates.csv lists what
is still unverified. Treat the output as a ranking, not a design.

Run: pixi run python scripts/uv_led_layout/screen_leds.py [--cure-time 1 --dose 260]
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
SLIDE_W_MM = 25.4  # across the slide
SLIDE_L_MM = 76.2  # along the slide
STATIONS = 2


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidates", type=Path, default=HERE / "candidates.csv")
    p.add_argument(
        "--height-mm", type=float, default=12.7, help="LED to slide distance"
    )
    p.add_argument(
        "--dose",
        type=float,
        default=260.0,
        help="required dose, mJ/cm^2. Default is Pinskiy 2015's upper bound "
        "(52 mW/cm^2 for 5 s); the real figure is unmeasured.",
    )
    p.add_argument("--cure-time", type=float, default=1.0, help="target cure time, s")
    p.add_argument(
        "--min-uniformity", type=float, default=0.5, help="min/max irradiance"
    )
    p.add_argument("--chamber-c", type=float, default=-24.0)
    p.add_argument("--band-nm", type=float, nargs=2, default=(360.0, 380.0))
    p.add_argument(
        "--power-budget-w", type=float, default=215.0, help="electrical, LEDs"
    )
    p.add_argument("--bom-budget", type=float, default=200.0, help="whole board, USD")
    p.add_argument("--other-bom", type=float, default=90.0, help="non-LED BOM, USD")
    p.add_argument(
        "--clearance-mm", type=float, default=1.0, help="added to package size"
    )
    p.add_argument("--droop", type=float, default=0.85, help="flux factor above test I")
    p.add_argument("--diffuser-fwhm-deg", type=float, default=40.0)
    p.add_argument("--diffuser-transmission", type=float, default=0.85)
    p.add_argument(
        "--wall-reflectance",
        type=float,
        default=0.85,
        help="mirror walls round each station at 365 nm (polished Al about 0.85); 0 = none",
    )
    p.add_argument("--wall-orders", type=int, default=3, help="reflections modelled")
    p.add_argument(
        "--max-spill", type=float, default=0.10, help="flag spill above this"
    )
    p.add_argument("--grid-mm", type=float, default=1.0, help="slide sample spacing")
    return p.parse_args()


def lambertian_order(fwhm_deg):
    half = math.radians(fwhm_deg / 2)
    return -math.log(2) / math.log(math.cos(half))


def slide_points(step):
    xs = np.arange(-SLIDE_L_MM / 2 + step / 2, SLIDE_L_MM / 2, step)
    ys = np.arange(-SLIDE_W_MM / 2 + step / 2, SLIDE_W_MM / 2, step)
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel(), step * step


def mirror(coords, k, size):
    """Position of the k-th image of coords in walls at +-size/2."""
    return k * size + (-1) ** k * coords


def led_centres(nx, ny, reflectance, orders):
    """LED positions plus their wall images, with each one's weight."""
    xs = (np.arange(nx) + 0.5) * SLIDE_L_MM / nx - SLIDE_L_MM / 2
    ys = (np.arange(ny) + 0.5) * SLIDE_W_MM / ny - SLIDE_W_MM / 2
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    ks = range(-orders, orders + 1) if reflectance > 0 else [0]
    lx, ly, w = [], [], []
    for kx in ks:
        for ky in ks:
            bounces = abs(kx) + abs(ky)
            if bounces > orders:
                continue
            lx.append(mirror(gx, kx, SLIDE_L_MM))
            ly.append(mirror(gy, ky, SLIDE_W_MM))
            w.append(np.full(gx.size, reflectance**bounces))
    return np.concatenate(lx), np.concatenate(ly), np.concatenate(w)


def irradiance_map(nx, ny, flux_mw, m, h, pts, reflectance, orders):
    """Irradiance at each slide point, mW/cm^2, for one station."""
    px, py, _ = pts
    lx, ly, w = led_centres(nx, ny, reflectance, orders)
    r2 = (px[:, None] - lx[None, :]) ** 2 + (py[:, None] - ly[None, :]) ** 2 + h * h
    i0 = flux_mw * (m + 1) / (2 * math.pi)  # mW/sr
    e = i0 * w[None, :] * h ** (m + 1) / r2 ** ((m + 3) / 2)  # mW/mm^2
    return e.sum(axis=1) * 100.0


def interior_uniformity(e, pts, margin):
    """min/max away from the walls, to tell beam hot spots from edge falloff."""
    px, py, _ = pts
    inner = (np.abs(px) <= SLIDE_L_MM / 2 - margin) & (
        np.abs(py) <= SLIDE_W_MM / 2 - margin
    )
    return e[inner].min() / e[inner].max()


def evaluate_layouts(led, args, pts, fwhm_deg, transmission):
    """Every grid that fits the pitch limit, with its irradiance figures."""
    pitch_min = led.package_mm + args.clearance_mm
    m = lambertian_order(fwhm_deg)
    flux = led.flux_at_max_mw * transmission
    area_mm2 = SLIDE_W_MM * SLIDE_L_MM
    rows = []
    for ny in range(1, int(SLIDE_W_MM // pitch_min) + 1):
        for nx in range(1, int(SLIDE_L_MM // pitch_min) + 1):
            e = irradiance_map(
                nx,
                ny,
                flux,
                m,
                args.height_mm,
                pts,
                args.wall_reflectance,
                args.wall_orders,
            )
            n = nx * ny * STATIONS
            on_slide_mw = e.mean() / 100.0 * area_mm2
            rows.append(
                {
                    "nx": nx,
                    "ny": ny,
                    "leds": n,
                    "cost": n * led.unit_price_usd,
                    "power_w": n * led.max_current_a * led.vf_v,
                    "e_mean": e.mean(),
                    "e_min": e.min(),
                    "uniformity": e.min() / e.max(),
                    "inner_uniformity": interior_uniformity(
                        e, pts, min(SLIDE_W_MM / ny, SLIDE_W_MM / 4)
                    ),
                    "spill": 1 - on_slide_mw / (nx * ny * led.flux_at_max_mw),
                }
            )
    return pd.DataFrame(rows)


def screen(led, args, pts):
    required = args.dose / args.cure_time  # mW/cm^2
    led_budget = args.bom_budget - args.other_bom
    out = {"part": led.part, "required_mw_cm2": required}

    if led.min_operating_c > args.chamber_c:
        return out | {"status": f"reject: rated only to {led.min_operating_c:g} C"}
    lo, hi = args.band_nm
    if not lo <= led.peak_nm <= hi:
        return out | {"status": f"reject: {led.peak_nm:g} nm outside {lo:g}-{hi:g} nm"}

    diffuser = False
    layouts = evaluate_layouts(led, args, pts, led.viewing_angle_deg, 1.0)
    ok = layouts[layouts.uniformity >= args.min_uniformity]
    if ok.empty:
        fwhm = math.hypot(led.viewing_angle_deg, args.diffuser_fwhm_deg)
        layouts = evaluate_layouts(led, args, pts, fwhm, args.diffuser_transmission)
        ok = layouts[layouts.uniformity >= args.min_uniformity]
        diffuser = True
    if ok.empty:
        if (layouts.inner_uniformity >= args.min_uniformity).any():
            reason = "edge falloff: needs reflective walls (raise --wall-reflectance)"
        else:
            reason = "beam too concentrated, even with diffuser"
        return out | {"status": f"reject: {reason}"}

    in_power = ok[ok.power_w <= args.power_budget_w]
    in_budget = in_power[in_power.cost <= led_budget]
    best_physical = (
        in_power.loc[in_power.e_min.idxmax()] if not in_power.empty else None
    )
    best_budget = (
        in_budget.loc[in_budget.e_min.idxmax()] if not in_budget.empty else None
    )
    meets = in_power[in_power.e_min >= required]
    cheapest = meets.loc[meets.cost.idxmin()] if not meets.empty else None

    if cheapest is not None and cheapest.cost <= led_budget:
        status, pick = "ok", cheapest
    elif cheapest is not None:
        status, pick = "over budget", cheapest
    elif best_budget is not None:
        status, pick = "short", best_budget
    else:
        return out | {"status": "reject: no layout inside power and BOM budgets"}
    notes = []
    if diffuser:
        notes.append("needs diffuser")
    if pick.spill > args.max_spill:
        notes.append(f"mask: {pick.spill:.0%} spills off slide")
    return out | {
        "status": status,
        "notes": "; ".join(notes),
        "grid": f"{int(pick.nx)}x{int(pick.ny)}",
        "leds": int(pick.leds),
        "led_cost": pick.cost,
        "power_w": pick.power_w,
        "e_min": pick.e_min,
        "uniformity": pick.uniformity,
        "cure_s": args.dose / pick.e_min,
        "usd_per_mw_cm2": pick.cost / pick.e_min,
        "max_e_min_budget": (
            best_budget.e_min if best_budget is not None else float("nan")
        ),
        "max_e_min_physical": (
            best_physical.e_min if best_physical is not None else float("nan")
        ),
    }


def main():
    args = parse_args()
    leds = pd.read_csv(args.candidates)
    boost = np.where(leds.max_current_a > leds.flux_test_a, args.droop, 1.0)
    leds["flux_at_max_mw"] = (
        leds.flux_mw * leds.max_current_a / leds.flux_test_a * boost
    )
    pts = slide_points(args.grid_mm)

    results = pd.DataFrame([screen(led, args, pts) for led in leds.itertuples()])
    print(
        f"Required: {args.dose:g} mJ/cm^2 in {args.cure_time:g} s = "
        f"{args.dose / args.cure_time:.0f} mW/cm^2 minimum on each slide. "
        f"LED budget ${args.bom_budget - args.other_bom:.0f}, "
        f"LED power {args.power_budget_w:g} W, h = {args.height_mm:g} mm, "
        f"wall reflectance {args.wall_reflectance:g}.\n"
    )
    print(
        "status ok: cheapest layout meeting the target, inside budget. over budget: "
        "cheapest layout meeting the target costs more than the LED budget. short: "
        "nothing inside the budget meets the target; shows the best that does, and "
        "cure_s is the time it needs.\n"
        "e_min is the dimmest point on the slide, mW/cm^2. max_e_min_budget is the best "
        "inside the BOM, max_e_min_physical the best that fits the pitch and power.\n"
    )
    cols = [c for c in results.columns if c != "required_mw_cm2"]
    with pd.option_context(
        "display.width", 200, "display.float_format", "{:.2f}".format
    ):
        print(results[cols].to_string(index=False))
    print("\nUnverified inputs:")
    for led in leds.itertuples():
        print(f"  {led.part}: {led.unverified}")


if __name__ == "__main__":
    main()
