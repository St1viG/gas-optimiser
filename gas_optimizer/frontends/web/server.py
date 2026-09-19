"""
Local web GUI backend: `gas-optimize gui`.

FastAPI + Server-Sent Events. One run at a time — foundry/src and foundry/test
are a single shared workspace, so RunManager is single-flight and a concurrent
start returns 409. The event stream endpoint replays what already happened and
then live-tails, so a mid-run page refresh reconnects cleanly.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from importlib import resources

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ... import events, optimizer, preflight, runstore, settings, utils


class RunOptions(BaseModel):
    fuzz_runs: int | None = None
    max_retries: int | None = None
    hevm: bool | None = None
    model: str | None = None

    def overrides(self) -> dict:
        raw = {
            "fuzz_runs": self.fuzz_runs,
            "max_retries": self.max_retries,
            "hevm_enabled": self.hevm,
            "model": self.model,
        }
        return {key: value for key, value in raw.items() if value is not None}


class StartRun(BaseModel):
    source: str
    filename: str | None = None
    options: RunOptions = RunOptions()


class Cancelled(Exception):
    """Raised inside the event sink to unwind a cancelled run."""


class ActiveRun:
    """In-memory event buffer for the run in progress, for SSE live-tailing."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict] = []
        self.done = False
        self.cancel_requested = False
        self.condition = threading.Condition()

    def append(self, payload: dict) -> None:
        with self.condition:
            self.events.append(payload)
            self.condition.notify_all()

    def finish(self) -> None:
        with self.condition:
            self.done = True
            self.condition.notify_all()


class RunManager:
    """Single-flight: the foundry workspace cannot host two runs at once."""

    def __init__(self, store: runstore.RunStore):
        self.store = store
        self._lock = threading.Lock()
        self.active: ActiveRun | None = None

    def start(self, request: StartRun) -> str:
        if not self._lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a run is already in progress")

        try:
            overrides = request.options.overrides()
            resolved = settings.resolve(overrides)
            contract = utils.extract_contract_name(request.source) or "unknown"
            recorder = self.store.start(
                request.source, contract, filename=request.filename, options=overrides
            )
        except Exception:
            self._lock.release()
            raise

        active = ActiveRun(recorder.run_id)
        self.active = active

        def sink(event: events.Event) -> None:
            # Events arrive between gates and attempts — the natural points to
            # honour a cancel without killing forge mid-flight.
            active.append(events.to_json(event))
            recorder.sink(event)
            if active.cancel_requested:
                raise Cancelled()

        def worker() -> None:
            try:
                result = optimizer.run_optimization_loop(
                    request.source,
                    verbose=False,
                    on_event=sink,
                    settings=resolved.validator,
                    max_retries=resolved.llm["max_retries"],
                    model=resolved.llm["model"],
                )
            except Cancelled:
                result = optimizer.OptimizationResult(False, "cancelled by the user")
            except Exception as exc:  # recorded, never a silent dead thread
                result = optimizer.OptimizationResult(False, f"internal error: {exc}")
            try:
                recorder.finish(result)
            finally:
                active.finish()
                self.active = None
                self._lock.release()

        threading.Thread(target=worker, name=f"run-{recorder.run_id}", daemon=True).start()
        return recorder.run_id

    def cancel(self, run_id: str) -> bool:
        active = self.active
        if active is None or active.run_id != run_id:
            return False
        active.cancel_requested = True
        return True


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def create_app(store: runstore.RunStore | None = None) -> FastAPI:
    store = store if store is not None else runstore.RunStore()
    manager = RunManager(store)
    app = FastAPI(title="gas-optimizer", docs_url=None, redoc_url=None)
    app.state.manager = manager

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = resources.files(__package__).joinpath("static/index.html")
        return page.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict:
        checks = preflight.report()
        return {
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "hint": c.hint,
                    "required": c.required,
                }
                for c in checks
            ],
            "ready": not preflight.require(checks),
            "busy": manager.active is not None,
        }

    @app.post("/api/runs", status_code=201)
    def start_run(request: StartRun) -> dict:
        if not request.source.strip():
            raise HTTPException(status_code=422, detail="source is empty")
        run_id = manager.start(request)
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        active = manager.active
        summaries = []
        for summary in store.list():
            status = summary.status
            if active is not None and summary.run_id == active.run_id:
                status = "running"
            elif status == "incomplete" and active is None:
                status = "incomplete"
            summaries.append(
                {
                    "run_id": summary.run_id,
                    "started": summary.started,
                    "contract": summary.contract,
                    "status": status,
                    "attempts": summary.attempts,
                    "total_delta": summary.total_delta,
                }
            )
        return summaries

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        record = store.load(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return record

    @app.get("/api/runs/{run_id}/events")
    def stream_events(run_id: str) -> StreamingResponse:
        active = manager.active
        live = active if active is not None and active.run_id == run_id else None

        def replay_then_tail():
            if live is None:
                # Finished run: replay the recorded stream and close.
                for payload in store.load_events(run_id):
                    yield _sse(payload)
                yield "event: done\ndata: {}\n\n"
                return

            cursor = 0
            while True:
                with live.condition:
                    while cursor >= len(live.events) and not live.done:
                        live.condition.wait(timeout=1.0)
                    chunk = live.events[cursor:]
                    cursor = len(live.events)
                    done = live.done and cursor >= len(live.events)
                for payload in chunk:
                    yield _sse(payload)
                if done:
                    yield "event: done\ndata: {}\n\n"
                    return

        return StreamingResponse(replay_then_tail(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict:
        if not manager.cancel(run_id):
            raise HTTPException(status_code=404, detail="no such run in progress")
        return {"cancelling": True}

    return app


def main(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    import uvicorn

    app = create_app()
    url = f"http://{host}:{port}"
    print(f"gas-optimizer GUI: {url}")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
