import importlib
import importlib.util
import sys
from pathlib import Path

import pytest
from cryojane_bench.runs import load_run, load_runs, make_run_engine, run_file_name

SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load_script(path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


@pytest.fixture
def validation():
    return _load_script(
        SCRIPTS
        / "instrument_checks"
        / "qr101b_gdm8341_validation"
        / "qr101b_gdm8341_validation.py"
    )


@pytest.fixture
def acquisition():
    return _load_script(SCRIPTS / "sensor_characterization" / "acquisition.py")


def test_default_points_cover_the_regulation_span_with_overreach(validation):
    points = validation.default_points_ohm()
    assert min(points) == pytest.approx(3_000.0, abs=10)
    assert max(points) == pytest.approx(33_000.0, abs=10)
    assert points == points[: len(points) // 2 + 1] + points[len(points) // 2 - 1 :: -1]
    for regulation in validation.REGULATION_POINTS_OHM.values():
        # Points are rounded to 10 ohm, so the band edges can land just outside 10%.
        assert (
            sum(abs(p - regulation) <= 0.1 * regulation + 10 for p in set(points)) >= 11
        )


def test_validation_run_round_trips_through_disk(validation, tmp_path):
    from gdm8341.ophyd_device import Gdm8341Device
    from qr101b.ophyd_device import Qr101bDevice

    box_driver, meter_driver = validation._simulated_instruments()
    box = Qr101bDevice(box_driver, name="qr101b")
    meter = Gdm8341Device(meter_driver, name="gdm")
    box_driver.set_resistance(1_234.0, settle=False)
    points = [5_000.0, 10_000.0, 5_000.0]

    RE = make_run_engine(tmp_path)
    RE(
        validation.resistance_validation(
            box, meter, points, readings_per_point=2, dwell_s=0
        )
    )

    (run,) = load_runs(tmp_path, plan_name=validation.PLAN_NAME)
    assert run.stop["exit_status"] == "success"
    assert run.start["points_ohm"] == points
    assert "git_commit" in run.start
    table = run.table()
    assert list(table.qr101b_setpoint) == [5_000.0] * 2 + [10_000.0] * 2 + [5_000.0] * 2
    assert run.data_keys()["gdm_reading"]["units"] == "ohm"
    assert run.configuration()["qr101b"]["qr101b_device_type"] == "QR101B-1M-R1"
    assert box_driver.setpoint_ohm == 1_234.0  # Restored after the run.

    summary = validation.summarize(next(tmp_path.glob("*.jsonl")))
    assert list(summary.index) == ["up", "up", "down"]
    assert summary.error_ppm.abs().max() < 1_000


def test_mux_sweep_labels_every_reading_and_opens_the_mux(acquisition, tmp_path):
    from gdm8341.ophyd_device import Gdm8341Device
    from xl9535_relay_mux.ophyd_device import OPEN, RelayMuxDevice

    mux_driver, meter_driver = acquisition._simulated_instruments()
    mux = RelayMuxDevice(mux_driver, name="mux")
    meter = Gdm8341Device(meter_driver, name="gdm")

    RE = make_run_engine(tmp_path)
    RE(
        acquisition.mux_sweep(
            mux, meter, acquisition.CHANNELS, readings_per_block=3, settle_s=0, cycles=2
        )
    )

    run = load_run(next(tmp_path.glob("*.jsonl")))
    table = run.table()
    assert len(table) == 2 * 4 * 3
    assert dict(zip(table.mux_channel, table.channel_name)) == acquisition.CHANNELS
    heater = table[table.channel_name == "Heater (0)"].gdm_reading
    assert heater.between(125, 127).all()
    assert run.configuration()["gdm"]["gdm_function"] == "RESISTANCE"
    assert mux.channel.get() == OPEN


DUTY_DIR = SCRIPTS / "duty_cycle_characterization"


@pytest.fixture
def duty_scripts():
    """The bench's scripts import each other, so their folder stays importable here."""
    sys.path.insert(0, str(DUTY_DIR))
    yield (
        importlib.import_module("bench"),
        importlib.import_module("characterize_series_resistor"),
        importlib.import_module("duty_vs_sensor_resistance"),
    )
    sys.path.remove(str(DUTY_DIR))


def test_series_resistor_measurement_removes_the_meter_gain(duty_scripts, tmp_path):
    bench_module, characterize, _ = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    bench.box.driver.set_resistance(1_234.0, settle=False)
    make_run_engine(tmp_path)(
        characterize.series_resistor(
            bench.box, bench.meter, bench.mux, relay_settle_s=0
        )
    )
    result = characterize.latest_measurement(tmp_path)
    # The simulated meter reads 600 ppm low, or about 5.5 ohm at this value.
    assert result.ohm == pytest.approx(9_087.0, abs=1.0)
    assert result.meter_gain == pytest.approx(1 - 600e-6, abs=50e-6)
    assert bench.box.driver.setpoint_ohm == 1_234.0
    assert bench.mux.driver.bridge._interface.closed_channels == []


def test_duty_points_stay_in_the_gap_free_range_and_cover_the_bracket(duty_scripts):
    _, _, duty = duty_scripts
    coarse = duty.coarse_points_ohm(9_087.0)
    assert (min(coarse), max(coarse)) == (10_000.0, 21_000.0)
    totals = duty.sweep_points_ohm((14_000.0, 14_700.0), 9_087.0)
    assert all(duty.box_setpoint_ohm(total, 9_087.0) for total in totals)
    assert set(coarse) <= set(totals)
    fine = [total for total in totals if 13_950.0 <= total <= 14_750.0]
    assert len(fine) >= duty.SWEEP_FINE_STEPS


def test_duty_plan_refuses_box_values_in_its_gap(duty_scripts):
    _, _, duty = duty_scripts
    with pytest.raises(ValueError, match="gap-free"):
        duty.box_setpoint_ohm(25_000.0, 9_087.0)


def test_find_transition_needs_both_fully_off_and_fully_on(duty_scripts):
    _, _, duty = duty_scripts
    steps = [(10.0, 24.0), (11.0, 23.9), (12.0, 12.0), (13.0, 0.6), (14.0, 0.6)]
    assert duty.find_transition(steps, 24.0) == (11.0, 13.0)
    assert duty.find_transition(steps[:3], 24.0) is None  # Never fully on.
    assert duty.find_transition(steps[3:], 24.0) is None  # Never fully off.
    assert duty.find_transition(steps[1:], 24.0) == (11.0, 13.0)


def _run_duty_plan(duty, bench, tmp_path, devices=(0, 1), **kwargs):
    make_run_engine(tmp_path)(
        duty.duty_vs_sensor_resistance(
            bench.box,
            bench.meter,
            bench.mux,
            list(devices),
            9_087.0,
            locate_dwell_s=0,
            readings_per_block=2,
            relay_settle_s=0,
            dwell_s=0,
            **kwargs,
        )
    )


def test_duty_plan_locates_then_sweeps_and_leaves_the_devices_idle(
    duty_scripts, tmp_path
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    bench.box.driver.set_resistance(1_234.0, settle=False)
    _run_duty_plan(duty, bench, tmp_path)

    runs = load_runs(tmp_path, plan_name=duty.PLAN_NAME)
    phases = ["locate_coarse", "locate_fine", "sweep"]
    assert [run.start["target_device"] for run in runs] == [0] * 3 + [1] * 3
    assert [run.start["phase"] for run in runs] == phases * 2
    assert len({run.start["campaign_uid"] for run in runs[:3]}) == 1
    assert runs[0].start["campaign_uid"] != runs[3].start["campaign_uid"]
    for run in runs:
        relays = bench_module.DEVICES[run.start["target_device"]]
        table = run.table()
        assert run.stop["exit_status"] == "success"
        assert set(table.mux_channel) == {
            relays["sensor_relay"],
            relays["heater_relay"],
        }
        is_idle = table.channel_name.str.startswith("Idle")
        assert set(table.mux_route[is_idle]) == {-1}
        assert set(table.mux_route[~is_idle]) == {relays["route_relay"]}
        assert run.configuration()["gdm"]["gdm_function"] == "VOLTAGE_DC"

    for run in runs[2::3]:
        low, high = run.start["transition_bracket_ohm"]
        regulation = run.start["regulation_estimate_ohm"]
        assert low < regulation < high  # The simulated band is centred on this.
        assert high - low < 2_000.0
        summary = duty.summarize(tmp_path / f"{run_file_name(run.start)}.jsonl")
        assert summary.duty.min() == pytest.approx(0.0, abs=0.01)
        assert summary.duty.max() == pytest.approx(1.0, abs=0.01)
        up = summary.loc["up"].sort_values("sensor_ohm")
        assert up.duty.diff().min() > -0.02
        partial = up[(up.duty > 0.05) & (up.duty < 0.95)]
        assert len(partial) >= 5

    assert bench.mux.driver.bridge._interface.closed_channels == []
    assert bench.box.driver.setpoint_ohm == 1_234.0


def test_duty_plan_optional_readings_agree_with_the_averaged_duty(
    duty_scripts, tmp_path
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True, ad3=True)
    bench.ad3.driver.window_s = 1.0
    _run_duty_plan(duty, bench, tmp_path, ad3=bench.ad3, pwm_checks=True)

    runs = load_runs(tmp_path, plan_name=duty.PLAN_NAME)
    sweeps = {run.start["target_device"]: run for run in runs[2::3]}
    assert set(runs[0].stream_names) == {"primary"}  # Locating needs neither.
    assert set(sweeps[0].stream_names) == {"primary", "heater_ac", "ad3"}
    assert set(sweeps[1].stream_names) == {
        "primary",
        "heater_ac",
    }  # No AD3 on device 1.
    assert sweeps[0].start["ad3_wiring"]["d0"].startswith("U101.6")
    assert sweeps[1].start["ad3_wiring"] is None
    assert sweeps[0].configuration("ad3")["ad3"]["ad3_sdk_version"] == "3.22.2"

    summary = duty.summarize(tmp_path / f"{run_file_name(sweeps[0].start)}.jsonl")
    assert summary.ad3_duty.notna().all()
    assert (summary.ad3_duty - summary.duty).abs().max() < 0.03
    assert (summary.ac_rms_v - summary.ac_expected_v).abs().max() < 1.0
    assert summary.ad3_frequency_hz.max() == pytest.approx(100.0, rel=0.02)
    device_1 = duty.summarize(tmp_path / f"{run_file_name(sweeps[1].start)}.jsonl")
    assert "ad3_duty" not in device_1
    assert device_1.ac_rms_v.notna().all()
    assert bench.mux.driver.bridge._interface.closed_channels == []


def test_duty_plan_locate_only_skips_the_sweep(duty_scripts, tmp_path):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    _run_duty_plan(duty, bench, tmp_path, devices=[1], locate_only=True)
    runs = load_runs(tmp_path, plan_name=duty.PLAN_NAME)
    assert [run.start["phase"] for run in runs] == ["locate_coarse", "locate_fine"]


def test_duty_plan_skips_the_sweep_when_nothing_switches(
    duty_scripts, tmp_path, monkeypatch
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    driver = bench.meter.driver
    real = driver.measure
    monkeypatch.setattr(
        driver,
        "measure",
        lambda function: (
            24.0
            if bench.mux.driver.selected == bench_module.DEVICES[1]["heater_relay"]
            else real(function)
        ),
    )
    _run_duty_plan(duty, bench, tmp_path, devices=[1])
    runs = load_runs(tmp_path, plan_name=duty.PLAN_NAME)
    assert [run.start["phase"] for run in runs] == [
        "locate_coarse",
        "locate_extended",
    ]


def test_duty_plan_tries_higher_totals_when_the_coarse_steps_stay_off(
    duty_scripts, tmp_path, monkeypatch
):
    bench_module, _, duty = duty_scripts
    monkeypatch.setitem(bench_module.DEVICES[1], "regulation_ohm", 34_000.0)
    bench = bench_module.open_bench(simulate=True)
    _run_duty_plan(duty, bench, tmp_path, devices=[1], locate_only=True)
    runs = load_runs(tmp_path, plan_name=duty.PLAN_NAME)
    assert [run.start["phase"] for run in runs] == [
        "locate_coarse",
        "locate_extended",
        "locate_fine",
    ]


def _fail_when(monkeypatch, bench, condition, value):
    driver = bench.meter.driver
    real = driver.measure
    monkeypatch.setattr(
        driver,
        "measure",
        lambda function: value if condition() else real(function),
    )


def test_duty_plan_disconnects_when_sense_goes_negative(
    duty_scripts, tmp_path, monkeypatch
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    bench.box.driver.set_resistance(1_234.0, settle=False)
    mux = bench.mux.driver
    _fail_when(
        monkeypatch,
        bench,
        lambda: mux.selected_in("route") is not None
        and mux.selected == bench_module.DEVICES[0]["sensor_relay"],
        -3.2,
    )
    with pytest.raises(duty.BenchFault, match="Sense read -3.2000"):
        _run_duty_plan(duty, bench, tmp_path, devices=[0])
    run = load_runs(tmp_path, plan_name=duty.PLAN_NAME)[0]
    assert run.stop["exit_status"] == "fail"
    assert bench.mux.driver.bridge._interface.closed_channels == []
    assert bench.box.driver.setpoint_ohm == 1_234.0


def test_duty_plan_never_connects_with_a_bad_idle_sense(
    duty_scripts, tmp_path, monkeypatch
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    mux = bench.mux.driver
    routed = []
    real_select = mux.select

    def select(channel, group="default"):
        if group == "route" and channel is not None:
            routed.append(channel)
        real_select(channel, group)

    monkeypatch.setattr(mux, "select", select)
    _fail_when(
        monkeypatch,
        bench,
        lambda: mux.selected == bench_module.DEVICES[0]["sensor_relay"],
        2.9,
    )
    with pytest.raises(duty.BenchFault, match="not connected"):
        _run_duty_plan(duty, bench, tmp_path, devices=[0])
    assert routed == []
    assert bench.mux.driver.bridge._interface.closed_channels == []


def test_duty_plan_never_connects_to_an_unpowered_board(
    duty_scripts, tmp_path, monkeypatch
):
    bench_module, _, duty = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    mux = bench.mux.driver
    _fail_when(
        monkeypatch,
        bench,
        lambda: mux.selected == bench_module.DEVICES[0]["heater_relay"],
        0.0,
    )
    with pytest.raises(duty.BenchFault, match="powered"):
        _run_duty_plan(duty, bench, tmp_path, devices=[0])
    assert bench.mux.driver.bridge._interface.closed_channels == []


def test_series_resistor_analysis_refuses_an_open_circuit(duty_scripts, tmp_path):
    bench_module, characterize, _ = duty_scripts
    bench = bench_module.open_bench(simulate=True)
    bench.meter.driver._interface.source = lambda function: 9.9e37
    make_run_engine(tmp_path)(
        characterize.series_resistor(
            bench.box, bench.meter, bench.mux, relay_settle_s=0, readings_per_block=2
        )
    )
    with pytest.raises(ValueError, match="open circuit"):
        characterize.analyze(next(tmp_path.glob("*.jsonl")))
