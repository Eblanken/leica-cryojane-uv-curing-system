"""Readers for the data logs the bench produces."""

from __future__ import annotations

import gzip
import json
import re
import struct
import tarfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from cryojane_bench import hobo

_RESULT_LINE = re.compile(
    r"^(?P<timestamp>\d+-\d+-\d+ \d+:\d+:[\d.]+): (?P<channel>.+?): "
    r"(?P<value>[-+\d.eE]+) \(QuantityType\.(?P<quantity>[A-Z]+)\)\s*$"
)


def format_result_line(timestamp, channel: str, value: float, quantity: str) -> str:
    return f"{timestamp}: {channel}: {value} (QuantityType.{quantity})\r\n"


def read_results_log(path: Path | str) -> pd.DataFrame:
    """Reads a mux sweep log into a table with timestamp, channel, value and quantity."""
    rows = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            match = _RESULT_LINE.match(line)
            if match:
                rows.append(match.groupdict())
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="ISO8601")
    frame["value"] = frame["value"].astype(float)
    return frame


def read_channel(results: pd.DataFrame, channel: str, max_value: float = 1e30) -> pd.Series:
    """One channel of a results log, indexed by time, without meter overload readings."""
    rows = results[(results.channel == channel) & (results.value < max_value)]
    return rows.set_index("timestamp").value.sort_index()


def read_hobo_temperature(path: Path | str) -> pd.Series:
    """Temperature from a .hobo file, indexed by the logger's local wall-clock time.

    Sweep logs are in local time too, so the two line up directly.
    """
    log = hobo.read(path)
    times = pd.DatetimeIndex([sample.time.replace(tzinfo=None) for sample in log.samples])
    return pd.Series([sample.values["temp"] for sample in log.samples], index=times, name="temperature_c")


def read_picolog(path: Path | str, timezone: str | None = None) -> pd.DataFrame:
    """Reads a PicoLog 6 .picolog capture at its finest resolution (1 s).

    The file is a tar holding gzipped big-endian float32 blocks, indexed by
    data-map.json.gz as "<channel key>/<sample period, s>/<block start, ms>".

    Returns a table indexed by local time with one column per enabled channel,
    named "ch<N>" plus the description when the user has edited it.
    """
    with tarfile.open(path) as archive:
        metadata = json.load(archive.extractfile("metadata.json"))
        data_map = json.loads(gzip.decompress(archive.extractfile("data-map.json.gz").read()))
        names = {entry.name for entry in archive.getmembers()}
        blobs = {
            name: gzip.decompress(archive.extractfile(name).read())
            for name in names
            if name.endswith(".gz") and name != "data-map.json.gz"
        }

    blocks: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
    for key, (blob_name, offset, length) in data_map:
        channel_key, period, start_ms = key.rsplit("/", 2)
        if period == "1":
            raw = blobs[blob_name][offset : offset + length]
            blocks[channel_key.rsplit(".", 1)[1]][int(start_ms)] = np.array(
                struct.unpack(f">{length // 4}f", raw)
            )

    labels = {}
    for device in metadata["devices"]["items"]:
        for channel in device.values():
            if isinstance(channel, dict) and channel.get("enabled"):
                identifier = channel["channelIdentifier"]
                description = channel.get("description", "")
                default = f"Channel {identifier[2:]}"
                labels[identifier] = identifier if description == default else f"{identifier} {description}"

    series = {}
    for channel, channel_blocks in blocks.items():
        pieces = []
        for start_ms, values in sorted(channel_blocks.items()):
            times = start_ms + np.arange(len(values)) * 1000
            pieces.append(pd.Series(values, index=pd.to_datetime(times, unit="ms", utc=True)))
        series[labels.get(channel, channel)] = pd.concat(pieces).dropna()

    frame = pd.DataFrame(series).sort_index()
    frame.index = frame.index.tz_convert(timezone or metadata.get("timezone", "UTC")).tz_localize(None)
    return frame
