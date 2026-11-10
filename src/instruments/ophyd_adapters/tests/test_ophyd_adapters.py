import pytest
from bluesky import RunEngine
from bluesky import plans as bp
from bluesky.utils import FailedStatus
from ophyd import Component as Cpt
from ophyd.utils import ReadOnlyError
from ophyd_adapters import DriverDevice, DriverSignal, MeasurementSignal


class FakeDriver:
    def __init__(self):
        self.measurements = 0
        self.setpoint = 1.0
        self.identity = "fake,1"

    def measure(self):
        self.measurements += 1
        return 10.0 * self.measurements

    def set_setpoint(self, value):
        self.setpoint = round(value, 1)  # The instrument rounds, like a real one would.


class FakeDevice(DriverDevice):
    reading = Cpt(MeasurementSignal, getter="measure", units="ohm", kind="hinted")
    setpoint = Cpt(DriverSignal, getter="setpoint", setter="set_setpoint")
    identity = Cpt(DriverSignal, getter="identity", kind="config")
    doubled = Cpt(DriverSignal, getter=lambda driver: 2 * driver.setpoint)


@pytest.fixture
def device():
    return FakeDevice(FakeDriver(), name="fake")


def test_set_records_the_instruments_readback(device):
    device.setpoint.set(2.04).wait(timeout=5)
    assert device.setpoint.get() == 2.0
    assert device.doubled.get() == 4.0


def test_read_only_signal_rejects_writes(device):
    with pytest.raises(ReadOnlyError):
        device.identity.put("other")


def test_measures_once_per_event_and_records_configuration(device):
    documents = []
    RE = RunEngine({})
    RE(bp.count([device], num=3), lambda name, doc: documents.append((name, doc)))

    events = [doc for name, doc in documents if name == "event"]
    assert [event["data"]["fake_reading"] for event in events] == [10.0, 20.0, 30.0]
    assert device.driver.measurements == 3

    (descriptor,) = [doc for name, doc in documents if name == "descriptor"]
    assert descriptor["data_keys"]["fake_reading"]["units"] == "ohm"
    assert descriptor["configuration"]["fake"]["data"]["fake_identity"] == "fake,1"


def test_measurement_errors_fail_the_run(device):
    device.driver.measure = lambda: 1 / 0
    with pytest.raises(FailedStatus):
        RunEngine({})(bp.count([device]))
