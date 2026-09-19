"""
Web GUI backend: start a run, stream its events over SSE, list history,
refuse concurrent runs. The optimization loop is a scripted stand-in — no
forge, no network. Requires the [gui] extra (fastapi + httpx).
"""

import json
import threading
import time

import pytest

from gas_optimizer import events, optimizer, runstore, validator

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from gas_optimizer.frontends.web import server  # noqa: E402

SOURCE = "pragma solidity ^0.8.20;\ncontract Foo { function f() external {} }\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    def scripted_loop(source, verbose=True, *, on_event=None, **kwargs):
        emit = on_event or events.NULL_SINK
        emit(events.RunStarted("Foo", 5, {}))
        emit(events.AttemptStarted(1, 5, None))
        emit(events.GateStarted(gate="build"))
        emit(events.GatePassed(gate="build"))
        result = optimizer.OptimizationResult(
            True,
            "cheaper",
            optimized_code=SOURCE.replace("{}", "{ }"),
            attempts=1,
            gas=[validator.GasEntry("f()", 100, 90, True, True)],
        )
        emit(events.RunFinished(True, "cheaper", 1, -10))
        return result

    monkeypatch.setattr(optimizer, "run_optimization_loop", scripted_loop)
    app = server.create_app(store=runstore.RunStore(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def _wait_for_finish(client, run_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = client.get("/api/runs").json()
        row = next((r for r in runs if r["run_id"] == run_id), None)
        if row and row["status"] != "running":
            return row
        time.sleep(0.02)
    raise AssertionError("run never finished")


class TestRunLifecycle:
    def test_start_stream_and_record(self, client):
        response = client.post("/api/runs", json={"source": SOURCE, "filename": "Foo.sol"})
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        _wait_for_finish(client, run_id)

        # SSE replays the recorded stream, then closes with a done event.
        seen = []
        with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("data: ") and line != "data: {}":
                    seen.append(json.loads(line[len("data: ") :]))
                if line.startswith("event: done"):
                    break
        kinds = [e["event"] for e in seen]
        assert kinds[0] == "run_started"
        assert kinds[-1] == "run_finished"

        record = client.get(f"/api/runs/{run_id}").json()
        assert record["success"] is True
        assert record["original_source"] == SOURCE
        assert record["optimized_code"] is not None
        assert record["gas"][0]["delta"] == -10

    def test_empty_source_is_422(self, client):
        assert client.post("/api/runs", json={"source": "   "}).status_code == 422

    def test_unknown_run_is_404(self, client):
        assert client.get("/api/runs/nope").status_code == 404

    def test_history_lists_the_run(self, client):
        run_id = client.post("/api/runs", json={"source": SOURCE}).json()["run_id"]
        row = _wait_for_finish(client, run_id)
        assert row["contract"] == "Foo"
        assert row["status"] == "accepted"
        assert row["total_delta"] == -10


class TestSingleFlight:
    def test_concurrent_start_is_409(self, tmp_path, monkeypatch):
        release = threading.Event()

        def slow_loop(source, verbose=True, *, on_event=None, **kwargs):
            release.wait(timeout=5)
            return optimizer.OptimizationResult(False, "done", attempts=1)

        monkeypatch.setattr(optimizer, "run_optimization_loop", slow_loop)
        app = server.create_app(store=runstore.RunStore(tmp_path))
        with TestClient(app) as client:
            first = client.post("/api/runs", json={"source": SOURCE})
            assert first.status_code == 201

            second = client.post("/api/runs", json={"source": SOURCE})
            assert second.status_code == 409

            release.set()
            _wait_for_finish(client, first.json()["run_id"])


class TestStaticAndHealth:
    def test_index_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "gas-optimizer" in response.text

    def test_health_reports_checks(self, client):
        payload = client.get("/api/health").json()
        names = {check["name"] for check in payload["checks"]}
        assert {"workspace", "forge", "HF_TOKEN", "hevm"} <= names
