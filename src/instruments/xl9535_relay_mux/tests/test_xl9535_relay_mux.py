import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from xl9535_relay_mux.driver import (
    I2cBridge,
    RelayMux,
    RelayMuxError,
    SafeState,
    SafeStateError,
    WatchdogTripped,
    Xl9535,
    channel_to_output_bytes,
)
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


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def watched(clock):
    sim = SimulatedBridge(clock=clock)
    return sim, RelayMux(
        bridge=I2cBridge(interface=sim), groups={"default": range(4), "route": (4, 5)}
    )


def test_a_quiet_host_trips_the_watchdog_into_the_safe_state(watched, clock):
    sim, mux = watched
    mux.bridge.watchdog_arm(1.0)
    mux.select(2)
    mux.select(4, "route")
    assert sim.closed_channels == [2, 4]
    clock.now += 1.5
    with pytest.raises(WatchdogTripped):
        mux.select(3)
    assert sim.closed_channels == []
    status = mux.bridge.watchdog_status()
    assert (status.armed, status.tripped, status.trip_count) == (False, True, 1)


def test_calls_within_the_timeout_keep_it_from_tripping(watched, clock):
    _sim, mux = watched
    mux.bridge.watchdog_arm(1.0)
    for _ in range(5):
        clock.now += 0.9
        mux.bridge.watchdog_kick()
    assert not mux.bridge.watchdog_status().tripped


def test_recovering_from_a_trip_opens_everything_and_rearms(clock):
    sim = SimulatedBridge(clock=clock)
    mux = RelayMux(bridge=I2cBridge(interface=sim), watchdog_s=1.0)
    try:
        mux.select(1)
        mux._feeder.stop()  # Stands in for a host that stops feeding.
        clock.now += 2.0
        with pytest.raises(WatchdogTripped):
            mux.select(2)
        mux.recover_from_trip()
        assert mux.selected is None
        assert mux.bridge.watchdog_status().armed
        mux.select(2)
        assert sim.closed_channels == [2]
    finally:
        mux.close()
    assert not sim.armed and sim.closed_channels == []


def test_the_feeder_kicks_in_the_background():
    import time

    sim = SimulatedBridge()
    mux = RelayMux(bridge=I2cBridge(interface=sim), watchdog_s=0.2)
    time.sleep(0.5)
    mux.close()
    assert sim.kicks >= 3
    assert not sim.tripped


def test_opening_clears_a_trip_left_by_an_earlier_host(clock):
    sim = SimulatedBridge(clock=clock)
    sim.watchdog_arm(1000)
    clock.now += 2.0
    sim.watchdog_kick()
    assert sim.tripped
    with pytest.warns(UserWarning, match="tripped"):
        mux = RelayMux(bridge=I2cBridge(interface=sim), watchdog_s=5.0)
    mux.close()


def test_a_watchdog_needs_firmware_2():
    sim = SimulatedBridge(watchdog=False)
    bridge = I2cBridge(interface=sim)
    assert bridge.firmware_version() is None
    RelayMux(bridge=bridge).select(3)  # Firmware 1 still switches.
    with pytest.raises(RelayMuxError, match="no watchdog"):
        RelayMux(bridge=bridge, watchdog_s=5.0)


def test_unacknowledged_writes_raise(sim):
    with pytest.raises(RelayMuxError, match="not acknowledged"):
        RelayMux(bridge=I2cBridge(interface=sim), target_address=0x21)


def test_safe_state_needs_confirmation_and_is_read_back(watched):
    sim, mux = watched
    with pytest.raises(SafeStateError, match="confirm=True"):
        mux.program_safe_state([1, 5])
    assert sim.safe == (0x20, 0, 0)
    stored = mux.program_safe_state([1, 5], confirm=True)
    assert stored == SafeState(0x20, 0x22, 0x00)
    assert stored.closed_relays == [1, 5]
    sim.reboot()
    assert sim.closed_channels == [1, 5]


def test_safe_state_keeps_one_relay_per_group(watched):
    _, mux = watched
    with pytest.raises(ValueError, match="route"):
        mux.program_safe_state([4, 5], confirm=True)
    with pytest.raises(ValueError, match="no group"):
        mux.program_safe_state([9], confirm=True)


def test_a_wrong_token_stores_nothing(watched):
    sim, mux = watched
    token = mux.bridge.stage_safe_state(SafeState(0x20, 0x01, 0x00))
    with pytest.raises(SafeStateError, match="token"):
        mux.bridge.commit_safe_state(token ^ 1)
    assert sim.safe == (0x20, 0, 0)
    with pytest.raises(SafeStateError, match="token"):
        mux.bridge.commit_safe_state(token)  # Spent by the failed attempt.
