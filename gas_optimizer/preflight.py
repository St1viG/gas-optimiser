"""
Environment checks that fail fast with an actionable message.

The validator can only report what a subprocess told it; by the time forge is
missing there, the failure has already been misspent on a retry loop. These
checks run before any work starts, and `gas-optimize doctor` prints them all.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = True


def check_workspace() -> Check:
    ok = (config.FOUNDRY_DIR / "foundry.toml").is_file()
    return Check(
        name="workspace",
        ok=ok,
        detail=str(config.FOUNDRY_DIR) if ok else f"{config.FOUNDRY_DIR} is missing",
        hint=(
            ""
            if ok
            else "gas-optimizer runs from a git clone (the foundry/ workspace ships with the "
            "repo); pip-only installs are not supported. Set GAS_OPTIMIZER_HOME to point "
            "at a checkout if the package lives elsewhere."
        ),
    )


def check_forge() -> Check:
    path = shutil.which("forge")
    if path is None:
        return Check(
            name="forge",
            ok=False,
            detail="not found on PATH",
            hint="install Foundry: https://getfoundry.sh",
        )
    from . import validator  # local import: validator pulls in the whole pipeline

    _, out, _ = validator.run_command(["forge", "--version"], timeout=10)
    return Check(name="forge", ok=True, detail=out.strip().splitlines()[0] if out else path)


def check_hf_token() -> Check:
    ok = bool(os.environ.get("HF_TOKEN", "").strip())
    return Check(
        name="HF_TOKEN",
        ok=ok,
        detail="set" if ok else "not set",
        hint=(
            ""
            if ok
            else "cp .env.example .env and put your Hugging Face token in it, or export "
            "HF_TOKEN=hf_... (tokens: https://huggingface.co/settings/tokens)"
        ),
    )


def check_hevm() -> Check:
    path = shutil.which("hevm")
    if path is None:
        return Check(
            name="hevm",
            ok=False,
            detail="not found on PATH (symbolic check will be skipped)",
            hint="optional; install instructions in docs/validator.md",
            required=False,
        )
    from . import validator

    _, out, _ = validator.run_command(["hevm", "version"], timeout=10)
    return Check(
        name="hevm",
        ok=True,
        detail=out.strip().splitlines()[0] if out else path,
        required=False,
    )


def report() -> list[Check]:
    """Everything `doctor` shows, hard requirements first."""
    return [check_workspace(), check_forge(), check_hf_token(), check_hevm()]


def require(checks: list[Check]) -> list[Check]:
    """The subset of `checks` that fail and are required."""
    return [c for c in checks if c.required and not c.ok]
