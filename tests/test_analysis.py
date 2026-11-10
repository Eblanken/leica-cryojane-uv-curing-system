from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from cryojane_bench.logs import (
    read_channel,
    read_hobo_temperature,
    read_picolog,
    read_results_log,
)
from cryojane_bench.plateaus import STABLE_WINDOW, find_plateaus
from cryojane_bench.sweep import QuantityType, SweepChannel, run_sweep
from cryojane_bench.thermistor import (
    Beta,
    SteinhartHart,
    divider_voltage_to_resistance,
    fit_beta,
    fit_steinhart_hart,
    resistance_to_divider_voltage,
)

SCRIPTS = Path(__file__).parents[1] / "scripts"
HEATER_DATA = SCRIPTS / "heater_characterization" / "data"
SENSOR_DATA = SCRIPTS / "sensor_characterization" / "data"
HOBO_STEM = "Chamber-Right 2026-10-26 13_53_30 PDT"

# Coefficients of a common 10 kohm NTC, used as a known answer.
KNOWN = SteinhartHart(1.129e-3, 2.341e-4, 8.775e-8)


def test_inverse_round_trips():
    temperatures = np.array([-40.0, -10.0, 25.0, 85.0])
    assert KNOWN.temperature_c(KNOWN.resistance_ohm(temperatures)) == pytest.approx(temperatures)


def test_inverse_round_trips_with_negative_c():
    curve = SteinhartHart(1.175e-3, 2.715e-4, -2.309e-8)
    temperatures = np.array([-22.0, -10.0, 0.0])
    assert curve.temperature_c(curve.resistance_ohm(temperatures)) == pytest.approx(temperatures)


def test_fit_recovers_known_curve():
    temperatures = np.linspace(-35, 25, 13)
    fitted = fit_steinhart_hart(KNOWN.resistance_ohm(temperatures), temperatures)
    assert fitted.temperature_c(1e4) == pytest.approx(KNOWN.temperature_c(1e4), abs=1e-6)
    assert fitted.c == pytest.approx(KNOWN.c, rel=1e-4)


def test_divider_round_trips():
    assert divider_voltage_to_resistance(resistance_to_divider_voltage(17_500.0)) == pytest.approx(17_500.0)


def test_divider_midpoint():
    assert divider_voltage_to_resistance(2.5) == pytest.approx(10_000.0)


def test_results_log_keeps_small_and_large_values(tmp_path):
    log = tmp_path / "r.txt"
    log.write_text(
        "2026-10-30 22:35:18.294218: Sensor (0): 3.8213 (QuantityType.VOLTAGE)\r\n"
        "2026-10-30 22:35:19.294218: Sensor (0): 1e-05 (QuantityType.VOLTAGE)\r\n"
        "2026-10-30 22:35:20: Sensor (0): -5.1e+37 (QuantityType.RESISTANCE)\r\n"
        "a line that is not data\r\n"
    )
    frame = read_results_log(log)
    assert frame.value.tolist() == [3.8213, 1e-05, -5.1e37]
    assert frame.channel.unique().tolist() == ["Sensor (0)"]


def test_real_sweep_log_parses_every_channel():
    frame = read_results_log(HEATER_DATA / "results_20261030-223516.txt")
    assert len(frame) == 17939
    assert set(frame.channel) == {"Sensor (0)", "Heater (0)", "Sensor (1)", "Heater (1)"}


def test_real_resistance_log_parses_every_line():
    path = SENSOR_DATA / "results_20261025-215246.txt"
    frame = read_results_log(path)
    assert len(frame) == len(path.read_text(encoding="utf-8").splitlines())
    assert set(frame.quantity) == {"RESISTANCE"}


def test_hobo_decoder_matches_the_vendor_export_to_its_rounding():
    decoded = read_hobo_temperature(SENSOR_DATA / f"{HOBO_STEM}.hobo")
    export = pd.read_excel(SENSOR_DATA / f"{HOBO_STEM}.xlsx").set_index("Date-Time (PDT)")["Temperature , °C"]
    assert len(decoded) == len(export)
    assert (decoded - export).abs().max() <= 0.00501  # The export rounds to 0.01 °C.
    assert decoded.min() < -35  # The comparison has to cover the cold end.


def test_beta_fit_recovers_known_curve():
    known = Beta(2400.0, 3750.0)
    ohms = np.array([50_000.0, 20_000.0, 8_000.0])
    fitted = fit_beta(ohms, known.temperature_c(ohms))
    assert fitted.r25_ohm == pytest.approx(2400.0)
    assert fitted.beta_k == pytest.approx(3750.0)


def _stepped_run():
    times = pd.date_range("2026-01-01", periods=4 * 60, freq="1min")
    reference = pd.Series(np.where(np.arange(len(times)) < 120, -30.0, -20.0), index=times)
    readings = reference * 100.0 + 5000.0
    return readings, reference


def test_plateaus_find_each_settled_step_and_skip_the_transition():
    readings, reference = _stepped_run()
    plateaus = find_plateaus(readings, reference, lambda value: (value - 5000.0) / 100.0)
    assert plateaus.reference_c.tolist() == pytest.approx([-30.0, -20.0])
    assert plateaus.value.tolist() == pytest.approx([2000.0, 3000.0])
    step = pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=120)
    assert plateaus.start.max() >= step + STABLE_WINDOW  # The second plateau waits for the step to leave the window.


def test_plateaus_wait_for_a_lagging_device():
    readings, reference = _stepped_run()
    lag = np.exp(-np.arange(len(readings)) / 15.0)  # The device takes much longer to settle than the reference.
    lagging = readings + pd.Series(1000.0 * lag, index=readings.index)
    fast = find_plateaus(readings, reference, lambda value: value / 100.0)
    slow = find_plateaus(lagging, reference, lambda value: value / 100.0)
    assert slow.start.iloc[0] > fast.start.iloc[0]


def test_read_channel_drops_overload_readings():
    results = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01 00:00:02", "2026-01-01 00:00:01", "2026-01-01 00:00:03"]),
        "channel": ["A", "A", "B"],
        "value": [5.0, 9.9e37, 7.0],
    })
    assert read_channel(results, "A").tolist() == [5.0]


def test_real_picolog_matches_its_recorded_sample_counts():
    frame = read_picolog(HEATER_DATA / "20261031_114234.picolog")
    assert frame["ch3"].notna().sum() == 47735
    assert frame["ch1 PMC 0530"].notna().sum() == 93862
    assert frame["ch5"].min() > -36


class _FakeMeter:
    def read_voltage(self):
        return 1.5

    def read_resistance(self):
        return 100.0


class _FakeMux:
    def __init__(self):
        self.selected = []

    def select(self, channel):
        self.selected.append(channel)


def test_sweep_writes_a_log_the_reader_understands(tmp_path):
    mux = _FakeMux()
    log = tmp_path / "r.txt"
    remaining = iter([True, False])
    run_sweep(
        mux, _FakeMeter(),
        [SweepChannel("A (0)", QuantityType.VOLTAGE, 3), SweepChannel("B (0)", QuantityType.RESISTANCE, 9)],
        log, repetitions_per_block=2, settle_s=0.0, keep_running=lambda: next(remaining),
    )
    frame = read_results_log(log)
    assert mux.selected == [3, 9]
    assert frame.value.tolist() == [1.5, 1.5, 100.0, 100.0]
    assert frame.quantity.tolist() == ["VOLTAGE", "VOLTAGE", "RESISTANCE", "RESISTANCE"]
