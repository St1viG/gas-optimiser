#!/usr/bin/env python3
"""
Full pipeline: TXT file -> optimizer -> validator

Usage:
    python run.py test-cases/tc2.txt
    
Flow:
    1. Runs src/main.py <txt_file> -> creates sol/original.sol and sol/candidate.sol
    2. Extracts contract name from sol/original.sol
    3. Copies to foundry_project/src/{Name}.sol and {Name}Candidate.sol
    4. Runs foundry_project/validator.py {Name}
"""

import subprocess
import sys
import os
import re


def extract_contract_name(filepath: str) -> str:
    """Extract contract name from a Solidity file."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    match = re.search(r'contract\s+(\w+)', content)
    if match:
        return match.group(1)
    raise ValueError(f"Could not find contract name in {filepath}")


def run_optimizer(txt_file: str) -> bool:
    """Run src/main.py to create sol/original.sol and sol/candidate.sol"""
    print("=" * 60)
    print("STEP 1: Running optimizer (src/main.py)")
    print("=" * 60)
    print(f"Input: {txt_file}")
    
    # IZMENA: Koristimo sys.executable umesto "python3"
    result = subprocess.run([sys.executable, "src/main.py", txt_file])
    
    if result.returncode != 0:
        print("ERROR: Optimizer failed")
        return False
    
    # Verify files were created
    if not os.path.exists("sol/original.sol"):
        print("ERROR: sol/original.sol not created")
        return False
    if not os.path.exists("sol/candidate.sol"):
        print("ERROR: sol/candidate.sol not created")
        return False
    
    print("✓ Created sol/original.sol and sol/candidate.sol")
    return True


def copy_to_foundry(contract_name: str) -> bool:
    """Copy sol files to foundry_project/src/ with proper naming."""
    print("\n" + "=" * 60)
    print("STEP 2: Copying to foundry_project/src/")
    print("=" * 60)
    
    original_src = "sol/original.sol"
    candidate_src = "sol/candidate.sol"
    
    original_dst = f"foundry_project/src/{contract_name}.sol"
    candidate_dst = f"foundry_project/src/{contract_name}Candidate.sol"
    
    # Read original
    with open(original_src, 'r') as f:
        original_content = f.read()
    
    # Read candidate and rename contract
    with open(candidate_src, 'r') as f:
        candidate_content = f.read()
    
    # Rename contract in candidate: "contract X" -> "contract XCandidate"
    candidate_content = re.sub(
        rf'\bcontract\s+{contract_name}\b',
        f'contract {contract_name}Candidate',
        candidate_content
    )
    
    # Ensure foundry_project/src exists
    os.makedirs("foundry_project/src", exist_ok=True)
    
    # Write files
    with open(original_dst, 'w') as f:
        f.write(original_content)
    print(f"  {original_src} -> {original_dst}")
    
    with open(candidate_dst, 'w') as f:
        f.write(candidate_content)
    print(f"  {candidate_src} -> {candidate_dst}")
    
    return True


def run_validator(contract_name: str) -> bool:
    """Run validator from foundry_project."""
    print("\n" + "=" * 60)
    print(f"STEP 3: Running validator for {contract_name}")
    print("=" * 60)
    
    # IZMENA: Koristimo sys.executable umesto "python3"
    result = subprocess.run(
        [sys.executable, "validator.py", contract_name],
        cwd="foundry_project"
    )
    
    return result.returncode == 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python run.py <test-case.txt>")
        print()
        print("Example: python run.py test-cases/tc2.txt")
        sys.exit(1)
    
    txt_file = sys.argv[1]
    
    if not os.path.exists(txt_file):
        print(f"ERROR: File not found: {txt_file}")
        sys.exit(1)
    
    # Step 1: Run optimizer
    if not run_optimizer(txt_file):
        sys.exit(1)
    
    # Extract contract name
    try:
        contract_name = extract_contract_name("sol/original.sol")
        print(f"  Contract name: {contract_name}")
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    
    # Step 2: Copy to foundry_project
    if not copy_to_foundry(contract_name):
        sys.exit(1)
    
    # Step 3: Run validator
    if run_validator(contract_name):
        print("\n" + "=" * 60)
        print("✅ SUCCESS: Validation passed!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("❌ FAILED: Validation failed")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()