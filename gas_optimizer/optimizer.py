"""
The generate-and-verify loop.

Each attempt asks for a complete optimized contract, writes it beside the
original under a distinct name, and puts it through the validator. Anything the
validator rejects comes back as structured feedback for the next attempt.

Note the loop carries `current_code` forward across attempts. The previous
version reset to the pristine original every time while telling the model it was
looking at "code after last patch", so successive optimizations could never
accumulate and the retry prompt described a state that did not exist.

Progress is reported through `events` (see events.py): pass `on_event` to
consume the stream; without it, `verbose` selects the console renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config, events, llm_client, render, utils, validator

# Failures the model cannot fix; retrying only burns attempts and API calls.
FATAL_FAILURE_TYPES = {"environment_error"}


@dataclass
class OptimizationResult:
    success: bool
    message: str
    optimized_code: str | None = None
    attempts: int = 0
    gas: list[validator.GasEntry] = field(default_factory=list)
    candidate_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    output_path: Path | None = None

    @property
    def total_delta(self) -> int:
        return sum(e.delta for e in self.gas if e.comparable)


def _shape_failure(problems: list[str]) -> dict:
    return {
        "compile": "false",
        "test": "N/A",
        "type": "shape_error",
        "error": "; ".join(problems),
        "trace": "The response must be the complete contract, same name, same pragma.",
    }


def run_optimization_loop(
    original_code: str,
    verbose: bool = True,
    *,
    on_event: events.EventSink | None = None,
    settings: dict | None = None,
    max_retries: int | None = None,
    model: str | None = None,
) -> OptimizationResult:
    """Optimize `original_code`, verifying every proposal before accepting it."""
    emit = (
        on_event
        if on_event is not None
        else (render.ConsoleRenderer() if verbose else events.NULL_SINK)
    )

    def finished(result: OptimizationResult) -> OptimizationResult:
        emit(
            events.RunFinished(
                success=result.success,
                message=result.message,
                attempts=result.attempts,
                total_delta=result.total_delta,
                notes=tuple(result.notes),
            )
        )
        return result

    contract_name = utils.extract_contract_name(original_code)
    if not contract_name:
        return finished(OptimizationResult(False, "No `contract` declaration found in the input."))

    candidate_name = f"{contract_name}Candidate"

    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)
    original_path = config.SOL_FOLDER / f"{contract_name}.sol"
    candidate_path = config.SOL_FOLDER / f"{candidate_name}.sol"
    original_path.write_text(original_code, encoding="utf-8")

    settings = settings or validator.load_config()
    max_retries = max_retries if max_retries is not None else config.MAX_RETRIES
    model_id = model or config.MODEL_ID

    emit(
        events.RunStarted(
            contract=contract_name,
            max_attempts=max_retries,
            settings=dict(settings),
        )
    )

    current_code = original_code
    retry_info: dict | None = None
    last_message = "no attempts were made"

    for attempt in range(1, max_retries + 1):
        emit(
            events.AttemptStarted(
                attempt=attempt,
                max_attempts=max_retries,
                fixing=retry_info["type"] if retry_info else None,
            )
        )

        emit(events.LLMRequested(attempt=attempt, model=model_id))
        try:
            # Forward the override only when one was given, so scripted stand-ins
            # with the historical two-argument signature keep working.
            kwargs = {"model": model} if model is not None else {}
            candidate_code = llm_client.get_optimization_proposal(
                current_code, retry_info, **kwargs
            )
        except llm_client.LLMError as exc:
            return finished(OptimizationResult(False, str(exc), attempts=attempt))

        problems = utils.check_candidate_source(current_code, candidate_code)
        if problems:
            emit(events.ShapeRejected(attempt=attempt, problems=tuple(problems)))
            retry_info = _shape_failure(problems)
            retry_info["attempt"] = attempt
            last_message = retry_info["error"]
            continue

        # The candidate must not declare the same contract as the original, or
        # it overwrites it and ends up compared against itself.
        candidate_path.write_text(
            utils.rename_contract(candidate_code, contract_name, candidate_name),
            encoding="utf-8",
        )

        emit(events.ValidationStarted(attempt=attempt))
        result = validator.validate(
            original_path, candidate_path, settings, verbose=verbose, on_event=emit
        )

        if result.ok:
            emit(
                events.CandidateAccepted(
                    attempt=attempt,
                    gas=tuple(result.gas),
                    notes=tuple(result.notes),
                    total_delta=result.total_delta,
                )
            )
            return finished(
                OptimizationResult(
                    True,
                    f"Equivalent and {abs(result.total_delta)} gas cheaper.",
                    optimized_code=candidate_code,
                    attempts=attempt,
                    gas=result.gas,
                    candidate_path=candidate_path,
                    notes=list(result.notes),
                )
            )

        failure = result.failure or {}
        emit(events.CandidateRejected(attempt=attempt, failure=dict(failure)))

        last_message = f"{failure.get('type')}: {failure.get('error')}"
        if failure.get("type") in FATAL_FAILURE_TYPES:
            return finished(
                OptimizationResult(
                    False, f"Cannot continue: {failure.get('error')}", attempts=attempt
                )
            )

        retry_info = dict(failure)
        retry_info["attempt"] = attempt

        # Feed the rejected candidate forward: the model is asked to fix that
        # specific failure, not to start over. A candidate that failed to
        # compile is not a useful base, so fall back to the last good source.
        if failure.get("compile") == "true":
            current_code = candidate_code

    return finished(
        OptimizationResult(
            False,
            f"No candidate passed in {max_retries} attempts. Last failure: {last_message}",
            attempts=max_retries,
        )
    )
