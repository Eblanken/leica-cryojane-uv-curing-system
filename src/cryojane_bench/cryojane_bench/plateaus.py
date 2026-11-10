"""Find the settled stretches of a stepped temperature run.

A plateau is a stretch where both the reference temperature and the device under
test have stopped moving. Devices lag the air, so checking only the reference
would keep readings from before the device caught up.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

STABLE_WINDOW = pd.Timedelta(minutes=10)  # Readings are averaged over this long...
STABLE_DRIFT_C = 0.05  # ...and the average may move this much from one window earlier.
PLATEAU_SPLIT_C = 0.25  # A larger reference change starts a new plateau.
PLATEAU_MIN_DURATION = pd.Timedelta(minutes=20)  # Shorter settled runs are transients.


def _to_ns(times) -> np.ndarray:
    return np.asarray(times).astype("datetime64[ns]").astype("int64")


def settled(series: pd.Series) -> np.ndarray:
    """True where the running mean is within STABLE_DRIFT_C of its value one window earlier."""
    running_mean = series.rolling(STABLE_WINDOW).mean().to_numpy()
    times = _to_ns(series.index)
    previous_mean = np.interp(times - STABLE_WINDOW.value, times, running_mean)
    old_enough = times - times[0] >= 2 * STABLE_WINDOW.value
    return old_enough & (np.abs(running_mean - previous_mean) <= STABLE_DRIFT_C)


def find_plateaus(
    readings: pd.Series,
    reference_c: pd.Series,
    device_to_approximate_c: Callable[[np.ndarray], np.ndarray],
) -> pd.DataFrame:
    """Returns one row per plateau, sorted by reference temperature.

    Args:
        readings: Device values indexed by time.
        reference_c: Reference temperature indexed by time.
        device_to_approximate_c: Rough conversion from device value to temperature.
            It only sets the scale of the device's settling check, so a nominal curve is enough.
    """
    readings = readings.loc[reference_c.index[0] : reference_c.index[-1]]
    reading_times, reference_times = _to_ns(readings.index), _to_ns(reference_c.index)
    reference_stable = settled(reference_c).astype(float)
    frame = pd.DataFrame({
        "value": readings.to_numpy(),
        "reference_c": np.interp(reading_times, reference_times, reference_c.to_numpy()),
        "stable": np.interp(reading_times, reference_times, reference_stable) >= 1.0,
    }, index=readings.index)

    device_c = pd.Series(device_to_approximate_c(frame.value.to_numpy()), index=frame.index)
    frame = frame[frame.stable.to_numpy() & settled(device_c)]

    new_plateau = (frame.reference_c.diff().abs() > PLATEAU_SPLIT_C) | (frame.index.to_series().diff() > STABLE_WINDOW)
    plateaus = frame.assign(start=frame.index, end=frame.index).groupby(new_plateau.cumsum().to_numpy()).agg(
        reference_c=("reference_c", "mean"),
        reference_std_c=("reference_c", "std"),
        value=("value", "median"),  # Median: some channels have brief contact spikes.
        value_std=("value", "std"),
        samples=("value", "size"),
        start=("start", "min"),
        end=("end", "max"),
    )
    plateaus = plateaus[plateaus.end - plateaus.start >= PLATEAU_MIN_DURATION]
    return plateaus.sort_values("reference_c").reset_index(drop=True)
