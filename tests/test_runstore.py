"""
Run persistence: the directory layout, the event log, and history listing.
"""

from gas_optimizer import events, optimizer, runstore, validator

SOURCE = "pragma solidity ^0.8.20;\ncontract Foo { function f() external {} }\n"
OPTIMIZED = SOURCE.replace("external {}", "external { }")


def _accepted():
    return optimizer.OptimizationResult(
        True,
        "cheaper",
        optimized_code=OPTIMIZED,
        attempts=2,
        gas=[validator.GasEntry("f()", 100, 90, True, True)],
        notes=["hevm proved the two bytecodes equivalent"],
    )


def test_successful_run_persists_everything(tmp_path):
    store = runstore.RunStore(tmp_path)
    recorder = store.start(SOURCE, "Foo", filename="Foo.sol", options={"fuzz_runs": 300})

    recorder.sink(events.RunStarted("Foo", 5, {}))
    recorder.sink(events.RunFinished(True, "cheaper", 2, -10))
    record = recorder.finish(_accepted())

    assert (recorder.directory / "original.sol").read_text() == SOURCE
    assert (recorder.directory / "optimized.sol").read_text() == OPTIMIZED
    assert record["success"] is True
    assert record["gas"][0]["delta"] == -10
    assert record["notes"] == ["hevm proved the two bytecodes equivalent"]
    assert "-\tfunction f() external {}" in record["diff"] or "external {}" in record["diff"]

    loaded = store.load(recorder.run_id)
    assert loaded["contract"] == "Foo"
    assert [e["event"] for e in loaded["events"]] == ["run_started", "run_finished"]


def test_failed_run_keeps_no_optimized_copy(tmp_path):
    store = runstore.RunStore(tmp_path)
    recorder = store.start(SOURCE, "Foo")
    record = recorder.finish(optimizer.OptimizationResult(False, "no luck", attempts=5))

    assert not (recorder.directory / "optimized.sol").exists()
    assert record["diff"] == ""

    (summary,) = store.list()
    assert summary.status == "rejected"
    assert summary.attempts == 5


def test_listing_is_newest_first_and_tolerates_junk(tmp_path):
    store = runstore.RunStore(tmp_path)
    first = store.start(SOURCE, "Foo")
    first.finish(_accepted())

    # a directory with no run.json (crashed mid-run) still shows up
    (tmp_path / "zz-broken").mkdir()

    summaries = store.list()
    assert [s.status for s in summaries] == ["incomplete", "accepted"]


def test_empty_store_lists_nothing(tmp_path):
    assert runstore.RunStore(tmp_path / "missing").list() == []
    assert runstore.RunStore(tmp_path).load("nope") is None
