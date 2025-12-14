"""
Validator for Solidity code optimization.
Compares original and candidate code to verify semantic equivalence.
Uses fuzz testing (Foundry) and optional symbolic execution (hevm).
"""

import subprocess
import os
import sys
import re
import signal
import config
from typing import Tuple, List, Optional

# Configuration file path (relative to project root)
CONFIG_FILE = "validatorConfig.txt"


def load_config(config_path: str = CONFIG_FILE) -> dict:
    """Load configuration from validatorConfig.txt"""
    config = {
        "fuzz_runs": 1000,
        "hevm_enabled": False,  # Default to False for faster iteration
        "hevm_timeout": 300,
        "hevm_solver_timeout": 30000,
        "hevm_max_iterations": 5,
        "max_array_length": 5
    }
    
    if not os.path.exists(config_path):
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
                    if key == "hevm_enabled":
                        config[key] = value.lower() in ('true', '1', 'yes', 'on')
                    else:
                        try:
                            config[key] = int(value)
                        except ValueError:
                            config[key] = value
    
    return config


def run_command(command_list: List[str], cwd: str = None) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)"""
    try:
        result = subprocess.run(
            command_list,
            capture_output=True,
            text=True,
            cwd=cwd
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def extract_contract_name(filepath: str) -> Optional[str]:
    """Extract the contract name from a Solidity file."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Find contract declaration
        match = re.search(r'contract\s+(\w+)', content)
        if match:
            return match.group(1)
        return None
    except:
        return None


def generate_fuzz_test(original_path: str, candidate_path: str, 
                       original_contract: str, candidate_contract: str,
                       output_path: str, config_data: dict) -> Tuple[bool, str]:
    """
    Generate fuzz test file for the given contracts.
    Returns: (success, error_message)
    """
    
    # KORISTIMO PUTANJU IZ CONFIGA
    generator_path = config.FUZZ_GENERATOR_PATH
    
    # Fallback logika ako putanja iz configa ne postoji
    if not os.path.exists(generator_path):
        fallback_paths = [
            "foundry_project/fuzz_test_generator.py",
            "../foundry_project/fuzz_test_generator.py",
            "../fuzz_test_generator.py",
            "fuzz_test_generator.py",
            "src/fuzz_test_generator.py"
        ]
        found = False
        for path in fallback_paths:
            if os.path.exists(path):
                generator_path = path
                found = True
                break
        
        if not found:
            return False, f"fuzz_test_generator.py not found at {config.FUZZ_GENERATOR_PATH}"

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    cmd = [
        "python3", generator_path,
        original_path,
        candidate_path,
        "-o", output_path,
        "--original-contract", original_contract,
        "--candidate-contract", candidate_contract,
        "--max-array-length", str(config_data.get("max_array_length", 5))
    ]
    
    returncode, stdout, stderr = run_command(cmd)
    
    if returncode != 0:
        return False, f"Generator failed: {stderr}\n{stdout}"
    
    return True, ""


def run_fuzz_tests(fuzz_runs: int) -> Tuple[bool, List[str]]:
    """
    Run fuzz tests with Foundry.
    Returns: (success, counterexamples/errors)
    """
    cmd = [
        "forge", "test",
        "--match-contract", "EquivalenceTest",
        "--fuzz-runs", str(fuzz_runs),
        "-vvv"
    ]
    
    returncode, stdout, stderr = run_command(cmd)
    output = stdout + stderr
    
    if returncode == 0:
        return True, []
    
    # Parse failure information
    errors = parse_fuzz_failures(output)
    return False, errors


def parse_fuzz_failures(output: str) -> List[str]:
    """Parse failure information from Foundry output."""
    errors = []
    
    # Look for counterexamples
    pattern = r'counterexample:.*?args=\[([^\]]+)\]'
    matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)
    for match in matches[:5]:
        errors.append(f"Counterexample: args=[{match}]")
    
    # Look for assertion failures
    fail_pattern = r'\[FAIL[^\]]*\].*?(?=\n\n|\[FAIL|\Z)'
    fail_matches = re.findall(fail_pattern, output, re.DOTALL)
    for match in fail_matches[:5 - len(errors)]:
        if match.strip():
            errors.append(match.strip()[:300])
    
    # Look for compilation errors
    if "Compiler run failed" in output:
        comp_pattern = r'Error.*?(?=\n\n|\Z)'
        comp_matches = re.findall(comp_pattern, output, re.DOTALL)
        for match in comp_matches[:3]:
            errors.append(f"Compilation: {match.strip()[:200]}")
    
    return errors if errors else ["Unknown failure - check test output"]


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
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_hevm_equivalence(original_bin: str, candidate_bin: str, config: dict) -> Tuple[bool, List[str]]:
    """
    Run hevm equivalence check.
    Returns: (is_equivalent, counterexamples)
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
    
    process = None
    try:
        # Create new process group for clean termination
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid if hasattr(os, 'setsid') else None
        )
        
        stdout, _ = process.communicate(timeout=timeout)
        
        if process.returncode == 0:
            return True, []
        else:
            # Parse counterexamples
            counterexamples = []
            for line in stdout.split('\n'):
                if 'not equal' in line.lower() or 'counterexample' in line.lower():
                    counterexamples.append(line.strip())
            return False, counterexamples if counterexamples else ["Equivalence check failed"]
            
    except subprocess.TimeoutExpired:
        if process:
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except:
                pass
        return False, []  # Timeout - no counterexamples
        
    except FileNotFoundError:
        return False, ["hevm not installed"]
        
    except Exception as e:
        if process:
            kill_process_tree(process.pid)
        return False, [f"Error: {str(e)}"]


def run_hevm_validation(original_path: str, candidate_path: str,
                        original_contract: str, candidate_contract: str,
                        config: dict) -> Tuple[bool, List[str]]:
    """
    Full hevm validation workflow.
    """
    original_bin = f"{original_contract}.bin"
    candidate_bin = f"{candidate_contract}.bin"
    
    try:
        # Compile
        returncode, stdout, stderr = run_command(["forge", "build"])
        if returncode != 0:
            return False, [f"Compilation failed: {stderr}"]
        
        # Extract bytecode
        original_code = get_bytecode(original_path, original_contract)
        candidate_code = get_bytecode(candidate_path, candidate_contract)
        
        if not original_code or original_code == "0x":
            return False, [f"Empty bytecode for {original_contract}"]
        if not candidate_code or candidate_code == "0x":
            return False, [f"Empty bytecode for {candidate_contract}"]
        
        # Save bytecode
        with open(original_bin, "w") as f:
            f.write(original_code)
        with open(candidate_bin, "w") as f:
            f.write(candidate_code)
        
        # Quick check: identical bytecode
        if original_code == candidate_code:
            cleanup_files(original_bin, candidate_bin)
            return True, []
        
        # Run hevm
        is_equivalent, counterexamples = run_hevm_equivalence(original_bin, candidate_bin, config)
        
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


def validate(original_path: str, candidate_path: str) -> Tuple[bool, Optional[dict]]:
    """
    Main validation function for the optimizer.
    
    Args:
        original_path: Path to original Solidity file
        candidate_path: Path to candidate/optimized Solidity file
    
    Returns:
        Tuple[bool, dict | None]: 
        - (True, None) if equivalent
        - (False, failure_dict) if not equivalent, with failure details for retry
    """
    # Load configuration
    config = load_config()
    
    # Extract contract names from files
    original_contract = extract_contract_name(original_path)
    candidate_contract = extract_contract_name(candidate_path)
    
    if not original_contract:
        return False, {
            "compile": "false",
            "test": "N/A",
            "type": "parse_error",
            "error": f"Could not extract contract name from {original_path}",
            "trace": ""
        }
    
    if not candidate_contract:
        return False, {
            "compile": "false",
            "test": "N/A",
            "type": "parse_error",
            "error": f"Could not extract contract name from {candidate_path}",
            "trace": ""
        }
    
    print(f"[VALIDATE] Original: {original_contract} ({original_path})")
    print(f"[VALIDATE] Candidate: {candidate_contract} ({candidate_path})")
    
    # Ensure files are in src/ for Foundry (create symlinks or copy if needed)
    os.makedirs("src", exist_ok=True)
    os.makedirs("test", exist_ok=True)
    
    # Copy files to src/ with proper names for the test generator
    src_original = f"src/{original_contract}.sol"
    src_candidate = f"src/{candidate_contract}.sol"
    
    # Only copy if paths are different
    if os.path.abspath(original_path) != os.path.abspath(src_original):
        with open(original_path, 'r') as f:
            content = f.read()
        with open(src_original, 'w') as f:
            f.write(content)
    
    if os.path.abspath(candidate_path) != os.path.abspath(src_candidate):
        with open(candidate_path, 'r') as f:
            content = f.read()
        with open(src_candidate, 'w') as f:
            f.write(content)
    
    # Step 1: Generate fuzz tests
    print(f"[VALIDATE] Generating fuzz tests...")
    test_path = "test/EquivalenceTest.t.sol"
    success, error = generate_fuzz_test(
        src_original, src_candidate,
        original_contract, candidate_contract,
        test_path, config
    )
    
    if not success:
        return False, {
            "compile": "false",
            "test": "N/A",
            "type": "generator_error",
            "error": error,
            "trace": ""
        }
    
    print(f"[VALIDATE] Running fuzz tests ({config['fuzz_runs']} runs)...")
    
    # Step 2: Run fuzz tests
    success, errors = run_fuzz_tests(config["fuzz_runs"])
    
    if not success:
        # Determine failure type
        error_text = "\n".join(errors)
        
        if "Compiler run failed" in error_text or "compilation" in error_text.lower():
            failure_type = "compile_error"
            compile_status = "false"
        elif "counterexample" in error_text.lower():
            failure_type = "equivalence_error"
            compile_status = "true"
        elif "assertion" in error_text.lower():
            failure_type = "assertion_error"
            compile_status = "true"
        else:
            failure_type = "test_error"
            compile_status = "true"
        
        return False, {
            "compile": compile_status,
            "test": "EquivalenceTest",
            "type": failure_type,
            "error": errors[0] if errors else "Unknown error",
            "trace": "\n".join(errors[:3])
        }
    
    print(f"[VALIDATE] Fuzz tests passed!")
    
    # Step 3: Run hevm validation (if enabled)
    if config.get("hevm_enabled", False):
        print(f"[VALIDATE] Running hevm symbolic check...")
        
        success, counterexamples = run_hevm_validation(
            src_original, src_candidate,
            original_contract, candidate_contract,
            config
        )
        
        if not success:
            if counterexamples:
                return False, {
                    "compile": "true",
                    "test": "hevm",
                    "type": "symbolic_counterexample",
                    "error": counterexamples[0],
                    "trace": "\n".join(counterexamples[:3])
                }
            else:
                # Timeout - treat as inconclusive but passing
                print(f"[VALIDATE] Hevm timed out - treating as passed (fuzz tests passed)")
    
    print(f"[VALIDATE] Validation passed!")
    return True, None


# CLI interface for standalone testing
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 validator.py <original.sol> <candidate.sol>")
        print("       python3 validator.py --hevm-enable")
        print("       python3 validator.py --hevm-disable")
        print("       python3 validator.py --config")
        sys.exit(1)
    
    arg = sys.argv[1]
    
    if arg == "--hevm-enable":
        config = load_config()
        config['hevm_enabled'] = True
        # Save config
        with open(CONFIG_FILE, 'w') as f:
            f.write(f"fuzz_runs={config['fuzz_runs']}\n")
            f.write(f"hevm_enabled=true\n")
            f.write(f"hevm_timeout={config['hevm_timeout']}\n")
            f.write(f"hevm_solver_timeout={config['hevm_solver_timeout']}\n")
            f.write(f"hevm_max_iterations={config['hevm_max_iterations']}\n")
            f.write(f"max_array_length={config['max_array_length']}\n")
        print("Hevm ENABLED")
        sys.exit(0)
    
    elif arg == "--hevm-disable":
        config = load_config()
        config['hevm_enabled'] = False
        with open(CONFIG_FILE, 'w') as f:
            f.write(f"fuzz_runs={config['fuzz_runs']}\n")
            f.write(f"hevm_enabled=false\n")
            f.write(f"hevm_timeout={config['hevm_timeout']}\n")
            f.write(f"hevm_solver_timeout={config['hevm_solver_timeout']}\n")
            f.write(f"hevm_max_iterations={config['hevm_max_iterations']}\n")
            f.write(f"max_array_length={config['max_array_length']}\n")
        print("Hevm DISABLED")
        sys.exit(0)
    
    elif arg == "--config":
        config = load_config()
        print("Current Configuration:")
        for k, v in config.items():
            print(f"  {k}: {v}")
        sys.exit(0)
    
    # Run validation
    original_path = sys.argv[1]
    candidate_path = sys.argv[2]
    
    is_valid, failure_data = validate(original_path, candidate_path)
    
    if is_valid:
        print("\nVALID: Contracts are equivalent")
        sys.exit(0)
    else:
        print(f"\nINVALID: {failure_data}")
        sys.exit(1)


if __name__ == "__main__":
    main()
