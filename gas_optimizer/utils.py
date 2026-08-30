"""
Source-level helpers for handling model output.

The model returns a complete contract rather than a diff. That removes an
entire failure class: a hand-rolled fuzzy patcher has to guess which of several
identical lines a hunk meant, and silently produces a no-op when it guesses
wrong. A whole file either parses and compiles or it does not.

The authoritative shape check (same public ABI) happens after compilation in
verification.harness_generator. What lives here are the cheap checks worth
making before spending a compile on obviously unusable output.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"```(?:solidity|sol)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_CONTRACT = re.compile(r"\bcontract\s+(\w+)")
_PRAGMA = re.compile(r"^\s*pragma\s+solidity\s+([^;]+);", re.MULTILINE)


def clean_llm_response(response_text: str) -> str:
    """Strip markdown fencing and surrounding prose from a model response.

    Picks the longest fenced block when several are present — models sometimes
    quote a snippet before emitting the full contract.
    """
    if not response_text:
        return ""

    blocks = _FENCE.findall(response_text)
    if blocks:
        return max(blocks, key=len).strip()
    return response_text.strip()


def extract_contract_name(code: str) -> str | None:
    """Name of the first contract declared in `code`, or None."""
    match = _CONTRACT.search(code)
    return match.group(1) if match else None


def extract_pragma(code: str) -> str | None:
    """The solidity version pragma, normalized for whitespace."""
    match = _PRAGMA.search(code)
    return " ".join(match.group(1).split()) if match else None


def rename_contract(code: str, old_name: str, new_name: str) -> str:
    """Rename a contract declaration, e.g. `contract X` -> `contract XCandidate`.

    The candidate must not share a contract name with the original: filenames
    are derived from the declared name, so identical names make the candidate
    overwrite the original and get compared against itself.
    """
    return re.sub(rf"\bcontract\s+{re.escape(old_name)}\b", f"contract {new_name}", code)


def check_candidate_source(original_code: str, candidate_code: str) -> list[str]:
    """Cheap pre-compile checks on model output. Empty list means usable.

    Deliberately shallow — anything requiring real Solidity semantics is left
    to the compiler and to the ABI parity check in the harness generator.
    """
    problems: list[str] = []

    if not candidate_code.strip():
        return ["the response was empty"]

    if "```" in candidate_code:
        problems.append("the response still contains markdown fencing")

    original_name = extract_contract_name(original_code)
    candidate_name = extract_contract_name(candidate_code)
    if candidate_name is None:
        problems.append("no `contract` declaration found in the response")
    elif original_name and candidate_name != original_name:
        problems.append(
            f"the contract was renamed from `{original_name}` to `{candidate_name}`; "
            f"keep the original name"
        )

    original_pragma = extract_pragma(original_code)
    candidate_pragma = extract_pragma(candidate_code)
    if candidate_pragma is None:
        problems.append("the response has no `pragma solidity` line")
    elif original_pragma and candidate_pragma != original_pragma:
        problems.append(
            f"the pragma changed from `{original_pragma}` to `{candidate_pragma}`; "
            f"keep the compiler version unchanged"
        )

    if candidate_code.strip() == original_code.strip():
        problems.append("the response is identical to the input; no optimization was applied")

    return problems
