"""Converts the October 2026 passive sweep into a Bluesky run, next to the live runs.

The source files in data/ are left as they are. The run gets the same streams and data
keys that acquisition.py records, so the same analysis reads both:
  - primary: one event per GDM-8341 reading, with gdm_reading, mux_channel and
    channel_name.
  - chamber_reference: the HOBO logger's temperature.

The legacy log did not record the instruments' identities or settings. The meter's
model and serial number come from the operator, who confirmed it is the meter used for
the validation runs, and are marked as such. Its firmware and settings are absent from
the run. The log's timestamps are local wall-clock times, taken as US Pacific.

    pixi run python scripts/sensor_characterization/convert_legacy_data.py
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import event_model
import numpy as np
import pandas as pd
from acquisition import CHANNELS, PLAN_NAME, RUNS_DIR
from cryojane_bench.logs import read_results_log
from cryojane_bench.runs import LOCAL_TIMEZONE, REPO_ROOT, provenance, write_documents

from cryojane_bench import hobo

HERE = Path(__file__).parent
SWEEP_LOG = HERE / "data" / "results_20261025-215246.txt"
HOBO_FILE = HERE / "data" / "Chamber-Right 2026-10-26 13_53_30 PDT.hobo"
PAGE_SIZE = 5_000
# Fixed so that converting again replaces this run instead of creating another.
RUN_UID = "ec0ee2da-1f9a-474d-a19f-dff558f6c7f3"
GDM_MODEL = "GDM8341"
GDM_SERIAL_NUMBER = "GES811201"
_FUNCTIONS = {"RESISTANCE": ("RESISTANCE", "ohm"), "VOLTAGE": ("VOLTAGE_DC", "V")}


def _source(path: Path) -> dict:
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _local_epoch(times: pd.Series) -> np.ndarray:
    aware = pd.DatetimeIndex(times).tz_localize(LOCAL_TIMEZONE, ambiguous="raise")
    return aware.as_unit("ns").asi8 / 1e9


def _pages(descriptor, data: dict[str, np.ndarray], times: np.ndarray):
    for start in range(0, len(times), PAGE_SIZE):
        chunk = slice(start, start + PAGE_SIZE)
        page_times = times[chunk].tolist()
        yield "event_page", descriptor.compose_event_page(
            data={key: values[chunk].tolist() for key, values in data.items()},
            timestamps={key: page_times for key in data},
            time=page_times,
            seq_num=list(range(start + 1, start + 1 + len(page_times))),
        )


def legacy_documents(sweep_log: Path, hobo_file: Path):
    results = read_results_log(sweep_log)
    (quantity,) = results.quantity.unique()
    function, units = _FUNCTIONS[quantity]
    relays = {name: index for index, name in CHANNELS.items()}
    if unknown := set(results.channel) - set(relays):
        raise ValueError(f"Channels without a relay index: {unknown}")
    times = _local_epoch(results.timestamp)
    logger = hobo.read(hobo_file)

    run = event_model.compose_run(
        uid=RUN_UID,
        time=float(times[0]),
        metadata={
            **provenance(),
            "plan_name": PLAN_NAME,
            "experiment": "sensor_characterization",
            "description": "Passive resistance sweep, both machines in the chamber, 25-26 Oct 2026.",
            "channels": {str(index): name for index, name in CHANNELS.items()},
            "function": function,
            "converted_from": [_source(sweep_log), _source(hobo_file)],
            "converted_at": datetime.now().isoformat(timespec="seconds"),
            "converted_with": Path(__file__).relative_to(REPO_ROOT).as_posix(),
            "note": (
                "Converted from the legacy text log, which recorded no instrument "
                "identities, ranges or settings. In the gdm configuration, the function "
                "comes from the log's QuantityType. The model and serial number were "
                "stated by the operator, not read from the meter during this run."
            ),
            "hints": {"dimensions": [(["time"], "primary")]},
        },
    )
    yield "start", run.start_doc

    gdm_source = "GW Instek GDM-8341, legacy log"
    primary = run.compose_descriptor(
        name="primary",
        data_keys={
            "gdm_reading": {
                "dtype": "number",
                "shape": [],
                "source": gdm_source,
                "units": units,
            },
            "mux_channel": {
                "dtype": "integer",
                "shape": [],
                "source": "XL9535 relay mux",
            },
            "channel_name": {
                "dtype": "string",
                "shape": [],
                "source": "acquisition plan",
            },
        },
        configuration={
            "gdm": {
                "data": {
                    "gdm_function": function,
                    "gdm_model": GDM_MODEL,
                    "gdm_serial_number": GDM_SERIAL_NUMBER,
                },
                "timestamps": dict.fromkeys(
                    ["gdm_function", "gdm_model", "gdm_serial_number"], float(times[0])
                ),
                "data_keys": {
                    "gdm_function": {
                        "dtype": "string",
                        "shape": [],
                        "source": gdm_source,
                    },
                    "gdm_model": {
                        "dtype": "string",
                        "shape": [],
                        "source": "stated by operator",
                    },
                    "gdm_serial_number": {
                        "dtype": "string",
                        "shape": [],
                        "source": "stated by operator",
                    },
                },
            }
        },
        object_keys={
            "gdm": ["gdm_reading"],
            "mux": ["mux_channel"],
            "channel_name": ["channel_name"],
        },
        hints={"gdm": {"fields": ["gdm_reading"]}},
        time=float(times[0]),
    )
    yield "descriptor", primary.descriptor_doc
    yield from _pages(
        primary,
        {
            "gdm_reading": results.value.to_numpy(),
            "mux_channel": results.channel.map(relays).to_numpy(),
            "channel_name": results.channel.to_numpy(),
        },
        times,
    )

    hobo_source = f"HOBO {logger.model} S/N {logger.serial}"
    reference = run.compose_descriptor(
        name="chamber_reference",
        data_keys={
            "hobo_temperature": {
                "dtype": "number",
                "shape": [],
                "source": hobo_source,
                "units": "degC",
            }
        },
        configuration={
            "hobo": {
                "data": {
                    "hobo_model": logger.model,
                    "hobo_serial": logger.serial,
                    "hobo_interval_s": float(logger.interval),
                    "hobo_launch_time": logger.launch_time.isoformat(),
                },
                "timestamps": dict.fromkeys(
                    [
                        "hobo_model",
                        "hobo_serial",
                        "hobo_interval_s",
                        "hobo_launch_time",
                    ],
                    logger.launch_time.timestamp(),
                ),
                "data_keys": {
                    "hobo_model": {
                        "dtype": "string",
                        "shape": [],
                        "source": hobo_source,
                    },
                    "hobo_serial": {
                        "dtype": "string",
                        "shape": [],
                        "source": hobo_source,
                    },
                    "hobo_interval_s": {
                        "dtype": "number",
                        "shape": [],
                        "source": hobo_source,
                        "units": "s",
                    },
                    "hobo_launch_time": {
                        "dtype": "string",
                        "shape": [],
                        "source": hobo_source,
                    },
                },
            }
        },
        object_keys={"hobo": ["hobo_temperature"]},
        time=float(times[0]),
    )
    yield "descriptor", reference.descriptor_doc
    samples = [sample for sample in logger.samples if "temp" in sample.values]
    yield from _pages(
        reference,
        {"hobo_temperature": np.array([sample.values["temp"] for sample in samples])},
        np.array([sample.time.timestamp() for sample in samples]),
    )

    yield "stop", run.compose_stop(
        exit_status="success",
        reason="Converted from a legacy log.",
        time=float(times[-1]),
    )


def main() -> None:
    path = write_documents(legacy_documents(SWEEP_LOG, HOBO_FILE), RUNS_DIR)
    print(f"Wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
