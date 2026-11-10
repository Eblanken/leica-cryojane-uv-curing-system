"""Bluesky runs on disk: one JSON Lines file of event-model documents per run.

Each line of a run file is `[name, document]`, as suitcase-jsonl writes it. A run
holds its own metadata: the start document records what was done and why, each
stream's descriptor records units and every instrument's configuration, and the stop
document records whether it finished.
"""

from __future__ import annotations

import getpass
import json
import platform
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_TIMEZONE = "America/Los_Angeles"


def provenance() -> dict[str, Any]:
    """Metadata every run from this bench should carry."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    return {
        "operator": getpass.getuser(),
        "host": platform.node(),
        "git_commit": commit,
        "git_dirty": dirty,
        "timezone": LOCAL_TIMEZONE,
    }


def run_file_name(start: dict) -> str:
    started = datetime.fromtimestamp(start["time"])
    return f"{started:%Y%m%d-%H%M%S}_{start.get('plan_name', 'run')}_{start['uid'][:8]}"


def make_run_engine(output_dir: Path | str, md: dict | None = None):
    """A RunEngine that writes every run to `output_dir` as <time>_<plan>_<uid>.jsonl."""
    from bluesky import RunEngine
    from event_model import RunRouter
    from suitcase.jsonl import Serializer

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def factory(name, start):
        return [
            Serializer(output_dir, file_prefix=run_file_name(start), flush=True)
        ], []

    RE = RunEngine({**provenance(), **(md or {})})
    RE.subscribe(RunRouter([factory]))
    return RE


def write_documents(
    documents: Iterable[tuple[str, dict]], output_dir: Path | str
) -> Path:
    """Writes one run's documents with the same serializer the RunEngine uses."""
    from suitcase.jsonl import Serializer

    documents = iter(documents)
    name, start = next(documents)
    if name != "start":
        raise ValueError("A run must begin with its start document.")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with Serializer(output_dir, file_prefix=run_file_name(start)) as serializer:
        serializer("start", start)
        for name, document in documents:
            serializer(name, document)
    return Path(output_dir) / f"{run_file_name(start)}.jsonl"


@dataclass
class Run:
    start: dict
    stop: dict | None
    descriptors: dict[str, list[dict]] = field(default_factory=dict)
    _events: dict[str, list[dict]] = field(default_factory=dict, repr=False)

    @property
    def stream_names(self) -> list[str]:
        return list(self.descriptors)

    def data_keys(self, stream: str = "primary") -> dict[str, dict]:
        return self.descriptors[stream][0]["data_keys"]

    def configuration(self, stream: str = "primary") -> dict[str, dict]:
        """Configuration values of each device, as {device: {key: value}}."""
        return {
            device: dict(values["data"])
            for device, values in self.descriptors[stream][0]["configuration"].items()
        }

    def table(self, stream: str = "primary") -> pd.DataFrame:
        """One row per event, indexed by local wall-clock time, with seq_num as a column."""
        pages = self._events.get(stream, [])
        frame = pd.concat(
            [pd.DataFrame(page["data"]) for page in pages], ignore_index=True
        )
        times = pd.concat(
            [pd.Series(page["time"]) for page in pages], ignore_index=True
        )
        seq_num = pd.concat(
            [pd.Series(page["seq_num"]) for page in pages], ignore_index=True
        )
        timezone = self.start.get("timezone", LOCAL_TIMEZONE)
        index = pd.to_datetime(times, unit="s", utc=True).dt.tz_convert(timezone)
        frame.index = pd.DatetimeIndex(index.dt.tz_localize(None), name="time")
        frame.insert(0, "seq_num", seq_num.to_numpy())
        return frame.sort_index()


def _as_page(event: dict) -> dict:
    return {
        "data": {key: [value] for key, value in event["data"].items()},
        "time": [event["time"]],
        "seq_num": [event["seq_num"]],
    }


def read_documents(path: Path | str) -> Iterator[tuple[str, dict]]:
    with open(path, encoding="utf-8") as file:
        for line in file:
            if line.strip():
                name, document = json.loads(line)
                yield name, document


def load_run(path: Path | str) -> Run:
    run: Run | None = None
    streams: dict[str, str] = {}
    for name, document in read_documents(path):
        if name == "start":
            run = Run(start=document, stop=None)
        elif name == "descriptor":
            streams[document["uid"]] = document["name"]
            run.descriptors.setdefault(document["name"], []).append(document)
        elif name in ("event", "event_page"):
            page = _as_page(document) if name == "event" else document
            run._events.setdefault(streams[document["descriptor"]], []).append(page)
        elif name == "stop":
            run.stop = document
    if run is None:
        raise ValueError(f"{path} has no start document.")
    return run


def load_runs(directory: Path | str, plan_name: str | None = None) -> list[Run]:
    """Every run in a folder, oldest first, optionally only those of one plan."""
    runs = [load_run(path) for path in sorted(Path(directory).glob("*.jsonl"))]
    return [run for run in runs if plan_name in (None, run.start.get("plan_name"))]
