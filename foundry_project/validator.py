#!/usr/bin/env python3
"""
Solidity Equivalence Validator
Combines fuzz testing and symbolic execution (hevm) to verify contract equivalence.

Usage:
    python3 validator.py <ContractName>           # Run validation
    python3 validator.py --hevm-enable            # Enable hevm
    python3 validator.py --hevm-disable           # Disable hevm
    python3 validator.py --config                 # Show current config
    
Expects:
    - src/<ContractName>.sol
    - src/<ContractName>Candidate.sol
"""

import subprocess
import os
import sys
import re
import signal
from pathlib import Path
from typing import Tuple, List, Optional

CONFIG_FILE = "validatorConfig.txt"


def load_config(config_path: str = CONFIG_FILE) -> dict:
    """Load configuration from validatorConfig.txt"""
    config = {
        "fuzz_runs": 1000,
        "hevm_enabled": True,
        "hevm_timeout": 300,
        "hevm_solver_timeout": 30000,
        "hevm_max_iterations": 5,
        "max_array_length": 5
    }
    
    if not os.path.exists(config_path):
        print(f"[!] Config file not found: {config_path}, using defaults")
        return config
    
    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                if key in config:
                    # Handle boolean
                    if key == "hevm_enabled":
                        config[key] = value.lower() in ('true', '1', 'yes', 'on')
                    else:
                        try:
                            config[key] = int(value)
                        except ValueError:
                            config[key] = value
    
    return config


def save_config(config: dict, config_path: str = CONFIG_FILE):
    """Save configuration to validatorConfig.txt"""
    lines = []
    
    # Read existing file to preserve comments
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('#') or not stripped:
                    lines.append(line.rstrip('\n'))
                elif '=' in stripped:
                    key = stripped.split('=')[0].strip()
                    if key in config:
                        value = config[key]
                        if isinstance(value, bool):
                            value = 'true' if value else 'false'
                        lines.append(f"{key}={value}")
                    else:
                        lines.append(line.rstrip('\n'))
                else:
                    lines.append(line.rstrip('\n'))
    else:
        # Create new config file
        lines = [
            "# Validator Configuration",
            "# ======================",
            "",
            "# Number of fuzz test iterations",
            f"fuzz_runs={config['fuzz_runs']}",
            "",
            "# Hevm Configuration",
            "# ------------------",
            "# Enable/disable hevm symbolic verification (true/false)",
            f"hevm_enabled={'true' if config['hevm_enabled'] else 'false'}",
            "",
            "# Hevm timeout in seconds (overall process timeout)",
            f"hevm_timeout={config['hevm_timeout']}",
            "",
            "# Hevm solver timeout in milliseconds (Z3 SMT solver per-query timeout)",
            f"hevm_solver_timeout={config['hevm_solver_timeout']}",
            "",
            "# Max loop iterations for hevm (prevents infinite loop explosion)",
            f"hevm_max_iterations={config['hevm_max_iterations']}",
            "",
            "# Fuzz Test Configuration",
            "# -----------------------",
            "# Max array length for fuzz testing",
            f"max_array_length={config['max_array_length']}"
        ]
    
    with open(config_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def set_hevm_enabled(enabled: bool):
    """Enable or disable hevm in config"""
    config = load_config()
    config['hevm_enabled'] = enabled
    save_config(config)
    status = "ENABLED" if enabled else "DISABLED"
    print(f"✓ Hevm has been {status}")
    print(f"  Config saved to: {CONFIG_FILE}")


def show_config():
    """Display current configuration"""
    config = load_config()
    print("Current Configuration:")
    print("-" * 40)
    for key, value in config.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        print(f"  {key}: {value}")
    print("-" * 40)


def run_command(command_list: List[str], capture: bool = True) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)"""
    try:
        result = subprocess.run(
            command_list,
            capture_output=capture,
            text=True
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def generate_fuzz_tests(contract_name: str, config: dict) -> Tuple[bool, str]:
    """
    Generate fuzz tests using fuzz_test_generator.py
    Returns: (success, message)
    """
    original_path = f"src/{contract_name}.sol"
    candidate_path = f"src/{contract_name}Candidate.sol"
    output_path = "test/EquivalenceTest.t.sol"
    
    # Ensure test directory exists
    os.makedirs("test", exist_ok=True)
    
    # Check source files exist
    if not os.path.exists(original_path):
        return False, f"Original contract not found: {original_path}"
    if not os.path.exists(candidate_path):
        return False, f"Candidate contract not found: {candidate_path}"
    
    print(f"[*] Generating fuzz tests...")
    print(f"    Original:  {original_path}")
    print(f"    Candidate: {candidate_path}")
    print(f"    Output:    {output_path}")
    
    # Run the generator
    cmd = [
        "python3", "fuzz_test_generator.py",
        original_path,
        candidate_path,
        "-o", output_path,
        "--original-contract", contract_name,
        "--candidate-contract", f"{contract_name}Candidate",
        "--max-array-length", str(config.get("max_array_length", 5))
    ]
    
    returncode, stdout, stderr = run_command(cmd)
    
    if returncode != 0:
        return False, f"Failed to generate tests:\n{stderr}\n{stdout}"
    
    print(f"    ✓ Generated: {output_path}")
    return True, output_path


def run_fuzz_tests(fuzz_runs: int) -> Tuple[bool, List[str]]:
    """
    Run fuzz tests with Foundry.
    Returns: (success, counterexamples)
    """
    print(f"\n[*] Running fuzz tests ({fuzz_runs} runs)...")
    print("-" * 50)
    
    cmd = [
        "forge", "test",
        "--match-contract", "EquivalenceTest",
        "--fuzz-runs", str(fuzz_runs),
        "-vvv"
    ]
    
    returncode, stdout, stderr = run_command(cmd)
    
    # Print output
    print(stdout)
    if stderr:
        print(stderr)
    
    if returncode == 0:
        print("\n    ✓ All fuzz tests passed")
        return True, []
    
    # Parse counterexamples from output
    counterexamples = parse_fuzz_counterexamples(stdout + stderr)
    
    print(f"\n    ✗ Fuzz tests failed")
    return False, counterexamples


def parse_fuzz_counterexamples(output: str, max_examples: int = 5) -> List[str]:
    """Parse counterexamples from Foundry fuzz test output."""
    counterexamples = []
    
    # Look for counterexample patterns in Foundry output
    pattern = r'counterexample:.*?args=\[([^\]]+)\]'
    matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)
    
    for match in matches[:max_examples]:
        counterexamples.append(f"Fuzz counterexample: args=[{match}]")
    
    # Also look for assertion failures
    fail_pattern = r'\[FAIL[^\]]*\].*?(?=\n\n|\[FAIL|\Z)'
    fail_matches = re.findall(fail_pattern, output, re.DOTALL)
    
    for match in fail_matches[:max_examples - len(counterexamples)]:
        if match.strip() and match.strip() not in counterexamples:
            counterexamples.append(match.strip()[:500])
    
    return counterexamples[:max_examples]


def get_bytecode(contract_path: str, contract_name: str) -> str:
    """Extract deployed bytecode for a contract."""
    cmd = ["forge", "inspect", f"{contract_path}:{contract_name}", "deployedBytecode"]
    returncode, stdout, stderr = run_command(cmd)
    
    if returncode != 0:
        raise Exception(f"Failed to get bytecode: {stderr}")
    
    return stdout.strip()


def kill_process_tree(pid: int):
    """Kill a process and all its children."""
    try:
        # Try to kill the process group
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        # Also try direct kill
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_hevm_equivalence(original_bin: str, candidate_bin: str, config: dict) -> Tuple[bool, List[str]]:
    """
    Run hevm equivalence check with proper timeout and solver limits.
    Returns: (is_equivalent, counterexamples)
    - (True, []) if equivalent
    - (False, [counterexamples]) if not equivalent
    - (False, []) if timeout (no counterexamples found)
    """
    timeout = config.get("hevm_timeout", 300)
    solver_timeout = config.get("hevm_solver_timeout", 30000)
    max_iterations = config.get("hevm_max_iterations", 5)
    
    cmd = [
        "hevm", "equivalence",
        "--code-a-file", original_bin,
        "--code-b-file", candidate_bin,
        "--smttimeout", str(solver_timeout),
        "--max-iterations", str(max_iterations),
    ]
    
    print(f"\n[*] Running hevm symbolic equivalence check...")
    print(f"    Timeout: {timeout}s")
    print(f"    Solver timeout: {solver_timeout}ms")
    print(f"    Max iterations: {max_iterations}")
    print("-" * 50)
    
    process = None
    try:
        # Start process in new process group so we can kill all children
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid  # Create new process group
        )
        
        stdout, _ = process.communicate(timeout=timeout)
        print(stdout)
        
        if process.returncode == 0:
            return True, []
        else:
            counterexamples = parse_hevm_counterexamples(stdout)
            return False, counterexamples
            
    except subprocess.TimeoutExpired:
        print(f"\n⏰ TIMEOUT: hevm stopped after {timeout}s")
        print("The symbolic execution could not complete in time.")
        print("This may happen with complex contracts (loops, math, etc.)")
        
        if process:
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except:
                pass
        
        return False, []  # Timeout - no counterexamples found
        
    except FileNotFoundError:
        print("\n❌ ERROR: hevm not found!")
        print("Please install hevm: https://github.com/ethereum/hevm")
        return False, ["hevm not installed"]
        
    except Exception as e:
        if process:
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except:
                pass
        return False, [f"Error: {str(e)}"]


def parse_hevm_counterexamples(output: str, max_examples: int = 5) -> List[str]:
    """Parse counterexamples from hevm output."""
    counterexamples = []
    
    lines = output.split('\n')
    current_example = []
    in_counterexample = False
    
    for line in lines:
        lower_line = line.lower()
        if any(marker in lower_line for marker in ['counterexample', 'not equal', 'calldata:']):
            if current_example and in_counterexample:
                counterexamples.append('\n'.join(current_example))
                if len(counterexamples) >= max_examples:
                    break
                current_example = []
            in_counterexample = True
        
        if in_counterexample:
            current_example.append(line)
        
        if in_counterexample and line.strip() == '' and current_example:
            counterexamples.append('\n'.join(current_example))
            current_example = []
            in_counterexample = False
            if len(counterexamples) >= max_examples:
                break
    
    if current_example and len(counterexamples) < max_examples:
        counterexamples.append('\n'.join(current_example))
    
    # If no structured counterexamples, look for "Not equal" or error messages
    if not counterexamples:
        for line in lines:
            if 'not equal' in line.lower() or 'different' in line.lower():
                counterexamples.append(line.strip())
                if len(counterexamples) >= max_examples:
                    break
    
    return counterexamples[:max_examples]


def run_hevm_validation(contract_name: str, config: dict) -> Tuple[bool, List[str]]:
    """
    Full hevm validation: compile, extract bytecode, run equivalence check.
    Returns: (is_equivalent, counterexamples)
    """
    original_sol = f"src/{contract_name}.sol"
    candidate_sol = f"src/{contract_name}Candidate.sol"
    original_bin = f"{contract_name}.bin"
    candidate_bin = f"{contract_name}Candidate.bin"
    
    try:
        # Compile
        print("\n[*] Compiling contracts...")
        returncode, stdout, stderr = run_command(["forge", "build"])
        if returncode != 0:
            return False, [f"Compilation failed: {stderr}"]
        print("    ✓ Compilation successful")
        
        # Extract bytecode
        print("[*] Extracting bytecode...")
        original_code = get_bytecode(original_sol, contract_name)
        candidate_code = get_bytecode(candidate_sol, f"{contract_name}Candidate")
        
        if not original_code or original_code == "0x":
            return False, [f"Empty bytecode for {contract_name}"]
        if not candidate_code or candidate_code == "0x":
            return False, [f"Empty bytecode for {contract_name}Candidate"]
        
        # Save bytecode
        with open(original_bin, "w") as f:
            f.write(original_code)
        with open(candidate_bin, "w") as f:
            f.write(candidate_code)
        print(f"    ✓ Bytecode saved")
        
        # Quick check: identical bytecode
        if original_code == candidate_code:
            print("\n✅ Bytecodes are identical - contracts are equivalent.")
            cleanup_files(original_bin, candidate_bin)
            return True, []
        
        print("[*] Bytecodes differ - running symbolic check...")
        
        # Run hevm
        is_equivalent, counterexamples = run_hevm_equivalence(original_bin, candidate_bin, config)
        
        # Cleanup
        cleanup_files(original_bin, candidate_bin)
        
        return is_equivalent, counterexamples
        
    except Exception as e:
        cleanup_files(original_bin, candidate_bin)
        return False, [f"Error: {str(e)}"]


def cleanup_files(*files):
    """Remove temporary files."""
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass


def Validate(contract_name: str, candidate_name: str = None) -> Tuple[bool, List[str]]:
    """
    Main validation function.
    
    Args:
        contract_name: Name of the original contract (e.g., "ERC20")
        candidate_name: Name of the candidate contract (default: "{contract_name}Candidate")
    
    Returns:
        Tuple[bool, List[str]]: (is_equivalent, counterexamples)
        - (True, []) if equivalent
        - (False, [counterexamples]) if not equivalent (with up to 5 counterexamples)
        - (False, []) if hevm timeout (no counterexamples found)
    """
    if candidate_name is None:
        candidate_name = f"{contract_name}Candidate"
    
    print("=" * 60)
    print("SOLIDITY EQUIVALENCE VALIDATOR")
    print("=" * 60)
    print(f"\nContract: {contract_name}")
    print(f"Candidate: {candidate_name}")
    
    # Load config
    config = load_config()
    print(f"\nConfiguration:")
    print(f"  - Fuzz runs: {config['fuzz_runs']}")
    print(f"  - Hevm enabled: {config['hevm_enabled']}")
    if config['hevm_enabled']:
        print(f"  - Hevm timeout: {config['hevm_timeout']}s")
        print(f"  - Hevm solver timeout: {config['hevm_solver_timeout']}ms")
        print(f"  - Hevm max iterations: {config['hevm_max_iterations']}")
    print(f"  - Max array length: {config['max_array_length']}")
    print()
    
    # Step 1: Generate fuzz tests
    success, result = generate_fuzz_tests(contract_name, config)
    if not success:
        print(f"\n❌ Failed to generate fuzz tests: {result}")
        return False, [result]
    
    # Step 2: Run fuzz tests
    success, counterexamples = run_fuzz_tests(config["fuzz_runs"])
    if not success:
        print("\n" + "=" * 60)
        print("RESULT: FAILED (Fuzz testing found differences)")
        print("=" * 60)
        return False, counterexamples[:5]
    
    # Step 3: Run hevm validation (if enabled)
    if not config["hevm_enabled"]:
        print("\n[*] Hevm is disabled, skipping symbolic verification")
        print("\n" + "=" * 60)
        print("RESULT: PASSED (Fuzz tests only) ✓")
        print("Note: Hevm symbolic verification was skipped.")
        print("=" * 60)
        return True, []
    
    print("\n[*] Fuzz tests passed, proceeding to symbolic verification...")
    
    success, counterexamples = run_hevm_validation(contract_name, config)
    
    print("\n" + "=" * 60)
    if success:
        print("RESULT: EQUIVALENT ✅")
        print("Both fuzz testing and symbolic execution confirm equivalence.")
    elif counterexamples:
        print("RESULT: NOT EQUIVALENT ❌")
        print(f"Found {len(counterexamples)} counterexample(s).")
    else:
        print("RESULT: INCONCLUSIVE ⏰")
        print("Fuzz tests passed but hevm timed out.")
        print("Contracts may be equivalent, but could not prove it symbolically.")
    print("=" * 60)
    
    return success, counterexamples[:5]


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 validator.py <ContractName>    Run validation")
        print("  python3 validator.py --hevm-enable     Enable hevm")
        print("  python3 validator.py --hevm-disable    Disable hevm")
        print("  python3 validator.py --config          Show current config")
        print()
        print("Expects:")
        print("  - src/<ContractName>.sol")
        print("  - src/<ContractName>Candidate.sol")
        print()
        print("Example: python3 validator.py ERC20")
        sys.exit(1)
    
    arg = sys.argv[1]
    
    # Handle commands
    if arg == "--hevm-enable":
        set_hevm_enabled(True)
        sys.exit(0)
    elif arg == "--hevm-disable":
        set_hevm_enabled(False)
        sys.exit(0)
    elif arg == "--config":
        show_config()
        sys.exit(0)
    elif arg.startswith("--"):
        print(f"Unknown command: {arg}")
        sys.exit(1)
    
    # Run validation
    contract_name = arg
    
    is_equivalent, counterexamples = Validate(contract_name)
    
    # Print counterexamples if any
    if counterexamples:
        print("\nCOUNTEREXAMPLES:")
        print("-" * 40)
        for i, ce in enumerate(counterexamples, 1):
            print(f"\n[{i}] {ce}")
    
    sys.exit(0 if is_equivalent else 1)


if __name__ == "__main__":
    main()
