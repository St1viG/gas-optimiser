"""
The tests that actually prove the rebuild works.

Each one is a control with a known correct verdict, and each corresponds to a
way the previous harness got it wrong:

  ERC20 pair    equivalent and cheaper  -> accepted
  ERC4626 pair  unchecked underflow     -> rejected (previously: empty test body)
  no-op pair    equivalent, same gas    -> rejected (previously: "SUCCESS!")
"""

import pytest

from gas_optimizer import llm_client, optimizer, utils, validator

pytestmark = pytest.mark.foundry


FAST = {**validator.DEFAULT_SETTINGS, "fuzz_runs": 300}


class TestValidatorControls:
    def test_equivalent_and_cheaper_is_accepted(self, fixtures):
        result = validator.validate(
            fixtures / "ERC20.sol", fixtures / "ERC20Candidate.sol", FAST, verbose=False
        )
        assert result.ok, result.failure
        assert result.total_delta < 0
        by_signature = {entry.signature: entry for entry in result.gas}
        # views are measured too now — the getters ride along at equal cost
        assert set(by_signature) == {
            "transfer(address,uint256)",
            "totalSupply()",
            "balanceOf(address)",
        }
        assert by_signature["transfer(address,uint256)"].improved
        assert not any(entry.regressed for entry in result.gas)

    def test_unchecked_underflow_is_rejected(self, fixtures):
        """The candidate wraps where the original reverts."""
        result = validator.validate(
            fixtures / "ERC4626.sol", fixtures / "ERC4626Candidate.sol", FAST, verbose=False
        )
        assert not result.ok
        assert result.failure["type"] == "equivalence_error"
        assert "mismatch" in result.failure["error"]

    def test_no_op_candidate_is_rejected_by_the_gas_gate(self, fixtures, tmp_path):
        renamed = tmp_path / "ERC20NoOp.sol"
        renamed.write_text(
            utils.rename_contract((fixtures / "ERC20.sol").read_text(), "ERC20", "ERC20NoOp")
        )

        result = validator.validate(fixtures / "ERC20.sol", renamed, FAST, verbose=False)
        assert not result.ok
        assert result.failure["type"] == "gas_error"
        assert result.gas and result.total_delta == 0

    def test_wrong_return_value_is_rejected(self, fixtures):
        """Storage and revert behaviour match; only the returned value differs.

        The previous harness compared no return values at all, so this class of
        divergence was invisible to it.
        """
        result = validator.validate(
            fixtures / "Vault.sol", fixtures / "VaultBadReturn.sol", FAST, verbose=False
        )
        assert not result.ok
        assert result.failure["type"] == "equivalence_error"
        assert "returndata mismatch" in result.failure["error"]

    def test_dropped_event_is_rejected(self, fixtures):
        """Storage and return value match; the candidate simply stops emitting."""
        result = validator.validate(
            fixtures / "Emitter.sol", fixtures / "EmitterCandidate.sol", FAST, verbose=False
        )
        assert not result.ok
        assert result.failure["type"] == "equivalence_error"
        assert "event" in result.failure["error"]

    def test_guarded_unchecked_subtraction_is_accepted(self, fixtures):
        """`require(balance >= amount)` makes the unchecked form sound."""
        result = validator.validate(
            fixtures / "Vault.sol", fixtures / "VaultCandidate.sol", FAST, verbose=False
        )
        assert result.ok, result.failure
        assert result.total_delta < 0

    def test_lying_view_is_rejected(self, fixtures):
        """Storage and mutations match; only the getter misreports.

        Views were previously never verified, so this class of divergence was
        invisible: a candidate could change what balanceOf() returns and pass.
        """
        result = validator.validate(
            fixtures / "Counter.sol", fixtures / "CounterBadView.sol", FAST, verbose=False
        )
        assert not result.ok
        assert result.failure["type"] == "equivalence_error"
        assert "mismatch" in result.failure["error"]

    def test_view_only_saving_is_accepted(self, fixtures):
        """The only improvement is one fewer SLOAD in a view.

        Views were previously not gas-benched, so a saving confined to a view
        could never satisfy the strictly-cheaper gate.
        """
        result = validator.validate(
            fixtures / "Registry.sol", fixtures / "RegistryViewOpt.sol", FAST, verbose=False
        )
        assert result.ok, result.failure
        assert result.total_delta < 0
        improved = [entry.signature for entry in result.gas if entry.improved]
        assert improved == ["share(address)"]

    def test_gas_gate_can_be_disabled(self, fixtures, tmp_path):
        renamed = tmp_path / "ERC20NoOp.sol"
        renamed.write_text(
            utils.rename_contract((fixtures / "ERC20.sol").read_text(), "ERC20", "ERC20NoOp")
        )

        settings = {**FAST, "require_gas_improvement": False}
        result = validator.validate(fixtures / "ERC20.sol", renamed, settings, verbose=False)
        assert result.ok
        assert result.total_delta == 0

    def test_same_contract_name_is_refused(self, fixtures, tmp_path):
        twin = tmp_path / "Twin.sol"
        twin.write_text((fixtures / "ERC20.sol").read_text())

        result = validator.validate(fixtures / "ERC20.sol", twin, FAST, verbose=False)
        assert not result.ok
        assert result.failure["type"] == "shape_error"

    def test_non_compiling_candidate_is_reported_as_such(self, fixtures, tmp_path):
        broken = tmp_path / "ERC20Broken.sol"
        broken.write_text(
            (fixtures / "ERC20.sol").read_text().replace("contract ERC20", "contract ERC20Broken")
            + "\nthis is not solidity\n"
        )

        result = validator.validate(fixtures / "ERC20.sol", broken, FAST, verbose=False)
        assert not result.ok
        assert result.failure["type"] == "compile_error"
        assert result.failure["compile"] == "false"

    def test_added_public_function_is_refused(self, fixtures, tmp_path):
        source = (fixtures / "ERC20.sol").read_text()
        source = source.replace("contract ERC20", "contract ERC20Extra")
        source = source.rstrip().removesuffix("}") + "\tfunction extra() external {}\n}"

        extra = tmp_path / "ERC20Extra.sol"
        extra.write_text(source)

        result = validator.validate(fixtures / "ERC20.sol", extra, FAST, verbose=False)
        assert not result.ok
        assert result.failure["type"] == "harness_error"
        assert "extra()" in result.failure["error"]


class TestOptimizationLoop:
    """Drive the full loop with a scripted model."""

    def _install(self, monkeypatch, responses):
        calls = []

        def fake(current_code, retry_info=None):
            calls.append((current_code, retry_info))
            index = min(len(calls) - 1, len(responses) - 1)
            value = responses[index]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(llm_client, "get_optimization_proposal", fake)
        monkeypatch.setattr(validator, "load_config", lambda *a, **k: dict(FAST))
        return calls

    def test_accepts_a_good_candidate_on_the_first_attempt(self, fixtures, monkeypatch):
        good = utils.rename_contract(
            (fixtures / "ERC20Candidate.sol").read_text(), "ERC20Candidate", "ERC20"
        )
        self._install(monkeypatch, [good])

        result = optimizer.run_optimization_loop(
            (fixtures / "ERC20.sol").read_text(), verbose=False
        )

        assert result.success, result.message
        assert result.attempts == 1
        assert result.total_delta < 0
        assert result.candidate_path.name == "ERC20Candidate.sol"

    def test_a_rejected_candidate_becomes_retry_feedback(self, fixtures, monkeypatch):
        """Attempt 1 regresses nothing but saves nothing; attempt 2 lands."""
        source = (fixtures / "ERC20.sol").read_text()
        no_op = source.replace("return true;", "return  true;")
        good = utils.rename_contract(
            (fixtures / "ERC20Candidate.sol").read_text(), "ERC20Candidate", "ERC20"
        )
        calls = self._install(monkeypatch, [no_op, good])

        result = optimizer.run_optimization_loop(source, verbose=False)

        assert result.success, result.message
        assert result.attempts == 2

        _, first_retry_info = calls[0]
        assert first_retry_info is None

        second_code, second_retry_info = calls[1]
        assert second_retry_info["type"] == "gas_error"
        # the rejected candidate is what gets handed back, not the pristine original
        assert second_code == no_op

    def test_unchanged_output_is_caught_before_compiling(self, fixtures, monkeypatch):
        source = (fixtures / "ERC20.sol").read_text()
        calls = self._install(monkeypatch, [source])

        result = optimizer.run_optimization_loop(source, verbose=False)

        assert not result.success
        assert "identical to the input" in result.message
        # every attempt was spent, and none of them reached the compiler
        assert len(calls) > 1
        assert calls[1][1]["type"] == "shape_error"

    def test_api_failure_stops_the_loop(self, fixtures, monkeypatch):
        self._install(monkeypatch, [llm_client.LLMError("no route to host")])

        result = optimizer.run_optimization_loop(
            (fixtures / "ERC20.sol").read_text(), verbose=False
        )

        assert not result.success
        assert "no route to host" in result.message
        assert result.attempts == 1
