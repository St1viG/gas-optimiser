"""
The generate-and-verify loop.

Each attempt asks for a complete optimized contract, writes it beside the
original under a distinct name, and puts it through the validator. Anything the
validator rejects comes back as structured feedback for the next attempt.

Note the loop carries `current_code` forward across attempts. The previous
version reset to the pristine original every time while telling the model it was
looking at "code after last patch", so successive optimizations could never
accumulate and the retry prompt described a state that did not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config, llm_client, utils, validator


@dataclass
class OptimizationResult:
    success: bool
    message: str
    optimized_code: str | None = None
    attempts: int = 0
    gas: list[validator.GasEntry] = field(default_factory=list)
    candidate_path: Path | None = None

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


def run_optimization_loop(original_code: str, verbose: bool = True) -> OptimizationResult:
    """Optimize `original_code`, verifying every proposal before accepting it."""
    contract_name = utils.extract_contract_name(original_code)
    if not contract_name:
        return OptimizationResult(False, "No `contract` declaration found in the input.")

    candidate_name = f"{contract_name}Candidate"

    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)
    original_path = config.SOL_FOLDER / f"{contract_name}.sol"
    candidate_path = config.SOL_FOLDER / f"{candidate_name}.sol"
    original_path.write_text(original_code, encoding="utf-8")

    print(f"[INFO] Contract: {contract_name}")

    settings = validator.load_config()
    current_code = original_code
    retry_info: dict | None = None
    last_message = "no attempts were made"

    for attempt in range(1, config.MAX_RETRIES + 1):
        header = f"ATTEMPT {attempt}/{config.MAX_RETRIES}"
        if retry_info:
            header += f"  (fixing {retry_info['type']})"
        print("\n" + "=" * 60)
        print(header)
        print("=" * 60)

        print("[1] Requesting a candidate...")
        try:
            candidate_code = llm_client.get_optimization_proposal(current_code, retry_info)
        except llm_client.LLMError as exc:
            return OptimizationResult(False, str(exc), attempts=attempt)

        problems = utils.check_candidate_source(current_code, candidate_code)
        if problems:
            print(f"[2] Rejected before compiling: {'; '.join(problems)}")
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

        print("[2] Validating...")
        result = validator.validate(original_path, candidate_path, settings, verbose=verbose)

        if result.ok:
            print(f"\n[SUCCESS] Verified on attempt {attempt}.")
            return OptimizationResult(
                True,
                f"Equivalent and {abs(result.total_delta)} gas cheaper.",
                optimized_code=candidate_code,
                attempts=attempt,
                gas=result.gas,
                candidate_path=candidate_path,
            )

        failure = result.failure or {}
        print(f"[3] Rejected [{failure.get('type')}]: {failure.get('error')}")

        retry_info = dict(failure)
        retry_info["attempt"] = attempt
        last_message = f"{failure.get('type')}: {failure.get('error')}"

        # Feed the rejected candidate forward: the model is asked to fix that
        # specific failure, not to start over. A candidate that failed to
        # compile is not a useful base, so fall back to the last good source.
        if failure.get("compile") == "true":
            current_code = candidate_code

    return OptimizationResult(
        False,
        f"No candidate passed in {config.MAX_RETRIES} attempts. Last failure: {last_message}",
        attempts=config.MAX_RETRIES,
    )
