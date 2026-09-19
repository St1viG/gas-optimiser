"""
Structured progress events for the optimization pipeline.

The optimizer and validator emit these instead of printing, so every frontend
(console, JSONL stream, TUI, web GUI) is just a sink over one event stream.
Events are frozen dataclasses; `to_json` gives them a stable wire shape for
the JSONL protocol shared by `--stream-json` and the web GUI.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Callable
from typing import Any


@dataclasses.dataclass(frozen=True)
class Event:
    """Marker base class for every pipeline event."""


@dataclasses.dataclass(frozen=True)
class RunStarted(Event):
    contract: str
    max_attempts: int
    settings: dict


@dataclasses.dataclass(frozen=True)
class AttemptStarted(Event):
    attempt: int
    max_attempts: int
    fixing: str | None = None


@dataclasses.dataclass(frozen=True)
class LLMRequested(Event):
    attempt: int
    model: str


@dataclasses.dataclass(frozen=True)
class ShapeRejected(Event):
    attempt: int
    problems: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ValidationStarted(Event):
    attempt: int


@dataclasses.dataclass(frozen=True)
class GateStarted(Event):
    gate: str
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class GatePassed(Event):
    gate: str
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class GateFailed(Event):
    gate: str
    failure: dict


@dataclasses.dataclass(frozen=True)
class Note(Event):
    text: str


@dataclasses.dataclass(frozen=True)
class CandidateRejected(Event):
    attempt: int
    failure: dict


@dataclasses.dataclass(frozen=True)
class CandidateAccepted(Event):
    attempt: int
    gas: tuple  # of validator.GasEntry
    notes: tuple[str, ...]
    total_delta: int


@dataclasses.dataclass(frozen=True)
class RunFinished(Event):
    success: bool
    message: str
    attempts: int
    total_delta: int = 0
    notes: tuple[str, ...] = ()


EventSink = Callable[[Event], None]


def NULL_SINK(event: Event) -> None:  # noqa: N802 - used as a constant
    return None


def tee(*sinks: EventSink) -> EventSink:
    """One sink that forwards every event to all of `sinks`."""

    def forward(event: Event) -> None:
        for sink in sinks:
            sink(event)

    return forward


# Split camel case without breaking acronyms: LLMRequested -> llm_requested.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

SCHEMA_VERSION = 1


def kind(event: Event) -> str:
    """Wire name of an event: RunStarted -> "run_started"."""
    return _CAMEL.sub("_", type(event).__name__).lower()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data = dataclasses.asdict(value)
        # GasEntry-style objects: surface the derived numbers the frontends need.
        for prop in ("delta", "comparable", "improved"):
            if hasattr(type(value), prop):
                data[prop] = getattr(value, prop)
        return data
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def jsonable(value: Any) -> Any:
    """JSON-safe view of any event field value (GasEntry included)."""
    return _jsonable(value)


def to_json(event: Event) -> dict:
    """Stable wire shape: {"schema": 1, "event": <kind>, "t": <epoch>, ...fields}."""
    payload: dict[str, Any] = {"schema": SCHEMA_VERSION, "event": kind(event), "t": time.time()}
    for f in dataclasses.fields(event):
        payload[f.name] = _jsonable(getattr(event, f.name))
    return payload
