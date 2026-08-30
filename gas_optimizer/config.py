"""
Configuration and path constants.

Every path is derived from the package location, so the pipeline behaves the
same regardless of the working directory it is launched from.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths -------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

# Foundry workspace. `forge` is always invoked with this as its cwd.
FOUNDRY_DIR = PROJECT_ROOT / "foundry"

# Scratch space owned by the validator: originals, candidates and the generated
# harness are all rewritten on every run. Nothing here is source.
SOL_FOLDER = FOUNDRY_DIR / "src"
TEST_FOLDER = FOUNDRY_DIR / "test"

# Validator tunables (fuzz runs, gas gate, hevm toggles).
VALIDATOR_CONFIG_PATH = PROJECT_ROOT / "validatorConfig.txt"

# Test-case inputs.
TEST_CASES_DIR = PROJECT_ROOT / "test-cases"


# --- Environment -------------------------------------------------------------


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


def _int_env(name: str, default: int) -> int:
    """Read an integer setting, falling back rather than crashing on junk."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[WARN] {name}={raw!r} is not an integer; using {default}")
        return default


MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen2.5-Coder-32B-Instruct")

# Recent huggingface_hub routes inference through named providers. Left empty
# the client picks its own default, which is what most setups want.
HF_PROVIDER = os.environ.get("HF_PROVIDER", "").strip()

# Attempts at the optimize-and-verify loop.
MAX_RETRIES = _int_env("MAX_RETRIES", 5)

# Attempts at a single API call before giving up on it.
API_RETRIES = _int_env("API_RETRIES", 3)


def get_hf_token() -> str:
    """Return the Hugging Face token, or raise with a fixable message.

    Read lazily so importing this module never fails — only the code paths that
    actually call the API require a token.
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
