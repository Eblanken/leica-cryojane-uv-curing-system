"""NTC thermistor conversions: sense-divider voltage, resistance and temperature."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KELVIN_OFFSET = 273.15
T25_K = 298.15


def divider_voltage_to_resistance(volts, pull_up_ohm: float = 10_000.0, supply_v: float = 5.0):
    """Thermistor resistance from the voltage at the node between it and its pull-up to the supply."""
    volts = np.asarray(volts, dtype=float)
    return pull_up_ohm * volts / (supply_v - volts)


def resistance_to_divider_voltage(ohms, pull_up_ohm: float = 10_000.0, supply_v: float = 5.0):
    ohms = np.asarray(ohms, dtype=float)
    return supply_v * ohms / (ohms + pull_up_ohm)


@dataclass(frozen=True)
class SteinhartHart:
    """1/T = a + b ln(R) + c ln(R)^3, with T in kelvin and R in ohms."""

    a: float
    b: float
    c: float

    def temperature_c(self, ohms):
        log_r = np.log(np.asarray(ohms, dtype=float))
        return 1.0 / (self.a + self.b * log_r + self.c * log_r**3) - KELVIN_OFFSET

    def resistance_ohm(self, temperature_c):
        """Inverse of temperature_c.

        Uses Newton iteration because the usual closed form has no real answer
        when c is negative, which narrow-range fits can produce.
        """
        inverse_t = 1.0 / (np.asarray(temperature_c, dtype=float) + KELVIN_OFFSET)
        log_r = (inverse_t - self.a) / self.b
        for _ in range(20):
            error = self.a + self.b * log_r + self.c * log_r**3 - inverse_t
            log_r = log_r - error / (self.b + 3.0 * self.c * log_r**2)
        return np.exp(log_r)


@dataclass(frozen=True)
class Beta:
    """R = r25 exp(beta (1/T - 1/298.15)), with T in kelvin."""

    r25_ohm: float
    beta_k: float

    def temperature_c(self, ohms):
        inverse_t = np.log(np.asarray(ohms, dtype=float) / self.r25_ohm) / self.beta_k + 1.0 / T25_K
        return 1.0 / inverse_t - KELVIN_OFFSET


def fit_beta(ohms, temperature_c) -> Beta:
    inverse_t = 1.0 / (np.asarray(temperature_c, dtype=float) + KELVIN_OFFSET) - 1.0 / T25_K
    slope, intercept = np.polyfit(inverse_t, np.log(np.asarray(ohms, dtype=float)), 1)
    return Beta(float(np.exp(intercept)), float(slope))


def fit_steinhart_hart(ohms, temperature_c) -> SteinhartHart:
    """Least-squares fit in 1/T, which is linear in the three coefficients."""
    log_r = np.log(np.asarray(ohms, dtype=float))
    inverse_t = 1.0 / (np.asarray(temperature_c, dtype=float) + KELVIN_OFFSET)
    design = np.column_stack([np.ones_like(log_r), log_r, log_r**3])
    # Column scaling keeps the normal equations well conditioned: ln(R)^3 is far larger than 1.
    scale = np.linalg.norm(design, axis=0)
    coefficients, *_ = np.linalg.lstsq(design / scale, inverse_t, rcond=None)
    return SteinhartHart(*(coefficients / scale))
