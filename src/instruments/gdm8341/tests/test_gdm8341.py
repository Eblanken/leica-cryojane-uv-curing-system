import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky import plans as bp
from gdm8341.driver import Function, Gdm8341, Rate, parse_error, parse_identity
from gdm8341.ophyd_device import Gdm8341Device
from gdm8341.sim import SimulatedGdm8341


@pytest.fixture
def sim():
    return SimulatedGdm8341(
        lambda function: 12_345.0 if function is Function.RESISTANCE else 1.5
    )


@pytest.fixture
def meter(sim):
    return Gdm8341(transport=sim)


def test_parses_manual_example_identity():
    identity = parse_identity("GWInstek,GDM8342,00000000,1.0")
    assert identity.model == "GDM8342"
    assert identity.version == "1.0"


def test_parses_error_queue_entries():
    assert parse_error('+0,"No error"').code == 0
    error = parse_error('-113,"Undefined header"')
    assert (error.code, error.message) == (-113, "Undefined header")


def test_measures_with_the_manual_command(meter, sim):
    assert meter.read_resistance() == 12_345.0
    assert sim.commands[-1] == "MEAS:RES?"
    assert meter.measure(Function.VOLTAGE_DC, 5) == 1.5
    assert sim.commands[-1] == "MEAS:VOLT:DC? 5"


def test_configure_and_query_round_trip(meter):
    meter.configure(Function.RESISTANCE, 50e3)
    assert meter.function() is Function.RESISTANCE
    assert meter.range() == 50e3
    assert not meter.auto_range()
    meter.rate = Rate.FAST
    assert meter.rate is Rate.FAST
    assert meter.errors() == []


def test_rejects_functions_display_2_lacks(meter):
    with pytest.raises(ValueError):
        meter.configure(Function.CAPACITANCE, display=2)


def test_reports_rejected_commands(meter):
    meter.write("NOT:A:COMMAND")
    assert [error.code for error in meter.errors()] == [-113]


def test_device_records_reading_units_and_configuration(meter):
    device = Gdm8341Device(meter, name="gdm")
    documents = []

    def plan():
        yield from bps.mv(device.function, "RESISTANCE")
        yield from bp.count([device], num=2)

    RunEngine({})(plan(), lambda name, doc: documents.append((name, doc)))
    (descriptor,) = [doc for name, doc in documents if name == "descriptor"]
    events = [doc for name, doc in documents if name == "event"]
    assert descriptor["data_keys"]["gdm_reading"]["units"] == "ohm"
    configuration = descriptor["configuration"]["gdm"]["data"]
    assert configuration["gdm_function"] == "RESISTANCE"
    assert configuration["gdm_model"] == "GDM8341"
    assert [event["data"]["gdm_reading"] for event in events] == [12_345.0, 12_345.0]
    assert events[0]["data"]["gdm_range"] == 50e3


def test_a_missed_reply_is_retried_and_a_late_one_cannot_answer_the_next_command(sim):
    meter = Gdm8341(transport=sim)
    meter.configure(Function.VOLTAGE_DC)
    real = sim.readline
    state = {"missed": False}

    def miss_once():
        if not state["missed"]:
            state["missed"] = True
            return b""  # The reply stays queued, as a late one would.
        return real()

    sim.readline = miss_once
    sim.source = lambda function: 1.5
    assert meter.measure_active(retry_wait_s=0.0) == 1.5
    assert state["missed"]
    sim.source = lambda function: 2.5
    assert meter.measure_active(retry_wait_s=0.0) == 2.5


def test_a_meter_that_stays_silent_still_raises(sim):
    meter = Gdm8341(transport=sim)
    sim.readline = lambda: b""
    with pytest.raises(TimeoutError):
        meter.measure_active(retries=1, retry_wait_s=0.0)
