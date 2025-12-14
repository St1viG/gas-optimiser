import subprocess
import os
import sys
import re
import select
import signal

class TimeoutError(Exception):
    pass

def run_command(command_list):
    """Runs a command and returns the output string."""
    try:
        result = subprocess.run(
            command_list,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running command: {' '.join(command_list)}")
        print(f"Error details:\n{e.stderr}")
        raise e


def parse_counterexamples(output, max_examples=10):
    """
    Parses hevm output to extract counterexamples.
    Returns a list of counterexample strings (up to max_examples).
    """
    counterexamples = []
    
    # hevm typically outputs counterexamples in patterns like:
    # "Counterexample:" followed by calldata/input values
    # or "Not equal!" with specific input data
    
    lines = output.split('\n')
    current_example = []
    in_counterexample = False
    
    for line in lines:
        # Detect start of a counterexample block
        if any(marker in line.lower() for marker in ['counterexample', 'not equal', 'calldata:', 'input:']):
            if current_example and in_counterexample:
                counterexamples.append('\n'.join(current_example))
                if len(counterexamples) >= max_examples:
                    break
                current_example = []
            in_counterexample = True
        
        if in_counterexample:
            current_example.append(line)
        
        # Detect end of counterexample (empty line or new section)
        if in_counterexample and line.strip() == '' and current_example:
            counterexamples.append('\n'.join(current_example))
            current_example = []
            in_counterexample = False
            if len(counterexamples) >= max_examples:
                break
    
    # Don't forget the last one
    if current_example and len(counterexamples) < max_examples:
        counterexamples.append('\n'.join(current_example))
    
    # If no structured counterexamples found, return relevant output chunks
    if not counterexamples:
        # Return non-empty lines that might contain useful info
        relevant_lines = [l for l in lines if l.strip() and not l.startswith('[')]
        if relevant_lines:
            counterexamples = relevant_lines[:max_examples]
    
    return counterexamples[:max_examples]


def run_hevm_equivalence(original_path, candidate_path, timeout_seconds=3000, max_counterexamples=1):
    """
    Runs hevm equivalence check with proper timeout handling.
    Returns: (is_equivalent: bool, counterexamples: list)
    """
    cmd = [
        "hevm", "equivalence",
        "--code-a-file", original_path,
        "--code-b-file", candidate_path
    ]
    
    print(f"[*] Running hevm equivalence (timeout: {timeout_seconds}s)...")
    print("-" * 50)
    
    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        # Use communicate with timeout - this is the proper way to handle timeouts
        stdout, _ = process.communicate(timeout=timeout_seconds)
        
        print(stdout)
        
        if process.returncode == 0:
            return True, []
        else:
            counterexamples = parse_counterexamples(stdout, max_counterexamples)
            return False, counterexamples
            
    except subprocess.TimeoutExpired:
        if process:
            process.kill()
            process.wait()
        print(f"\n⏰ TIMEOUT: hevm stopped after {timeout_seconds}s")
        print("Possible causes: complex math, path explosion, or Z3 solver stuck.")
        return False, ["Timeout: hevm could not complete within time limit"]
        
    except Exception as e:
        if process:
            process.kill()
            process.wait()
        return False, [f"Error: {str(e)}"]


def get_bytecode(contract_path, contract_name):
    """
    Extract deployed bytecode for a contract.
    contract_path: path to the .sol file
    contract_name: name of the contract inside the file
    """
    print(f"[*] Extracting bytecode for {contract_name} from {contract_path}...")
    return run_command(["forge", "inspect", f"{contract_path}:{contract_name}", "deployedBytecode"])


def validate_equivalence(contract_name, timeout=3000, keep_files=False):
    """
    Validates equivalence between Original and Candidate contracts.
    
    Expects:
        - src/{contract_name}.sol containing contract named {contract_name}
        - src/{contract_name}Candidate.sol containing contract named {contract_name}Candidate
    
    Returns: (is_equivalent: bool, counterexamples: list)
    """
    # Define paths
    original_sol = f"src/{contract_name}.sol"
    candidate_sol = f"src/{contract_name}Candidate.sol"
    
    original_bin = f"src/{contract_name}.bin"
    candidate_bin = f"src/{contract_name}Candidate.bin"
    
    candidate_contract_name = f"{contract_name}Candidate"
    
    print(f"[*] Validating: {contract_name} vs {candidate_contract_name}")
    print(f"    Original:  {original_sol}")
    print(f"    Candidate: {candidate_sol}")
    print()

    # Check files exist
    if not os.path.exists(original_sol):
        print(f"❌ Original contract not found: {original_sol}")
        return False, [f"File not found: {original_sol}"]
    
    if not os.path.exists(candidate_sol):
        print(f"❌ Candidate contract not found: {candidate_sol}")
        return False, [f"File not found: {candidate_sol}"]

    try:
        # 1. Compile
        print("[*] Compiling project with Forge...")
        run_command(["forge", "build"])
        print("    ✓ Compilation successful")

        # 2. Extract bytecode
        original_code = get_bytecode(original_sol, contract_name)
        candidate_code = get_bytecode(candidate_sol, candidate_contract_name)

        if not original_code or original_code == "0x":
            return False, [f"Empty bytecode for {contract_name}. Is it an abstract contract?"]
        
        if not candidate_code or candidate_code == "0x":
            return False, [f"Empty bytecode for {candidate_contract_name}. Is it an abstract contract?"]

        # 3. Save bytecode to .bin files
        with open(original_bin, "w") as f:
            f.write(original_code)
        with open(candidate_bin, "w") as f:
            f.write(candidate_code)
        print(f"[*] Bytecode saved: {original_bin}, {candidate_bin}")

        # 4. Quick check: if bytecodes are identical, skip hevm
        if original_code == candidate_code:
            print("\n✅ Bytecodes are identical - contracts are equivalent.")
            cleanup_files(original_bin, candidate_bin, keep_files)
            return True, []

        print("[*] Bytecodes differ - running symbolic equivalence check...")

        # 5. Run hevm equivalence
        is_equivalent, counterexamples = run_hevm_equivalence(
            original_bin, 
            candidate_bin, 
            timeout_seconds=timeout,
            max_counterexamples=10
        )

        # 6. Cleanup
        cleanup_files(original_bin, candidate_bin, keep_files)

        # 7. Report results
        if is_equivalent:
            print("\n✅ Contracts are symbolically equivalent.")
        else:
            print(f"\n❌ Contracts are NOT equivalent.")
            if counterexamples:
                print(f"   Found {len(counterexamples)} counterexample(s).")

        return is_equivalent, counterexamples

    except subprocess.CalledProcessError as e:
        cleanup_files(original_bin, candidate_bin, keep_files)
        return False, [f"Command failed: {e.stderr or str(e)}"]
    except Exception as e:
        cleanup_files(original_bin, candidate_bin, keep_files)
        return False, [f"Unexpected error: {str(e)}"]


def cleanup_files(*files, keep_files=False):
    """Remove temporary files unless keep_files is True."""
    if keep_files:
        return
    for f in files:
        if isinstance(f, str) and os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 validator.py <ContractName> [timeout] [keep_files]")
        print()
        print("Arguments:")
        print("  ContractName  - Name of the contract (without 'Candidate' suffix)")
        print("  timeout       - Optional: timeout in seconds (default: 60)")
        print("  keep_files    - Optional: 'true' to keep .bin files")
        print()
        print("Expected files:")
        print("  src/<ContractName>.sol          - Original contract")
        print("  src/<ContractName>Candidate.sol - Candidate contract")
        print()
        print("Example: python3 validator.py MyToken 120 true")
        sys.exit(1)

    contract_name = sys.argv[1]
    
    timeout = 3000
    if len(sys.argv) > 2:
        try:
            timeout = int(sys.argv[2])
        except ValueError:
            pass
    
    keep = len(sys.argv) > 3 and sys.argv[3].lower() == "true"

    is_valid, counterexamples = validate_equivalence(contract_name, timeout=timeout, keep_files=keep)
    
    # Print counterexamples for AI consumption
    if counterexamples:
        print("\n" + "=" * 50)
        print("COUNTEREXAMPLES:")
        print("=" * 50)
        for i, ce in enumerate(counterexamples, 1):
            print(f"\n--- Counterexample {i} ---")
            print(ce)
    
    sys.exit(0 if is_valid else 1)