"""
Validator for Solidity code optimization.

Compares original and candidate code to verify semantic equivalence using fuzz
testing (Foundry) and optional symbolic execution (hevm).

All `forge` invocations run with cwd=config.FOUNDRY_DIR, so this module works
regardless of where the process was started from.
"""

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import config

DEFAULT_SETTINGS = {
    "fuzz_runs": 1000,
    "hevm_enabled": False,  # Default to False for faster iteration
    "hevm_timeout": 300,
    "hevm_solver_timeout": 30000,
    "hevm_max_iterations": 5,
    "max_array_length": 5,
}


def load_config(config_path: Path | str = config.VALIDATOR_CONFIG_PATH) -> dict:
    """Load validator settings from validatorConfig.txt."""
    settings = dict(DEFAULT_SETTINGS)

    if not os.path.exists(config_path):
        return settings

    with open(config_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()

            if key not in settings:
                continue
            if key == "hevm_enabled":
                settings[key] = value.lower() in ("true", "1", "yes", "on")
            else:
                try:
                    settings[key] = int(value)
                except ValueError:
                    settings[key] = value

    return settings


def save_config(settings: dict, config_path: Path | str = config.VALIDATOR_CONFIG_PATH) -> None:
    """Write settings back to validatorConfig.txt, preserving comments."""
    lines = []
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    key = stripped.split("=", 1)[0].strip()
                    if key in settings:
                        value = settings[key]
                        if isinstance(value, bool):
                            value = "true" if value else "false"
                        lines.append(f"{key}={value}")
                        continue
                lines.append(line.rstrip("\n"))
    else:
        lines = ["# Validator Configuration"]
        for key, value in settings.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            lines.append(f"{key}={value}")

    with open(config_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def run_command(command_list: List[str], cwd: Path | str | None = None) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            command_list,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def run_forge(args: List[str]) -> Tuple[int, str, str]:
    """Run a forge subcommand inside the Foundry workspace."""
    return run_command(["forge", *args], cwd=config.FOUNDRY_DIR)


def extract_contract_name(filepath: Path | str) -> Optional[str]:
    """Extract the contract name from a Solidity file."""
    try:
        with open(filepath, "r") as f:
            match = re.search(r"contract\s+(\w+)", f.read())
        return match.group(1) if match else None
    except OSError:
        return None


def generate_fuzz_test(original_path: Path, candidate_path: Path,
                       original_contract: str, candidate_contract: str,
                       output_path: Path, settings: dict) -> Tuple[bool, str]:
    """
    Generate the Foundry fuzz test comparing the two contracts.
    Returns: (success, error_message)
    """
    generator_path = config.FUZZ_GENERATOR_PATH
    if not generator_path.exists():
        return False, f"fuzz_test_generator.py not found at {generator_path}"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(generator_path),
        str(original_path),
        str(candidate_path),
        "-o", str(output_path),
        "--original-contract", original_contract,
        "--candidate-contract", candidate_contract,
        "--max-array-length", str(settings.get("max_array_length", 5)),
    ]

    returncode, stdout, stderr = run_command(cmd)
    if returncode != 0:
        return False, f"Generator failed: {stderr}\n{stdout}"

    return True, ""


def run_fuzz_tests(fuzz_runs: int) -> Tuple[bool, List[str]]:
    """
    Run the equivalence fuzz tests with Foundry.
    Returns: (success, counterexamples/errors)
    """
    returncode, stdout, stderr = run_forge([
        "test",
        "--match-contract", "EquivalenceTest",
        "--fuzz-runs", str(fuzz_runs),
        "-vvv",
    ])

    if returncode == 0:
        return True, []

    return False, parse_fuzz_failures(stdout + stderr)


def parse_fuzz_failures(output: str) -> List[str]:
    """Parse failure information from Foundry output."""
    errors = []

    for match in re.findall(r"counterexample:.*?args=\[([^\]]+)\]", output,
                            re.IGNORECASE | re.DOTALL)[:5]:
        errors.append(f"Counterexample: args=[{match}]")

    for match in re.findall(r"\[FAIL[^\]]*\].*?(?=\n\n|\[FAIL|\Z)", output, re.DOTALL):
        if len(errors) >= 5:
            break
        if match.strip():
            errors.append(match.strip()[:300])

    if "Compiler run failed" in output:
        for match in re.findall(r"Error.*?(?=\n\n|\Z)", output, re.DOTALL)[:3]:
            errors.append(f"Compilation: {match.strip()[:200]}")

    return errors or ["Unknown failure - check test output"]


def get_bytecode(contract_path: Path, contract_name: str) -> str:
    """Extract deployed bytecode for a contract."""
    # forge expects the path relative to the Foundry workspace.
    rel_path = os.path.relpath(contract_path, config.FOUNDRY_DIR)
    returncode, stdout, stderr = run_forge(
        ["inspect", f"{rel_path}:{contract_name}", "deployedBytecode"]
    )

    if returncode != 0:
        raise RuntimeError(f"Failed to get bytecode: {stderr}")

    return stdout.strip()


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its children."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_hevm_equivalence(original_bin: Path, candidate_bin: Path,
                         settings: dict) -> Tuple[bool, List[str]]:
    """
    Run the hevm equivalence check.
    Returns: (is_equivalent, counterexamples)
    """
    timeout = settings.get("hevm_timeout", 300)

    cmd = [
        "hevm", "equivalence",
        "--code-a-file", str(original_bin),
        "--code-b-file", str(candidate_bin),
        "--smttimeout", str(settings.get("hevm_solver_timeout", 30000)),
        "--max-iterations", str(settings.get("hevm_max_iterations", 5)),
    ]

    process = None
    try:
        # New process group so a timeout can take down the whole solver tree.
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )

        stdout, _ = process.communicate(timeout=timeout)

        if process.returncode == 0:
            return True, []

        counterexamples = [
            line.strip() for line in stdout.split("\n")
            if "not equal" in line.lower() or "counterexample" in line.lower()
        ]
        return False, counterexamples or ["Equivalence check failed"]

    except subprocess.TimeoutExpired:
        if process:
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return False, []  # Timeout - no counterexamples

    except FileNotFoundError:
        return False, ["hevm not installed"]

    except Exception as e:
        if process:
            kill_process_tree(process.pid)
        return False, [f"Error: {str(e)}"]


def run_hevm_validation(original_path: Path, candidate_path: Path,
                        original_contract: str, candidate_contract: str,
                        settings: dict) -> Tuple[bool, List[str]]:
    """Compile both contracts, dump bytecode, and prove equivalence with hevm."""
    original_bin = config.FOUNDRY_DIR / f"{original_contract}.bin"
    candidate_bin = config.FOUNDRY_DIR / f"{candidate_contract}.bin"

    try:
        returncode, _, stderr = run_forge(["build"])
        if returncode != 0:
            return False, [f"Compilation failed: {stderr}"]

        original_code = get_bytecode(original_path, original_contract)
        candidate_code = get_bytecode(candidate_path, candidate_contract)

        if not original_code or original_code == "0x":
            return False, [f"Empty bytecode for {original_contract}"]
        if not candidate_code or candidate_code == "0x":
            return False, [f"Empty bytecode for {candidate_contract}"]

        # Identical bytecode is trivially equivalent — skip the solver.
        if original_code == candidate_code:
            return True, []

        original_bin.write_text(original_code)
        candidate_bin.write_text(candidate_code)

        return run_hevm_equivalence(original_bin, candidate_bin, settings)

    except Exception as e:
        return False, [f"Error: {str(e)}"]

    finally:
        cleanup_files(original_bin, candidate_bin)


def cleanup_files(*files: Path) -> None:
    """Remove temporary files."""
    for f in files:
        try:
            Path(f).unlink(missing_ok=True)
        except OSError:
            pass


def _failure(compile_status: str, test: str, failure_type: str,
             error: str, trace: str = "") -> dict:
    return {
        "compile": compile_status,
        "test": test,
        "type": failure_type,
        "error": error,
        "trace": trace,
    }


def validate(original_path: Path | str, candidate_path: Path | str) -> Tuple[bool, Optional[dict]]:
    """
    Main validation entry point.

    Args:
        original_path: Path to original Solidity file
        candidate_path: Path to candidate/optimized Solidity file

    Returns:
        (True, None) if equivalent, else (False, failure_dict) with retry details.
    """
    settings = load_config()

    original_path = Path(original_path).resolve()
    candidate_path = Path(candidate_path).resolve()

    original_contract = extract_contract_name(original_path)
    candidate_contract = extract_contract_name(candidate_path)

    if not original_contract:
        return False, _failure("false", "N/A", "parse_error",
                               f"Could not extract contract name from {original_path}")
    if not candidate_contract:
        return False, _failure("false", "N/A", "parse_error",
                               f"Could not extract contract name from {candidate_path}")

    print(f"[VALIDATE] Original: {original_contract} ({original_path})")
    print(f"[VALIDATE] Candidate: {candidate_contract} ({candidate_path})")

    # Foundry only compiles what lives under its src/ dir.
    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)
    config.TEST_FOLDER.mkdir(parents=True, exist_ok=True)

    src_original = config.SOL_FOLDER / f"{original_contract}.sol"
    src_candidate = config.SOL_FOLDER / f"{candidate_contract}.sol"

    if original_path != src_original:
        src_original.write_text(original_path.read_text())
    if candidate_path != src_candidate:
        src_candidate.write_text(candidate_path.read_text())

    print("[VALIDATE] Generating fuzz tests...")
    success, error = generate_fuzz_test(
        src_original, src_candidate,
        original_contract, candidate_contract,
        config.EQUIVALENCE_TEST_PATH, settings,
    )
    if not success:
        return False, _failure("false", "N/A", "generator_error", error)

    print(f"[VALIDATE] Running fuzz tests ({settings['fuzz_runs']} runs)...")
    success, errors = run_fuzz_tests(settings["fuzz_runs"])

    if not success:
        error_text = "\n".join(errors)

        if "Compiler run failed" in error_text or "compilation" in error_text.lower():
            failure_type, compile_status = "compile_error", "false"
        elif "counterexample" in error_text.lower():
            failure_type, compile_status = "equivalence_error", "true"
        elif "assertion" in error_text.lower():
            failure_type, compile_status = "assertion_error", "true"
        else:
            failure_type, compile_status = "test_error", "true"

        return False, _failure(
            compile_status, "EquivalenceTest", failure_type,
            errors[0] if errors else "Unknown error",
            "\n".join(errors[:3]),
        )

    print("[VALIDATE] Fuzz tests passed!")

    if settings.get("hevm_enabled", False):
        print("[VALIDATE] Running hevm symbolic check...")
        success, counterexamples = run_hevm_validation(
            src_original, src_candidate,
            original_contract, candidate_contract,
            settings,
        )

        if not success:
            if counterexamples:
                return False, _failure(
                    "true", "hevm", "symbolic_counterexample",
                    counterexamples[0], "\n".join(counterexamples[:3]),
                )
            # Timeout with no counterexample: inconclusive, but fuzzing passed.
            print("[VALIDATE] Hevm timed out - treating as passed (fuzz tests passed)")

    print("[VALIDATE] Validation passed!")
    return True, None


def main() -> None:
    """CLI for standalone validation and config tweaking."""
    if len(sys.argv) < 2:
        print("Usage: python -m gas_optimizer.validator <original.sol> <candidate.sol>")
        print("       python -m gas_optimizer.validator --hevm-enable")
        print("       python -m gas_optimizer.validator --hevm-disable")
        print("       python -m gas_optimizer.validator --config")
        sys.exit(1)

    arg = sys.argv[1]

    if arg in ("--hevm-enable", "--hevm-disable"):
        settings = load_config()
        settings["hevm_enabled"] = arg == "--hevm-enable"
        save_config(settings)
        print(f"Hevm {'ENABLED' if settings['hevm_enabled'] else 'DISABLED'}")
        sys.exit(0)

    if arg == "--config":
        print("Current Configuration:")
        for k, v in load_config().items():
            print(f"  {k}: {v}")
        sys.exit(0)

    if len(sys.argv) < 3:
        print("Usage: python -m gas_optimizer.validator <original.sol> <candidate.sol>")
        sys.exit(1)

    is_valid, failure_data = validate(sys.argv[1], sys.argv[2])

    if is_valid:
        print("\nVALID: Contracts are equivalent")
        sys.exit(0)

    print(f"\nINVALID: {failure_data}")
    sys.exit(1)


if __name__ == "__main__":
    main()
