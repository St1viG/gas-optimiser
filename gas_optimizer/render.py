"""
Event renderers.

ConsoleRenderer reproduces the line-oriented output the pipeline printed before
events existed, so logs and README examples stay recognizable. JsonlRenderer
writes the wire shape from `events.to_json`, one event per line — the protocol
`--stream-json` and the web GUI share.
"""

from __future__ import annotations

import json
import sys
from typing import IO

from . import events

_BANNER = "=" * 60

_GATE_STARTED_LINES = {
    "build": "[VALIDATE] Building...",
    "harness": "[VALIDATE] Generating equivalence + gas harness...",
    "gas": "[VALIDATE] Measuring gas...",
    "hevm": "[VALIDATE] Running hevm symbolic check...",
}


def format_event(event: events.Event) -> str | None:
    """Console text for an event, or None for events with no console line."""
    if isinstance(event, events.RunStarted):
        return f"[INFO] Contract: {event.contract}"

    if isinstance(event, events.AttemptStarted):
        header = f"ATTEMPT {event.attempt}/{event.max_attempts}"
        if event.fixing:
            header += f"  (fixing {event.fixing})"
        return f"\n{_BANNER}\n{header}\n{_BANNER}"

    if isinstance(event, events.LLMRequested):
        return "[1] Requesting a candidate..."

    if isinstance(event, events.ShapeRejected):
        return f"[2] Rejected before compiling: {'; '.join(event.problems)}"

    if isinstance(event, events.ValidationStarted):
        return "[2] Validating..."

    if isinstance(event, events.GateStarted):
        if event.gate == "validate":
            return f"[VALIDATE] {event.detail}"
        if event.gate == "equivalence":
            return f"[VALIDATE] Differential fuzzing ({event.detail})..."
        return _GATE_STARTED_LINES.get(event.gate)

    if isinstance(event, events.GatePassed):
        if event.gate == "harness":
            return f"[VALIDATE] {event.detail}"
        if event.gate == "equivalence":
            return "[VALIDATE] Equivalence holds."
        if event.gate == "gas":
            return f"[VALIDATE] Gas gate passed:\n{event.detail}"
        return None

    if isinstance(event, events.Note):
        return f"[VALIDATE] {event.text}"

    if isinstance(event, events.CandidateRejected):
        failure = event.failure
        return f"[3] Rejected [{failure.get('type')}]: {failure.get('error')}"

    if isinstance(event, events.CandidateAccepted):
        return f"\n[SUCCESS] Verified on attempt {event.attempt}."

    # GateFailed precedes CandidateRejected; RunFinished is summarized by the
    # caller, which also holds the OptimizationResult.
    return None


class ConsoleRenderer:
    """EventSink printing human-readable progress lines."""

    def __init__(self, stream: IO[str] | None = None, quiet: bool = False):
        self.stream = stream if stream is not None else sys.stdout
        self.quiet = quiet

    def __call__(self, event: events.Event) -> None:
        if self.quiet:
            return
        line = format_event(event)
        if line is not None:
            print(line, file=self.stream)


class JsonlRenderer:
    """EventSink writing one JSON object per event."""

    def __init__(self, stream: IO[str] | None = None):
        self.stream = stream if stream is not None else sys.stdout

    def __call__(self, event: events.Event) -> None:
        self.stream.write(json.dumps(events.to_json(event)) + "\n")
        self.stream.flush()
