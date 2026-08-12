"""
Configuration and path constants for the Solidity gas optimizer.

Every path is derived from the package location, so the pipeline behaves the
same regardless of the working directory it is launched from.
"""

import os
from pathlib import Path

# --- Paths -------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

# Foundry workspace. `forge` is always invoked with this as its cwd.
FOUNDRY_DIR = PROJECT_ROOT / "foundry"

# Foundry's own source/test dirs, per foundry/foundry.toml.
# Generated originals and candidates are written into SOL_FOLDER.
SOL_FOLDER = FOUNDRY_DIR / "src"
TEST_FOLDER = FOUNDRY_DIR / "test"

# Auto-generated equivalence test, rewritten on every validation run.
EQUIVALENCE_TEST_PATH = TEST_FOLDER / "EquivalenceTest.t.sol"

# Verification tooling
FUZZ_GENERATOR_PATH = PACKAGE_DIR / "verification" / "fuzz_test_generator.py"

# Validator tunables (fuzz runs, hevm toggles) — see validatorConfig.txt
VALIDATOR_CONFIG_PATH = PROJECT_ROOT / "validatorConfig.txt"

# Test-case inputs
TEST_CASES_DIR = PROJECT_ROOT / "test-cases"


# --- Secrets / model ---------------------------------------------------------

def _load_dotenv() -> None:
    """Load PROJECT_ROOT/.env if python-dotenv is available.

    Optional dependency: real environment variables work fine without it.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env")


_load_dotenv()

MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen2.5-Coder-32B-Instruct")


def get_hf_token() -> str:
    """Return the Hugging Face token, or raise with a fixable message.

    Read lazily so that importing this module never fails — only the code paths
    that actually call the API require a token.
    """
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set.\n"
            "  cp .env.example .env   and put your Hugging Face token in it,\n"
            "  or export HF_TOKEN=hf_...\n"
            "  Tokens: https://huggingface.co/settings/tokens"
        )
    return token


# --- Retry settings ----------------------------------------------------------

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
