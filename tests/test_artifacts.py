import pytest

from gas_optimizer.verification import artifacts
from gas_optimizer.verification.artifacts import AbiParam


class TestCanonicalTypes:
    def test_elementary(self):
        assert AbiParam(name="x", type="uint256").canonical_type == "uint256"

    def test_tuple_is_expanded(self):
        param = AbiParam(
            name="s",
            type="tuple",
            components=(AbiParam(name="a", type="uint256"), AbiParam(name="b", type="address")),
        )
        assert param.canonical_type == "(uint256,address)"

    def test_tuple_array_keeps_its_suffix(self):
        param = AbiParam(
            name="s", type="tuple[]", components=(AbiParam(name="a", type="uint256"),)
        )
        assert param.canonical_type == "(uint256)[]"


def test_missing_artifact_is_a_clear_error(tmp_path):
    with pytest.raises(artifacts.ArtifactError, match="No build artifact"):
        artifacts.load(tmp_path / "Nope.sol", "Nope", out_dir=tmp_path)


def test_artifact_without_storage_layout_is_rejected(tmp_path):
    out = tmp_path / "A.sol"
    out.mkdir()
    (out / "A.json").write_text('{"abi": [], "deployedBytecode": {"object": "0x00"}}')
    with pytest.raises(artifacts.ArtifactError, match="storageLayout"):
        artifacts.load(tmp_path / "A.sol", "A", out_dir=tmp_path)


@pytest.mark.foundry
class TestRealArtifacts:
    def test_erc20_abi_and_layout(self, fixtures, build_pair):
        original, _ = build_pair(fixtures / "ERC20.sol", fixtures / "ERC20Candidate.sol")

        assert original.signatures() == {
            "transfer(address,uint256)",
            "balanceOf(address)",
            "totalSupply()",
        }
        assert [f.signature for f in original.mutating_functions] == ["transfer(address,uint256)"]
        assert {f.signature for f in original.view_functions} == {
            "balanceOf(address)",
            "totalSupply()",
        }

        layout = original.storage
        assert [(e.label, e.slot) for e in layout.plain_values] == [("totalSupply", 0)]
        (mapping,) = layout.mappings
        assert (mapping.label, mapping.slot) == ("balanceOf", 1)
        assert layout.mapping_key_chain(mapping.type_id) == ["address"]

    def test_nested_mapping_key_chain(self, fixtures, build_pair):
        original, _ = build_pair(fixtures / "ERC4626.sol", fixtures / "ERC4626Candidate.sol")
        (mapping,) = original.storage.mappings
        assert original.storage.mapping_key_chain(mapping.type_id) == ["address", "address"]
        assert original.storage.mapping_value_encoding(mapping.type_id) == "inplace"

    def test_deployed_bytecode_is_a_hex_string(self, fixtures, build_pair):
        original, _ = build_pair(fixtures / "ERC20.sol", fixtures / "ERC20Candidate.sol")
        assert original.deployed_bytecode.startswith("0x")
        assert len(original.deployed_bytecode) > 2
