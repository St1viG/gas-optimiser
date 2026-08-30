import pytest

from gas_optimizer.verification import harness_generator as hg
from gas_optimizer.verification.artifacts import (
    AbiFunction,
    AbiParam,
    ContractArtifact,
    StorageLayout,
)


def _fn(name, inputs=(), outputs=(), mutability="nonpayable"):
    return AbiFunction(
        name=name,
        inputs=tuple(AbiParam(name=n, type=t) for n, t in inputs),
        outputs=tuple(AbiParam(name="", type=t) for t in outputs),
        state_mutability=mutability,
    )


def _artifact(name, functions, storage=None):
    return ContractArtifact(
        name=name,
        source_name=f"{name}.sol",
        functions=tuple(functions),
        storage=storage or StorageLayout(),
        deployed_bytecode="0x00",
    )


TRANSFER = _fn("transfer", [("to", "address"), ("val", "uint256")], ["bool"])


class TestRefusalToGenerate:
    """A harness that cannot drive a function must fail, not skip it quietly."""

    def test_missing_function_in_candidate(self):
        original = _artifact("A", [TRANSFER])
        candidate = _artifact("B", [])
        with pytest.raises(hg.GenerationError, match="missing functions"):
            hg.generate(original, candidate)

    def test_extra_function_in_candidate(self):
        original = _artifact("A", [TRANSFER])
        candidate = _artifact("B", [TRANSFER, _fn("sneak")])
        with pytest.raises(hg.GenerationError, match="functions the original does not"):
            hg.generate(original, candidate)

    def test_undrivable_parameter_type(self):
        weird = _fn("deposit", [("data", "(uint256,address)")])
        with pytest.raises(hg.GenerationError, match="cannot drive"):
            hg.generate(_artifact("A", [weird]), _artifact("B", [weird]))

    def test_no_mutating_functions(self):
        view_only = _fn("total", outputs=["uint256"], mutability="view")
        with pytest.raises(hg.GenerationError, match="no public state-changing"):
            hg.generate(_artifact("A", [view_only]), _artifact("B", [view_only]))


class TestGeneratedShape:
    def test_emits_three_files(self):
        harness = hg.generate(_artifact("A", [TRANSFER]), _artifact("B", [TRANSFER]))
        assert set(harness.files) == {hg.BASE_FILE, hg.EQUIVALENCE_FILE, hg.GAS_BENCH_FILE}
        assert harness.covered == ("transfer(address,uint256)",)

    def test_every_function_gets_a_raw_and_a_bounded_test(self):
        harness = hg.generate(_artifact("A", [TRANSFER]), _artifact("B", [TRANSFER]))
        source = harness.files[hg.EQUIVALENCE_FILE]
        assert "function testFuzz_transfer_raw(" in source
        assert "function testFuzz_transfer_bounded(" in source

    def test_raw_variant_leaves_arguments_unbounded(self):
        """The bounded variant alone cannot reach overflow divergence."""
        harness = hg.generate(_artifact("A", [TRANSFER]), _artifact("B", [TRANSFER]))
        source = harness.files[hg.EQUIVALENCE_FILE]
        raw = source.split("testFuzz_transfer_raw")[1].split("function ")[0]
        bounded = source.split("testFuzz_transfer_bounded")[1].split("function ")[0]
        assert "bound(val" not in raw
        assert "bound(val" in bounded

    def test_no_test_body_is_assertion_free(self):
        harness = hg.generate(_artifact("A", [TRANSFER]), _artifact("B", [TRANSFER]))
        source = harness.files[hg.EQUIVALENCE_FILE]
        bodies = source.split("function testFuzz_")[1:]
        assert bodies
        for body in bodies:
            assert "_assertEquivalent(" in body

    def test_overloads_get_distinct_test_names(self):
        a = _fn("push", [("x", "uint256")])
        b = _fn("push", [("x", "address")])
        harness = hg.generate(_artifact("A", [a, b]), _artifact("B", [a, b]))
        source = harness.files[hg.EQUIVALENCE_FILE]
        assert "testFuzz_push_0_raw" in source
        assert "testFuzz_push_1_raw" in source

    def test_payable_functions_get_a_value_parameter(self):
        payable = _fn("deposit", mutability="payable")
        harness = hg.generate(_artifact("A", [payable]), _artifact("B", [payable]))
        source = harness.files[hg.EQUIVALENCE_FILE]
        assert "uint256 callValue" in source
        assert "_exec(original, payload, callValue)" in source

    def test_gas_bench_reports_and_gates(self):
        harness = hg.generate(_artifact("A", [TRANSFER]), _artifact("B", [TRANSFER]))
        assert "_reportGas(" in harness.files[hg.GAS_BENCH_FILE]
        base = harness.files[hg.BASE_FILE]
        assert "assertLe(b.gas, a.gas" in base
        assert "GASRESULT|" in base

    def test_constructor_arguments_are_supplied(self):
        original = ContractArtifact(
            name="A", source_name="A.sol", functions=(TRANSFER,),
            storage=StorageLayout(), deployed_bytecode="0x00",
            constructor_inputs=(AbiParam(name="owner", type="address"),),
        )
        candidate = ContractArtifact(
            name="B", source_name="B.sol", functions=(TRANSFER,),
            storage=StorageLayout(), deployed_bytecode="0x00",
            constructor_inputs=(AbiParam(name="owner", type="address"),),
        )
        source = hg.generate(original, candidate).files[hg.EQUIVALENCE_FILE]
        assert "new A(BENCH_ACTOR)" in source
        assert "new B(BENCH_ACTOR)" in source


@pytest.mark.foundry
class TestAgainstRealArtifacts:
    def test_erc20_harness_seeds_the_balance_mapping(self, fixtures, build_pair):
        original, candidate = build_pair(
            fixtures / "ERC20.sol", fixtures / "ERC20Candidate.sol"
        )
        harness = hg.generate(original, candidate)
        source = harness.files[hg.EQUIVALENCE_FILE]
        # balanceOf sits in slot 1 per the compiler's own layout
        assert "_slot1(bytes32(uint256(uint160(address(this)))), 1)" in source
        assert "_seedSlot(0, SEED)" in source  # totalSupply

    def test_erc4626_nested_mapping_is_seeded_two_levels_deep(self, fixtures, build_pair):
        original, candidate = build_pair(
            fixtures / "ERC4626.sol", fixtures / "ERC4626Candidate.sol"
        )
        source = hg.generate(original, candidate).files[hg.EQUIVALENCE_FILE]
        raw = source.split("testFuzz_redeem_raw")[1].split("function ")[0]
        # allow[address][address]: seeded over {this, to, from} x {this, to, from}
        assert raw.count("_seedBoth(_slot2(") == 9
