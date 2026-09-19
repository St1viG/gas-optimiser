"""
Terminal UI: `gas-optimize tui [input]`.

One screen — a settings panel on the left, a Log tab streaming rendered events
and a Result tab with the gas table and diff. The optimization loop runs in a
worker thread; its events are posted back to the app as Textual messages, so
the UI stays live during long forge runs.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .. import events, optimizer, render, runstore, settings, utils


class OptimizeApp(App):
    TITLE = "gas-optimizer"
    BINDINGS = [("q", "quit", "Quit")]

    CSS = """
    #sidebar { width: 34; padding: 1; border-right: solid $accent 30%; }
    #sidebar Label { margin-top: 1; color: $text-muted; }
    #sidebar Button { margin-top: 1; width: 100%; }
    #status { margin-top: 1; color: $accent; }
    #hevm-row { height: 3; align-vertical: middle; }
    #hevm-row Label { margin: 1 0 0 1; }
    #gas { height: auto; max-height: 40%; }
    #diff { padding: 1; }
    """

    class Progress(Message):
        def __init__(self, event: events.Event) -> None:
            self.event = event
            super().__init__()

    class Finished(Message):
        def __init__(self, result: optimizer.OptimizationResult, error: str | None) -> None:
            self.result = result
            self.error = error
            super().__init__()

    def __init__(self, input_path: Path | None = None):
        super().__init__()
        self._input_path = input_path
        self._source = ""
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Contract path")
                yield Input(str(self._input_path or ""), id="path")
                yield Label("Fuzz runs (blank = config)")
                yield Input("", id="fuzz")
                yield Label("Max retries (blank = config)")
                yield Input("", id="retries")
                with Horizontal(id="hevm-row"):
                    yield Switch(id="hevm")
                    yield Label("hevm symbolic check")
                yield Button("Optimize", variant="primary", id="go")
                yield Static("", id="status")
            with TabbedContent(initial="log-tab"):
                with TabPane("Log", id="log-tab"):
                    yield RichLog(id="logview", wrap=True, markup=False)
                with TabPane("Result", id="result-tab"):
                    yield DataTable(id="gas")
                    yield Static("", id="diff")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#gas", DataTable)
        table.add_columns("function", "original", "candidate", "delta")

    @on(Button.Pressed, "#go")
    def start_run(self) -> None:
        if self._running:
            self.notify("a run is already in progress", severity="warning")
            return

        path = Path(self.query_one("#path", Input).value.strip())
        if not path.is_file():
            self.notify(f"not a file: {path}", severity="error")
            return
        self._source = path.read_text(encoding="utf-8")
        if not self._source.strip():
            self.notify(f"empty file: {path}", severity="error")
            return

        overrides: dict = {}
        fuzz = self.query_one("#fuzz", Input).value.strip()
        if fuzz.isdigit():
            overrides["fuzz_runs"] = int(fuzz)
        retries = self.query_one("#retries", Input).value.strip()
        if retries.isdigit():
            overrides["max_retries"] = int(retries)
        if self.query_one("#hevm", Switch).value:
            overrides["hevm_enabled"] = True

        self._running = True
        self.query_one("#go", Button).disabled = True
        self.query_one("#logview", RichLog).clear()
        self.query_one("#status", Static).update("running...")
        self._optimize(path, overrides)

    @work(thread=True, exclusive=True)
    def _optimize(self, path: Path, overrides: dict) -> None:
        def sink(event: events.Event) -> None:
            self.post_message(self.Progress(event))

        error: str | None = None
        result = optimizer.OptimizationResult(False, "did not run")
        try:
            resolved = settings.resolve(overrides)
            contract = utils.extract_contract_name(self._source) or "unknown"
            recorder = runstore.RunStore().start(
                self._source, contract, filename=path.name, options=overrides
            )
            result = optimizer.run_optimization_loop(
                self._source,
                verbose=False,
                on_event=events.tee(sink, recorder.sink),
                settings=resolved.validator,
                max_retries=resolved.llm["max_retries"],
                model=resolved.llm["model"],
            )
            recorder.finish(result)
        except Exception as exc:  # surfaced in the UI, never a dead worker
            error = str(exc)
        self.post_message(self.Finished(result, error))

    def on_optimize_app_progress(self, message: Progress) -> None:
        line = render.format_event(message.event)
        if line is not None:
            self.query_one("#logview", RichLog).write(line)

        event = message.event
        status = self.query_one("#status", Static)
        if isinstance(event, events.AttemptStarted):
            status.update(f"attempt {event.attempt}/{event.max_attempts}")
        elif isinstance(event, events.GateStarted):
            status.update(f"gate: {event.gate}")

    def on_optimize_app_finished(self, message: Finished) -> None:
        self._running = False
        self.query_one("#go", Button).disabled = False
        status = self.query_one("#status", Static)

        if message.error is not None:
            status.update("error")
            self.notify(message.error, severity="error", timeout=10)
            return

        result = message.result
        if not result.success:
            status.update("rejected")
            self.query_one("#logview", RichLog).write(f"\nFAILED: {result.message}")
            return

        status.update(f"accepted ({result.total_delta:+d} gas)")
        table = self.query_one("#gas", DataTable)
        table.clear()
        for entry in result.gas:
            delta = f"{entry.delta:+d}" if entry.comparable else "n/a"
            table.add_row(entry.signature, str(entry.original), str(entry.candidate), delta)

        diff = "".join(
            difflib.unified_diff(
                self._source.splitlines(keepends=True),
                (result.optimized_code or "").splitlines(keepends=True),
                fromfile="original",
                tofile="optimized",
            )
        )
        self.query_one("#diff", Static).update(diff or "(no diff)")
        self.query_one(TabbedContent).active = "result-tab"


def main(input_path: Path | None = None) -> int:
    OptimizeApp(input_path).run()
    return 0
