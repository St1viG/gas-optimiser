"""
The event stream is the contract every frontend depends on: ordering, JSON
shape, and silence when nobody is listening. None of this needs forge — the
validator is replaced by a scripted stand-in.
"""

import io
import json

import pytest

from gas_optimizer import config, events, llm_client, optimizer, render, validator

ORIGINAL = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Foo {
    uint256 public x;
    function set(uint256 v) external { x = v; }
}
"""

# Differs only in whitespace: passes every shape check, keeps name and pragma.
CANDIDATE = ORIGINAL.replace("x = v;", "x =  v;")

GAS_ENTRY = validator.GasEntry("set(uint256)", 100, 90, True, True)


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Keep the loop's file writes out of the real foundry workspace."""
    monkeypatch.setattr(config, "SOL_FOLDER", tmp_path / "src")
    monkeypatch.setattr(validator, "load_config", lambda *a, **k: dict(validator.DEFAULT_SETTINGS))
    return tmp_path


def _script_model(monkeypatch, responses):
    calls = []

    def fake(current_code, retry_info=None, **kwargs):
        calls.append((current_code, retry_info))
        index = min(len(calls) - 1, len(responses) - 1)
        return responses[index]

    monkeypatch.setattr(llm_client, "get_optimization_proposal", fake)
    return calls


def _accepting_validate(monkeypatch, notes=("hevm proved the two bytecodes equivalent",)):
    def fake(original, candidate, settings=None, verbose=True, on_event=None):
        emit = on_event or events.NULL_SINK
        emit(events.GateStarted(gate="build"))
        emit(events.GatePassed(gate="build"))
        return validator.ValidationResult(True, None, gas=[GAS_ENTRY], notes=list(notes))

    monkeypatch.setattr(validator, "validate", fake)


def _rejecting_validate(monkeypatch, failure):
    def fake(original, candidate, settings=None, verbose=True, on_event=None):
        emit = on_event or events.NULL_SINK
        emit(events.GateFailed(gate="gas", failure=failure))
        return validator.ValidationResult(False, failure, gas=[])

    monkeypatch.setattr(validator, "validate", fake)


class TestLoopEventStream:
    def test_success_emits_the_full_sequence_in_order(self, scratch, monkeypatch):
        _script_model(monkeypatch, [CANDIDATE])
        _accepting_validate(monkeypatch)

        seen = []
        result = optimizer.run_optimization_loop(ORIGINAL, on_event=seen.append)

        assert result.success
        kinds = [events.kind(e) for e in seen]
        assert kinds == [
            "run_started",
            "attempt_started",
            "llm_requested",
            "validation_started",
            "gate_started",
            "gate_passed",
            "candidate_accepted",
            "run_finished",
        ]

    def test_notes_survive_into_the_result_and_final_event(self, scratch, monkeypatch):
        _script_model(monkeypatch, [CANDIDATE])
        _accepting_validate(monkeypatch, notes=("hevm proved the two bytecodes equivalent",))

        seen = []
        result = optimizer.run_optimization_loop(ORIGINAL, on_event=seen.append)

        assert result.notes == ["hevm proved the two bytecodes equivalent"]
        finished = seen[-1]
        assert isinstance(finished, events.RunFinished)
        assert "hevm proved the two bytecodes equivalent" in finished.notes

    def test_rejection_becomes_feedback_and_fixing_label(self, scratch, monkeypatch):
        failure = {
            "compile": "true",
            "test": "GasBench",
            "type": "gas_error",
            "error": "not cheaper",
            "trace": "",
        }
        # The rejected candidate is carried forward, so the second response has
        # to differ from it or the shape check flags it as unchanged.
        second = ORIGINAL.replace("x = v;", "x = v ;")
        _script_model(monkeypatch, [CANDIDATE, second])
        _rejecting_validate(monkeypatch, failure)

        seen = []
        result = optimizer.run_optimization_loop(ORIGINAL, on_event=seen.append, max_retries=2)

        assert not result.success
        rejected = [e for e in seen if isinstance(e, events.CandidateRejected)]
        assert len(rejected) == 2
        assert rejected[0].failure["type"] == "gas_error"

        second_attempt = [e for e in seen if isinstance(e, events.AttemptStarted)][1]
        assert second_attempt.fixing == "gas_error"

    def test_environment_error_stops_after_one_attempt(self, scratch, monkeypatch):
        failure = {
            "compile": "false",
            "test": "N/A",
            "type": "environment_error",
            "error": "forge is not installed or not on PATH",
            "trace": "",
        }
        _script_model(monkeypatch, [CANDIDATE])
        _rejecting_validate(monkeypatch, failure)

        seen = []
        result = optimizer.run_optimization_loop(ORIGINAL, on_event=seen.append, max_retries=5)

        assert not result.success
        assert result.attempts == 1
        assert "forge is not installed" in result.message
        assert len([e for e in seen if isinstance(e, events.AttemptStarted)]) == 1

    def test_quiet_run_prints_nothing(self, scratch, monkeypatch, capsys):
        _script_model(monkeypatch, [CANDIDATE])
        _accepting_validate(monkeypatch)

        optimizer.run_optimization_loop(ORIGINAL, verbose=False)

        assert capsys.readouterr().out == ""


class TestWireFormat:
    def test_every_event_kind_round_trips_through_json(self):
        samples = [
            events.RunStarted("Foo", 5, {"fuzz_runs": 2000}),
            events.AttemptStarted(1, 5, None),
            events.LLMRequested(1, "some/model"),
            events.ShapeRejected(1, ("left a fence",)),
            events.ValidationStarted(1),
            events.GateStarted("equivalence", "300 runs"),
            events.GatePassed("gas", "summary"),
            events.GateFailed("gas", {"type": "gas_error"}),
            events.Note("hevm timed out"),
            events.CandidateRejected(1, {"type": "gas_error"}),
            events.CandidateAccepted(2, (GAS_ENTRY,), ("note",), -10),
            events.RunFinished(True, "done", 2, -10, ("note",)),
        ]
        for event in samples:
            payload = json.loads(json.dumps(events.to_json(event)))
            assert payload["schema"] == events.SCHEMA_VERSION
            assert payload["event"] == events.kind(event)

    def test_gas_entries_serialize_with_derived_fields(self):
        payload = events.to_json(events.CandidateAccepted(1, (GAS_ENTRY,), (), -10))
        (entry,) = payload["gas"]
        assert entry["signature"] == "set(uint256)"
        assert entry["delta"] == -10
        assert entry["comparable"] is True

    def test_tee_fans_out(self):
        first, second = [], []
        sink = events.tee(first.append, second.append)
        sink(events.Note("hello"))
        assert first == second == [events.Note("hello")]


class TestRenderers:
    def test_console_renderer_reproduces_the_known_lines(self):
        stream = io.StringIO()
        sink = render.ConsoleRenderer(stream=stream)
        sink(events.RunStarted("Foo", 5, {}))
        sink(events.AttemptStarted(2, 5, "gas_error"))
        sink(events.GateStarted("build"))
        sink(events.GateStarted("equivalence", "300 runs"))
        sink(events.GatePassed("equivalence"))
        sink(events.CandidateRejected(2, {"type": "gas_error", "error": "not cheaper"}))

        out = stream.getvalue()
        assert "[INFO] Contract: Foo" in out
        assert "ATTEMPT 2/5  (fixing gas_error)" in out
        assert "[VALIDATE] Building..." in out
        assert "[VALIDATE] Differential fuzzing (300 runs)..." in out
        assert "[VALIDATE] Equivalence holds." in out
        assert "[3] Rejected [gas_error]: not cheaper" in out

    def test_console_renderer_quiet_is_silent(self):
        stream = io.StringIO()
        render.ConsoleRenderer(stream=stream, quiet=True)(events.Note("loud"))
        assert stream.getvalue() == ""

    def test_jsonl_renderer_writes_one_object_per_line(self):
        stream = io.StringIO()
        sink = render.JsonlRenderer(stream=stream)
        sink(events.Note("a"))
        sink(events.GatePassed("build"))

        lines = stream.getvalue().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "note"
        assert json.loads(lines[1])["event"] == "gate_passed"
