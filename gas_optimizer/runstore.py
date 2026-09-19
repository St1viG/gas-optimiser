"""
Persistent run history.

Every optimization run gets a directory under runs/:

    runs/<YYYYMMDD-HHMMSS>-<4hex>/
        run.json        outcome summary (written when the run finishes)
        events.jsonl    the full event stream, replayable by any frontend
        original.sol    the input as submitted
        optimized.sol   the accepted candidate (success only)

This is what makes accepted output survive the next run's workspace wipe, and
it is the storage behind the web GUI's sidebar history.
"""

from __future__ import annotations

import builtins
import difflib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, events
from .optimizer import OptimizationResult

RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"
ORIGINAL_FILE = "original.sol"
OPTIMIZED_FILE = "optimized.sol"


def _unified_diff(original: str, optimized: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            optimized.splitlines(keepends=True),
            fromfile="original.sol",
            tofile="optimized.sol",
        )
    )


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    started: float
    contract: str
    status: str  # "accepted" | "rejected" | "incomplete"
    attempts: int
    total_delta: int

    @classmethod
    def from_record(cls, record: dict) -> RunSummary:
        return cls(
            run_id=record["id"],
            started=record.get("started", 0.0),
            contract=record.get("contract", "?"),
            status="accepted" if record.get("success") else "rejected",
            attempts=record.get("attempts", 0),
            total_delta=record.get("total_delta", 0),
        )


class RunRecorder:
    """Sink + finisher for one run in progress."""

    def __init__(
        self, directory: Path, source: str, contract: str, filename: str | None, options: dict
    ):
        self.directory = directory
        self.run_id = directory.name
        self.started = time.time()
        self._source = source
        self._contract = contract
        self._filename = filename
        self._options = options

        directory.mkdir(parents=True, exist_ok=True)
        (directory / ORIGINAL_FILE).write_text(source, encoding="utf-8")
        self._events_path = directory / EVENTS_FILE
        self._events_path.touch()

    def sink(self, event: events.Event) -> None:
        with self._events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(events.to_json(event)) + "\n")

    def finish(self, result: OptimizationResult) -> dict:
        """Write run.json; returns the record."""
        record = {
            "id": self.run_id,
            "started": self.started,
            "finished": time.time(),
            "contract": self._contract,
            "filename": self._filename,
            "options": self._options,
            "success": result.success,
            "message": result.message,
            "attempts": result.attempts,
            "total_delta": result.total_delta,
            "gas": [events.jsonable(entry) for entry in result.gas],
            "notes": list(result.notes),
            "output_path": str(result.output_path) if result.output_path else None,
            "diff": "",
        }
        if result.success and result.optimized_code:
            (self.directory / OPTIMIZED_FILE).write_text(result.optimized_code, encoding="utf-8")
            record["diff"] = _unified_diff(self._source, result.optimized_code)

        (self.directory / RUN_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record


class RunStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else config.RUNS_DIR

    def start(
        self, source: str, contract: str, filename: str | None = None, options: dict | None = None
    ) -> RunRecorder:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        directory = self.root / f"{stamp}-{secrets.token_hex(2)}"
        return RunRecorder(directory, source, contract, filename, options or {})

    def list(self) -> builtins.list[RunSummary]:
        """Newest first. Directories without run.json show as incomplete."""
        if not self.root.is_dir():
            return []
        summaries: list[RunSummary] = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            record_path = directory / RUN_FILE
            if record_path.is_file():
                try:
                    summaries.append(RunSummary.from_record(json.loads(record_path.read_text())))
                    continue
                except (json.JSONDecodeError, KeyError):
                    pass
            summaries.append(
                RunSummary(
                    run_id=directory.name,
                    started=0.0,
                    contract="?",
                    status="incomplete",
                    attempts=0,
                    total_delta=0,
                )
            )
        return summaries

    def load(self, run_id: str) -> dict | None:
        """Full record for one run, with the event stream and sources attached."""
        directory = self.root / run_id
        record_path = directory / RUN_FILE
        if not record_path.is_file():
            return None
        try:
            record = json.loads(record_path.read_text())
        except json.JSONDecodeError:
            return None
        record["events"] = self.load_events(run_id)
        for key, name in (("original_source", ORIGINAL_FILE), ("optimized_code", OPTIMIZED_FILE)):
            path = directory / name
            record[key] = path.read_text(encoding="utf-8") if path.is_file() else None
        return record

    def load_events(self, run_id: str) -> builtins.list[dict]:
        path = self.root / run_id / EVENTS_FILE
        if not path.is_file():
            return []
        parsed: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return parsed
