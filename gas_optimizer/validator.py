"""
Decides whether an optimized candidate may be accepted.

A candidate has to clear two independent gates:

  1. Equivalence — differential fuzzing over a generated harness. Original and
     candidate receive identical calldata from identical state; revert status,
     return data, storage writes and emitted events must match.
  2. Gas — a deterministic benchmark. The candidate may not regress on any
     function, and must be strictly cheaper on at least one. Without this an
     unchanged (or slower) candidate would pass, which is what "optimized"
     used to mean here.

Optionally a third, `hevm equivalence`, when the binary is installed. It is
advisory: a missing binary or a solver timeout is reported as skipped, never as
a counterexample.

All `forge` invocations run with cwd=config.FOUNDRY_DIR, so this module behaves
the same regardless of where the process was started.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .verification import artifacts, harness_generator

DEFAULT_SETTINGS: dict[str, object] = {
    "fuzz_runs": 2000,
    "hevm_enabled": False,
    "hevm_timeout": 300,
    "hevm_solver_timeout": 30000,
    "hevm_max_iterations": 5,
    "max_array_length": 5,
    "require_gas_improvement": True,
    "forge_timeout": 900,
}

BOOL_KEYS = {"hevm_enabled", "require_gas_improvement"}
_TRUTHY = ("true", "1", "yes", "on")

GAS_LOG_PREFIX = "GASRESULT|"


# --- configuration -----------------------------------------------------------


def load_config(config_path: Path | str = config.VALIDATOR_CONFIG_PATH) -> dict:
    """Load validator settings, falling back to DEFAULT_SETTINGS."""
    settings = dict(DEFAULT_SETTINGS)

    path = Path(config_path)
    if not path.exists():
        return settings

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = (part.strip() for part in line.split("=", 1))
        if key not in settings:
            continue
        if key in BOOL_KEYS:
            settings[key] = value.lower() in _TRUTHY
        else:
            try:
                settings[key] = int(value)
            except ValueError:
                settings[key] = value

    return settings


def _render(value: object) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def save_config(settings: dict, config_path: Path | str = config.VALIDATOR_CONFIG_PATH) -> None:
    """Write settings back, preserving comments and ordering."""
    path = Path(config_path)

    if not path.exists():
        body = ["# Validator Configuration"]
        body += [f"{k}={_render(v)}" for k, v in settings.items()]
        path.write_text("\n".join(body) + "\n")
        return

    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in settings:
                out.append(f"{key}={_render(settings[key])}")
                continue
        out.append(line)

    path.write_text("\n".join(out) + "\n")


# --- results -----------------------------------------------------------------


@dataclass(frozen=True)
class GasEntry:
    signature: str
    original: int
    candidate: int
    original_ok: bool
    candidate_ok: bool

    @property
    def comparable(self) -> bool:
        """Only calls that succeeded on both sides carry a meaningful number."""
        return self.original_ok and self.candidate_ok

    @property
    def delta(self) -> int:
        return self.candidate - self.original

    @property
    def improved(self) -> bool:
        return self.comparable and self.candidate < self.original

    @property
    def regressed(self) -> bool:
        return self.comparable and self.candidate > self.original

    def describe(self) -> str:
        if not self.comparable:
            return f"{self.signature}: reverted, not measured"
        sign = "+" if self.delta > 0 else ""
        return (
            f"{self.signature}: {self.original} -> {self.candidate} gas "
            f"({sign}{self.delta})"
        )


@dataclass
class ValidationResult:
    ok: bool
    failure: dict | None = None
    gas: list[GasEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_delta(self) -> int:
        return sum(e.delta for e in self.gas if e.comparable)

    def gas_summary(self) -> str:
        if not self.gas:
            return "no gas measurements"
        lines = [f"  {e.describe()}" for e in self.gas]
        lines.append(f"  total: {self.total_delta:+d} gas")
        return "\n".join(lines)


def _failure(
    failure_type: str,
    error: str,
    *,
    compile_ok: bool = True,
    test: str = "N/A",
    trace: str = "",
) -> dict:
    """Failure payload in the shape prompts.USER_PROMPT_RETRY expects."""
    return {
        "compile": "true" if compile_ok else "false",
        "test": test,
        "type": failure_type,
        "error": error,
        "trace": trace,
    }


# --- process helpers ---------------------------------------------------------


def run_command(
    command: list[str],
    cwd: Path | str | None = None,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr).

    A timeout is reported as a normal non-zero result so callers never hang.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(command)}"
    except FileNotFoundError:
        return 127, "", f"command not found: {command[0]}"
    except OSError as exc:
        return 1, "", str(exc)


def run_forge(args: list[str], timeout: float | None = None) -> tuple[int, str, str]:
    """Run a forge subcommand inside the Foundry workspace."""
    return run_command(["forge", *args], cwd=config.FOUNDRY_DIR, timeout=timeout)


def _forge_test_json(args: list[str], timeout: float | None) -> tuple[dict, str]:
    """Run `forge test --json -vv` and return (parsed suites, raw output).

    Returns an empty dict if the run produced no JSON (a compile failure, say),
    leaving the raw text for the caller to report.
    """
    _, stdout, stderr = run_forge([*args, "--json", "-vv"], timeout=timeout)
    raw = (stdout + "\n" + stderr).strip()

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), raw
            except json.JSONDecodeError:
                continue

    return {}, raw


def _iter_tests(suites: dict):
    for suite_name, suite in suites.items():
        for test_name, result in (suite.get("test_results") or {}).items():
            yield suite_name, test_name, result


# --- workspace ---------------------------------------------------------------


def prepare_workspace(original: Path, candidate: Path) -> tuple[Path, Path]:
    """Copy the pair into foundry/src and clear stale generated files.

    The test dir must be emptied *before* the first build: a harness left over
    from a previous run still imports contracts that no longer exist, and the
    build fails for reasons that have nothing to do with the candidate.
    """
    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)
    config.TEST_FOLDER.mkdir(parents=True, exist_ok=True)

    # Read before clearing: the caller may already have written the pair into
    # src/, in which case wiping first would delete the very files we copy.
    original_source = original.read_text(encoding="utf-8")
    candidate_source = candidate.read_text(encoding="utf-8")

    for stale in (*config.SOL_FOLDER.glob("*.sol"), *config.TEST_FOLDER.glob("*.sol")):
        stale.unlink()

    src_original = config.SOL_FOLDER / original.name
    src_candidate = config.SOL_FOLDER / candidate.name
    src_original.write_text(original_source, encoding="utf-8")
    src_candidate.write_text(candidate_source, encoding="utf-8")
    return src_original, src_candidate


def _contract_name(source: Path) -> str | None:
    match = re.search(r"\bcontract\s+(\w+)", source.read_text(encoding="utf-8"))
    return match.group(1) if match else None


# --- gates -------------------------------------------------------------------


def _parse_gas(suites: dict) -> list[GasEntry]:
    entries: list[GasEntry] = []
    for _, _, result in _iter_tests(suites):
        for log in result.get("decoded_logs") or []:
            if not log.startswith(GAS_LOG_PREFIX):
                continue
            _, signature, original, candidate, ok_a, ok_b = log.split("|")
            entries.append(
                GasEntry(
                    signature=signature,
                    original=int(original),
                    candidate=int(candidate),
                    original_ok=ok_a == "1",
                    candidate_ok=ok_b == "1",
                )
            )
    entries.sort(key=lambda e: e.signature)
    return entries


def _first_failure(suites: dict) -> tuple[str, str, str] | None:
    """(test name, reason, counterexample) for the first failing test."""
    for _, test_name, result in _iter_tests(suites):
        if result.get("status") == "Success":
            continue
        reason = result.get("reason") or "test failed without a reason"
        counterexample = result.get("counterexample")
        detail = ""
        if counterexample:
            single = counterexample.get("Single") if isinstance(counterexample, dict) else None
            payload = single or counterexample
            args = payload.get("args") if isinstance(payload, dict) else None
            calldata = payload.get("calldata") if isinstance(payload, dict) else None
            detail = f"args={args}" if args else f"calldata={calldata}"
        return test_name, reason, detail
    return None


def _run_equivalence(settings: dict) -> tuple[bool, dict | None]:
    timeout = settings["forge_timeout"]
    suites, raw = _forge_test_json(
        ["test", "--match-contract", "EquivalenceTest", "--fuzz-runs", str(settings["fuzz_runs"])],
        timeout,
    )

    if not suites:
        compiled = "Compiler run failed" not in raw and "Error (" not in raw
        return False, _failure(
            "compile_error" if not compiled else "test_error",
            "forge produced no test results",
            compile_ok=compiled,
            test="EquivalenceTest",
            trace=raw[-1500:],
        )

    total = sum(len(s.get("test_results") or {}) for s in suites.values())
    if total == 0:
        # A harness with no tests passes trivially. That is not evidence.
        return False, _failure(
            "harness_error",
            "the generated equivalence harness contains no tests",
            test="EquivalenceTest",
        )

    failure = _first_failure(suites)
    if failure:
        test_name, reason, detail = failure
        return False, _failure(
            "equivalence_error",
            reason,
            test=test_name,
            trace=detail or reason,
        )

    return True, None


def _run_gas_bench(settings: dict) -> tuple[list[GasEntry], dict | None]:
    timeout = settings["forge_timeout"]
    suites, raw = _forge_test_json(
        ["test", "--match-contract", "GasBench", "--isolate"], timeout
    )

    if not suites:
        return [], _failure(
            "gas_error",
            "the gas benchmark produced no results",
            test="GasBench",
            trace=raw[-1500:],
        )

    entries = _parse_gas(suites)
    failure = _first_failure(suites)

    if failure:
        test_name, reason, detail = failure
        return entries, _failure("gas_error", reason, test=test_name, trace=detail or reason)

    if not entries:
        return entries, _failure(
            "gas_error",
            "no gas measurements were produced",
            test="GasBench",
        )

    if settings.get("require_gas_improvement", True) and not any(e.improved for e in entries):
        detail = "; ".join(e.describe() for e in entries)
        return entries, _failure(
            "gas_error",
            "the candidate is not cheaper than the original on any function",
            test="GasBench",
            trace=detail,
        )

    return entries, None


# --- hevm (advisory) ---------------------------------------------------------


def _kill_process_tree(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_hevm(
    original_bin: Path, candidate_bin: Path, settings: dict
) -> tuple[bool, list[str], str]:
    """Return (equivalent, counterexamples, note).

    `note` is non-empty when the check could not reach a verdict; callers must
    treat that as "skipped", not as evidence against the candidate.
    """
    cmd = [
        "hevm", "equivalence",
        "--code-a-file", str(original_bin),
        "--code-b-file", str(candidate_bin),
        "--smttimeout", str(settings["hevm_solver_timeout"]),
        "--max-iterations", str(settings["hevm_max_iterations"]),
    ]

    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        stdout, _ = process.communicate(timeout=settings["hevm_timeout"])

        if process.returncode == 0:
            return True, [], ""

        counterexamples = [
            line.strip()
            for line in stdout.splitlines()
            if "not equal" in line.lower() or "counterexample" in line.lower()
        ]
        if counterexamples:
            return False, counterexamples, ""
        return False, [], "hevm exited non-zero without a counterexample"

    except subprocess.TimeoutExpired:
        if process:
            _kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return False, [], f"hevm timed out after {settings['hevm_timeout']}s"

    except FileNotFoundError:
        return False, [], "hevm is not installed; symbolic check skipped"

    except OSError as exc:
        if process:
            _kill_process_tree(process.pid)
        return False, [], f"hevm could not be run ({exc}); symbolic check skipped"


def _hevm_gate(
    original: artifacts.ContractArtifact,
    candidate: artifacts.ContractArtifact,
    settings: dict,
) -> tuple[dict | None, list[str]]:
    """Bytecode comes straight from the build artifact — no `forge inspect`
    output parsing, which changes shape between Foundry releases."""
    notes: list[str] = []

    original_code = original.deployed_bytecode.removeprefix("0x")
    candidate_code = candidate.deployed_bytecode.removeprefix("0x")

    if not original_code or not candidate_code:
        return None, ["one side has empty deployed bytecode; symbolic check skipped"]
    if original_code == candidate_code:
        return None, ["bytecode identical; symbolic check trivially satisfied"]

    original_bin = config.FOUNDRY_DIR / f"{original.name}.bin"
    candidate_bin = config.FOUNDRY_DIR / f"{candidate.name}.bin"
    try:
        original_bin.write_text(original_code)
        candidate_bin.write_text(candidate_code)
        equivalent, counterexamples, note = _run_hevm(original_bin, candidate_bin, settings)
    finally:
        original_bin.unlink(missing_ok=True)
        candidate_bin.unlink(missing_ok=True)

    if note:
        notes.append(note)
        return None, notes
    if equivalent:
        notes.append("hevm proved the two bytecodes equivalent")
        return None, notes

    return _failure(
        "symbolic_counterexample",
        counterexamples[0],
        test="hevm",
        trace="\n".join(counterexamples[:3]),
    ), notes


# --- entry point -------------------------------------------------------------


def validate(
    original_path: Path | str,
    candidate_path: Path | str,
    settings: dict | None = None,
    verbose: bool = True,
) -> ValidationResult:
    """Run every gate against a contract pair."""
    settings = settings or load_config()

    original_path = Path(original_path).resolve()
    candidate_path = Path(candidate_path).resolve()

    def say(message: str) -> None:
        if verbose:
            print(message)

    original_name = _contract_name(original_path)
    candidate_name = _contract_name(candidate_path)
    if not original_name:
        return ValidationResult(
            False,
            _failure("parse_error", f"no contract declared in {original_path}", compile_ok=False),
        )
    if not candidate_name:
        return ValidationResult(
            False,
            _failure("parse_error", f"no contract declared in {candidate_path}", compile_ok=False),
        )
    if original_name == candidate_name:
        return ValidationResult(
            False,
            _failure(
                "shape_error",
                f"original and candidate both declare `{original_name}`; the candidate must be "
                f"renamed or it overwrites the original and gets compared to itself",
                compile_ok=False,
            ),
        )

    say(
        f"[VALIDATE] {original_name} ({original_path.name}) vs "
        f"{candidate_name} ({candidate_path.name})"
    )

    src_original, src_candidate = prepare_workspace(original_path, candidate_path)

    say("[VALIDATE] Building...")
    returncode, stdout, stderr = run_forge(["build"], timeout=settings["forge_timeout"])
    if returncode != 0:
        return ValidationResult(
            False,
            _failure(
                "compile_error",
                "the candidate does not compile",
                compile_ok=False,
                trace=(stdout + stderr)[-1500:],
            ),
        )

    try:
        original_artifact = artifacts.load(src_original, original_name)
        candidate_artifact = artifacts.load(src_candidate, candidate_name)
    except artifacts.ArtifactError as exc:
        return ValidationResult(False, _failure("harness_error", str(exc)))

    say("[VALIDATE] Generating equivalence + gas harness...")
    try:
        harness = harness_generator.generate(
            original_artifact, candidate_artifact, settings["max_array_length"]
        )
    except harness_generator.GenerationError as exc:
        return ValidationResult(False, _failure("harness_error", str(exc)))

    for name, source in harness.files.items():
        (config.TEST_FOLDER / name).write_text(source, encoding="utf-8")
    say(f"[VALIDATE] Covering {len(harness.covered)} function(s): {', '.join(harness.covered)}")

    say(f"[VALIDATE] Differential fuzzing ({settings['fuzz_runs']} runs)...")
    equivalent, failure = _run_equivalence(settings)
    if not equivalent:
        return ValidationResult(False, failure)
    say("[VALIDATE] Equivalence holds.")

    say("[VALIDATE] Measuring gas...")
    gas, failure = _run_gas_bench(settings)
    if failure:
        return ValidationResult(False, failure, gas=gas)
    say("[VALIDATE] Gas gate passed:")
    if verbose:
        print(ValidationResult(True, gas=gas).gas_summary())

    notes: list[str] = []
    if settings.get("hevm_enabled", False):
        say("[VALIDATE] Running hevm symbolic check...")
        failure, notes = _hevm_gate(original_artifact, candidate_artifact, settings)
        for note in notes:
            say(f"[VALIDATE] {note}")
        if failure:
            return ValidationResult(False, failure, gas=gas, notes=notes)

    return ValidationResult(True, None, gas=gas, notes=notes)


# --- standalone CLI ----------------------------------------------------------

_USAGE = """Usage:
  python -m gas_optimizer.validator <original.sol> <candidate.sol>
  python -m gas_optimizer.validator --config
  python -m gas_optimizer.validator --hevm-enable | --hevm-disable
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print(_USAGE)
        return 1

    if argv[0] in ("--hevm-enable", "--hevm-disable"):
        settings = load_config()
        settings["hevm_enabled"] = argv[0] == "--hevm-enable"
        save_config(settings)
        print(f"hevm {'enabled' if settings['hevm_enabled'] else 'disabled'}")
        return 0

    if argv[0] == "--config":
        for key, value in load_config().items():
            print(f"  {key}: {_render(value)}")
        return 0

    if len(argv) < 2:
        print(_USAGE)
        return 1

    result = validate(argv[0], argv[1])

    if result.ok:
        print("\nACCEPTED: equivalent and cheaper")
        print(result.gas_summary())
        return 0

    print(f"\nREJECTED [{result.failure['type']}]: {result.failure['error']}")
    if result.failure.get("trace"):
        print(result.failure["trace"])
    if result.gas:
        print(result.gas_summary())
    return 1


if __name__ == "__main__":
    sys.exit(main())
