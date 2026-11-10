import numpy as np
import pytest
from ad3.driver import Ad3, Ad3Error, summarize_edges
from ad3.ophyd_device import Ad3Device
from ad3.sim import SimulatedDwf
from bluesky import RunEngine
from bluesky import plans as bp


def _pwm(frequency_hz: float, high_fraction: float):
    def source(times):
        pin = ((times * frequency_hz) % 1.0) < high_fraction
        return np.where(pin, 2.4, 0.08), np.full_like(times, 0.03), pin.astype(int)

    return source


def test_edges_report_duty_over_whole_periods():
    bits = np.tile([1, 1, 1, 0], 100)
    summary = summarize_edges(np.r_[0, bits], 1_000.0)
    assert summary.high_fraction == pytest.approx(0.75)
    assert summary.frequency_hz == pytest.approx(250.0)
    assert summary.rising_edges == 100


@pytest.mark.parametrize("level", [0, 1])
def test_a_steady_line_has_no_frequency(level):
    summary = summarize_edges(np.full(1_000, level), 1_000.0)
    assert (summary.high_fraction, summary.frequency_hz) == (level, 0.0)
    assert summary.rising_edges == 0


def test_measure_reduces_a_capture_to_duty_and_voltages():
    with Ad3(dwf=SimulatedDwf(_pwm(100.0, 0.3)), window_s=1.0) as ad3:
        result = ad3.measure()
        assert result.pin_high_fraction == pytest.approx(0.3, abs=0.01)
        assert result.pin_frequency_hz == pytest.approx(100.0, rel=0.01)
        assert result.ch1_max_v == pytest.approx(2.4)
        assert result.ch1_min_v == pytest.approx(0.08)
        assert result.ch2_mean_v == pytest.approx(0.03)
        assert ad3.last is result


def test_reads_identity_and_closes():
    sim = SimulatedDwf()
    with Ad3(dwf=sim) as ad3:
        assert ad3.identity.model == "Analog Discovery 3"
        assert ad3.identity.sdk_version == "3.22.2"
        assert sim.open_count == 1
    assert sim.open_count == 0


def test_sdk_failures_raise_with_its_message():
    sim = SimulatedDwf()
    with Ad3(dwf=sim) as ad3:
        sim.fail_next = "FDwfAnalogInReset"
        with pytest.raises(Ad3Error, match="FDwfAnalogInReset: simulated failure"):
            ad3.capture()


def test_refuses_to_run_with_digital_outputs_enabled():
    sim = SimulatedDwf()
    sim.output_mask = 1
    with pytest.raises(Ad3Error, match="outputs"):
        Ad3(dwf=sim)
    assert sim.open_count == 0


def test_never_calls_an_output_function():
    sim = SimulatedDwf(_pwm(100.0, 0.5))
    with Ad3(dwf=sim, window_s=0.5) as ad3:
        ad3.measure()
        ad3.read_pins()
    outputs = [
        call
        for call in sim.calls
        if "AnalogOut" in call or "AnalogIO" in call or "DigitalOut" in call
    ]
    assert outputs == []


def test_device_takes_one_capture_per_event():
    sim = SimulatedDwf(_pwm(50.0, 0.25))
    documents = []
    device = Ad3Device(Ad3(dwf=sim, window_s=0.5), name="ad3")
    RunEngine({})(
        bp.count([device], num=2), lambda name, doc: documents.append((name, doc))
    )
    events = [doc for name, doc in documents if name == "event"]
    assert len(events) == 2
    assert events[0]["data"]["ad3_pin_high_fraction"] == pytest.approx(0.25, abs=0.02)
    assert events[0]["data"]["ad3_pin_frequency_hz"] == pytest.approx(50.0, rel=0.02)
    assert sim.calls.count("FDwfAnalogInReset") == 2
    descriptor = next(doc for name, doc in documents if name == "descriptor")
    assert descriptor["configuration"]["ad3"]["data"]["ad3_sdk_version"] == "3.22.2"


def test_a_failed_capture_is_retried_after_reopening():
    sim = SimulatedDwf(_pwm(100.0, 0.3))
    with Ad3(dwf=sim, window_s=0.3) as ad3:
        sim.fail_next = "FDwfDigitalInStatus"
        result = ad3.measure()
        assert result.attempts == 2
        assert result.pin_high_fraction == pytest.approx(0.3, abs=0.02)
        assert (sim.opens, sim.open_count) == (2, 1)


def test_a_capture_that_finishes_early_is_retried():
    sim = SimulatedDwf(_pwm(100.0, 0.3))
    with Ad3(dwf=sim, window_s=0.3) as ad3:
        sim.done_early = True
        assert ad3.measure().attempts == 2


def test_gives_up_after_its_attempts():
    sim = SimulatedDwf()
    with Ad3(dwf=sim, window_s=0.2, attempts=2) as ad3:
        sim.done_early = True
        sim.fail_next = "FDwfAnalogInStatus"
        with pytest.raises(Ad3Error, match="2 captures failed"):
            ad3.measure()


def test_tolerated_failures_come_back_as_nan():
    sim = SimulatedDwf()
    with Ad3(dwf=sim, window_s=0.2, attempts=2, tolerate_failures=True) as ad3:
        sim.done_early = True
        sim.fail_next = "FDwfAnalogInStatus"
        result = ad3.measure()
        assert result.attempts == -2
        assert np.isnan(result.pin_high_fraction)
        assert sim.open_count == 1
        assert ad3.measure().attempts == 1


def test_zero_attempts_retries_until_a_capture_works():
    sim = SimulatedDwf(_pwm(100.0, 0.3))
    with Ad3(dwf=sim, window_s=0.2, attempts=0) as ad3:
        failures = iter(
            ["FDwfAnalogInStatus", "FDwfDigitalInStatus", "FDwfAnalogInReset"]
        )
        original = ad3.capture

        def flaky(window_s=None):
            name = next(failures, None)
            if name:
                sim.fail_next = name
            return original(window_s)

        ad3.capture = flaky
        assert ad3.measure().attempts == 4
