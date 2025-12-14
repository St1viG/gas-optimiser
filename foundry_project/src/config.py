"""
Configuration and constants for the Solidity optimizer.
"""

import os

# Hugging Face
HF_TOKEN = "REDACTED"
MODEL_ID = "Qwen/Qwen2.5-Coder-32B-Instruct"

# Retry settings
MAX_RETRIES = 5

# Base Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Output Paths
# Ovde definisemo folder u kome ce se cuvati ERC20.sol i ERC20Candidate.sol
SOL_FOLDER = os.path.join(BASE_DIR, "contracts") 

# External Tools Paths
FUZZ_GENERATOR_PATH = os.path.join(BASE_DIR, "foundry_project", "fuzz_test_generator.py")