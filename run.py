#!/usr/bin/env python3
"""
Entry point for the gas optimizer pipeline.

Usage:
    python run.py test-cases/tc2.txt

Flow:
    1. Reads the Solidity source from the given .txt/.sol file.
    2. Asks the LLM for a gas-optimizing patch and applies it.
    3. Writes foundry/src/<Name>.sol and foundry/src/<Name>Candidate.sol.
    4. Verifies equivalence with Foundry fuzz tests (and hevm, if enabled),
       retrying with failure feedback up to MAX_RETRIES times.
"""

import sys

from gas_optimizer.cli import main

if __name__ == "__main__":
    sys.exit(main())
