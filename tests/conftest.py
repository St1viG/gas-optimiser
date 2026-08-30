import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    """Skip Foundry-backed tests when `forge` is not on PATH."""
    if shutil.which("forge"):
        return
    skip = pytest.mark.skip(reason="`forge` not installed")
    for item in items:
        if "foundry" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def erc20_source() -> str:
    return (FIXTURES / "ERC20.sol").read_text()


@pytest.fixture
def erc20_candidate_source() -> str:
    return (FIXTURES / "ERC20Candidate.sol").read_text()


@pytest.fixture
def build_pair():
    """Compile a contract pair into the Foundry workspace and load artifacts."""
    from gas_optimizer import validator
    from gas_optimizer.verification import artifacts

    def _build(original: Path, candidate: Path):
        src_original, src_candidate = validator.prepare_workspace(original, candidate)
        code, out, err = validator.run_forge(["build"], timeout=300)
        assert code == 0, f"forge build failed:\n{out}\n{err}"
        return (
            artifacts.load(src_original, _declared_name(original)),
            artifacts.load(src_candidate, _declared_name(candidate)),
        )

    return _build


def _declared_name(path: Path) -> str:
    import re

    return re.search(r"\bcontract\s+(\w+)", path.read_text()).group(1)
