import time

import pytest
from bluesky import RunEngine
from bluesky import plans as bp
from qr101b.driver import Qr101b, parse_calibration_info, parse_output_state
from qr101b.ophyd_device import Qr101bDevice
from qr101b.sim import SimulatedQr101b


@pytest.fixture
def sim():
    return SimulatedQr101b()


@pytest.fixture
def box(sim):
    return Qr101b(transport=sim, min_change_interval_s=0.0)


def test_parses_manual_example_status_block():
    state = parse_output_state(
        [
            "SP(R)=2.000",
            "PV(R)=2.009",
            "UMax(V)=1.5",
            "RLimit(R)=0.000",
            "InnerT(C)=27.68",
        ]
    )
    assert state.setpoint_ohm == 2.0
    assert state.calculated_ohm == 2.009
    assert state.internal_temperature_c == 27.68


def test_rejects_incomplete_status_block():
    with pytest.raises(ValueError, match="PV"):
        parse_output_state(["SP(R)=2.000"])


def test_parses_calibration_info():
    info = parse_calibration_info(["+UCAL.INFO:", "USEN =0", "DATE=20221025"])
    assert info == {"USEN": "0", "DATE": "20221025"}


def test_rejects_other_devices():
    with pytest.raises(ConnectionError):
        Qr101b(transport=SimulatedQr101b(device_type="CH340 GPS"))


def test_sets_steps_and_limits_the_output(box, sim):
    assert box.set_resistance(10_000.0, settle=False).setpoint_ohm == 10_000.0
    assert sim.commands[-1] == "AT+USER.SP=10000.0000"
    assert box.step_resistance(-500.0, settle=False).setpoint_ohm == 9_500.0
    box.set_limit(2_000.0)
    with pytest.raises(ValueError, match="limit"):
        box.set_resistance(1_000.0)


def test_enforces_the_change_interval(sim):
    box = Qr101b(transport=sim, min_change_interval_s=0.2)
    start = time.monotonic()
    box.set_resistance(100.0, settle=False)
    box.set_resistance(200.0, settle=False)
    assert time.monotonic() - start >= 0.2


def test_device_scan_records_box_identity(box):
    device = Qr101bDevice(box, name="qr101b")
    documents = []
    RunEngine({})(
        bp.list_scan([device], device.setpoint, [5_000.0, 6_000.0]),
        lambda name, doc: documents.append((name, doc)),
    )
    events = [doc for name, doc in documents if name == "event"]
    assert [event["data"]["qr101b_setpoint"] for event in events] == [5_000.0, 6_000.0]
    (descriptor,) = [doc for name, doc in documents if name == "descriptor"]
    assert (
        descriptor["configuration"]["qr101b"]["data"]["qr101b_serial_number"]
        == "00000127"
    )
