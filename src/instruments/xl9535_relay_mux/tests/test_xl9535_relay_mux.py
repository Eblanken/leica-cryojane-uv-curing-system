import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from xl9535_relay_mux.driver import I2cBridge, RelayMux, Xl9535, channel_to_output_bytes
from xl9535_relay_mux.ophyd_device import OPEN, RelayMuxDevice, RoutedRelayMuxDevice
from xl9535_relay_mux.sim import SimulatedBridge


@pytest.fixture
def sim():
    return SimulatedBridge()


@pytest.fixture
def mux(sim):
    return RelayMux(bridge=I2cBridge(interface=sim))


@pytest.mark.parametrize(
    "channel, expected",
    [(None, (0, 0)), (0, (1, 0)), (7, (0x80, 0)), (8, (0, 1)), (15, (0, 0x80))],
)
def test_closes_only_the_requested_channel(channel, expected):
    assert channel_to_output_bytes(channel) == expected


def test_rejects_channel_off_the_board():
    with pytest.raises(ValueError):
        channel_to_output_bytes(16)


def test_opening_leaves_every_relay_open(mux, sim):
    assert sim.closed_channels == []
    assert mux.selected is None


def test_never_closes_two_relays_while_switching(sim):
    mux = RelayMux(bridge=I2cBridge(interface=sim))
    observed = []
    write = sim.write_i2c

    def record(address, length, payload):
        write(address, length, payload)
        observed.append(sim.closed_channels)

    sim.write_i2c = record
    for channel in (3, 12, 0, 15, None):
        mux.select(channel)
        assert sim.closed_channels == ([] if channel is None else [channel])
    assert all(len(closed) <= 1 for closed in observed)


def test_rejects_addresses_the_board_cannot_have(sim):
    with pytest.raises(ValueError):
        Xl9535(I2cBridge(interface=sim), address=0x40)


def test_device_opens_every_relay_when_the_plan_ends(mux, sim):
    device = RelayMuxDevice(mux, name="mux")

    @bpp.stage_decorator([device])
    @bpp.run_decorator()
    def plan():
        yield from bps.mv(device.channel, 5)
        assert sim.closed_channels == [5]
        yield from bps.trigger_and_read([device])

    RunEngine({})(plan())
    assert sim.closed_channels == []
    assert device.channel.get() == OPEN


@pytest.fixture
def grouped(sim):
    return RelayMux(
        bridge=I2cBridge(interface=sim),
        groups={"default": range(4), "route": (4, 5)},
    )


def test_groups_switch_independently(grouped, sim):
    grouped.select(1)
    grouped.select(5, "route")
    assert sim.closed_channels == [1, 5]
    grouped.select(2)
    assert sim.closed_channels == [2, 5]
    grouped.select(None, "route")
    assert sim.closed_channels == [2]
    assert grouped.selected == 2


def test_never_closes_two_relays_of_a_group(grouped, sim):
    observed = []
    write = sim.write_i2c

    def record(address, length, payload):
        write(address, length, payload)
        observed.append(set(sim.closed_channels))

    sim.write_i2c = record
    grouped.select(3)
    observed.clear()
    for relay in (4, 5, 4, None):
        grouped.select(relay, "route")
    assert not any({4, 5} <= closed for closed in observed)
    assert all(3 in closed for closed in observed)


def test_rejects_a_relay_outside_its_group(grouped):
    with pytest.raises(ValueError, match="route"):
        grouped.select(1, "route")


def test_rejects_overlapping_or_unnamed_groups(sim):
    bridge = I2cBridge(interface=sim)
    with pytest.raises(ValueError, match="one group"):
        RelayMux(bridge=bridge, groups={"default": (1, 2), "route": (2, 3)})
    with pytest.raises(ValueError, match="include"):
        RelayMux(bridge=bridge, groups={"route": (2, 3)})


def test_routed_device_opens_both_groups_when_the_plan_ends(grouped, sim):
    device = RoutedRelayMuxDevice(grouped, name="mux")

    @bpp.stage_decorator([device])
    @bpp.run_decorator()
    def plan():
        yield from bps.mv(device.route, 4, device.channel, 2)
        assert sim.closed_channels == [2, 4]
        yield from bps.trigger_and_read([device])

    RunEngine({})(plan())
    assert sim.closed_channels == []
    assert device.route.get() == OPEN
