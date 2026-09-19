# Frontends: events, TUI, web GUI

The optimizer and validator never print. They emit typed events
(`gas_optimizer/events.py`), and every frontend is a sink over that one stream:

- `render.ConsoleRenderer` — the classic `[VALIDATE] ...` console output
- `render.JsonlRenderer` — the `--stream-json` protocol, one JSON object per line
- `frontends/tui.py` — Textual terminal UI
- `frontends/web/` — FastAPI backend + chat-style page

## The event stream

Each event serializes as `{"schema": 1, "event": "<kind>", "t": <epoch>, ...}`.

| Kind | Fields | Meaning |
| --- | --- | --- |
| `run_started` | `contract`, `max_attempts`, `settings` | Loop begins |
| `attempt_started` | `attempt`, `max_attempts`, `fixing` | New attempt; `fixing` is the failure type being addressed |
| `llm_requested` | `attempt`, `model` | Candidate requested from the model |
| `shape_rejected` | `attempt`, `problems` | Pre-compile shape check failed |
| `validation_started` | `attempt` | Candidate written; gates begin |
| `gate_started` / `gate_passed` | `gate`, `detail` | One of `validate`, `build`, `artifacts`, `harness`, `equivalence`, `gas`, `hevm` |
| `gate_failed` | `gate`, `failure` | The failure dict that becomes model feedback |
| `note` | `text` | Advisory (hevm verdicts, skips) |
| `candidate_rejected` | `attempt`, `failure` | Attempt over, feedback recorded |
| `candidate_accepted` | `attempt`, `gas`, `notes`, `total_delta` | All gates passed |
| `run_finished` | `success`, `message`, `attempts`, `total_delta`, `notes` | Always the last event |

`gas` entries carry `signature`, `original`, `candidate`, `original_ok`,
`candidate_ok`, plus derived `delta`, `comparable`, `improved`.

Programmatic use:

```python
from gas_optimizer import events, optimizer

seen = []
result = optimizer.run_optimization_loop(source, on_event=seen.append)
```

## Run history

Every `gas-optimize run`, TUI run and GUI run records to `runs/<id>/`:

```
runs/20260919-142233-a1b2/
    run.json        outcome, gas table, notes, unified diff
    events.jsonl    the full event stream, replayable
    original.sol    the input as submitted
    optimized.sol   the accepted candidate (success only)
```

`--no-save-run` skips recording. The directory is gitignored and append-only;
delete old runs freely.

## TUI

```bash
pip install -e ".[tui]"
gas-optimize tui [input.sol]
```

Left panel: contract path, fuzz runs, max retries, hevm switch. Log tab streams
the rendered events; the Result tab holds the gas table and unified diff. The
loop runs in a worker thread, so the UI stays responsive during forge runs.

## Web GUI

```bash
pip install -e ".[gui]"
gas-optimize gui [--host 127.0.0.1] [--port 8765] [--no-browser]
```

Chat-style page: paste or drop a contract, submit, and watch each attempt as a
card whose gate checklist fills in live. Rejections show the exact feedback the
model receives; acceptance shows the per-function gas table, the diff, and
copy/download buttons. The sidebar lists past runs from `runs/`.

The server binds to `127.0.0.1` by default and has no authentication — it is a
local tool. Do not bind it to a public interface.

### API

| Route | Meaning |
| --- | --- |
| `GET /api/health` | Preflight report (forge, hevm, HF_TOKEN, workspace) + busy flag |
| `POST /api/runs` | Start a run: `{source, filename?, options?}` → `{run_id}`; `409` while another run is active |
| `GET /api/runs` | History summaries, newest first |
| `GET /api/runs/{id}` | Full record: outcome, gas, notes, diff, sources, events |
| `GET /api/runs/{id}/events` | SSE stream: replays recorded events, then live-tails until `run_finished`; ends with a `done` event |
| `POST /api/runs/{id}/cancel` | Best-effort cancel, honoured between gates |

`options` accepts `fuzz_runs`, `max_retries`, `hevm`, `model`.

### One run at a time

`foundry/src/` and `foundry/test/` are a single shared workspace, wiped at the
start of every validation. The backend therefore serializes runs (second
`POST /api/runs` → `409`). Parallel runs would need per-run workspace clones —
out of scope for a local tool.

### Cancellation is best-effort

The cancel flag is checked when events fire — between gates and attempts. A
`forge test` already in flight runs to completion (or to `forge_timeout`)
before the cancel lands.
