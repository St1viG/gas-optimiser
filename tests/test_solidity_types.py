import pytest

from gas_optimizer.verification import solidity_types as st


class TestSupport:
    @pytest.mark.parametrize(
        "abi_type",
        [
            "uint256",
            "uint8",
            "int128",
            "address",
            "bool",
            "bytes32",
            "bytes",
            "string",
            "uint256[]",
            "address[3]",
        ],
    )
    def test_supported(self, abi_type):
        st.check_supported(abi_type)

    @pytest.mark.parametrize("abi_type", ["(uint256,address)", "uint256[][]", "tuple", "uint7"])
    def test_unsupported(self, abi_type):
        with pytest.raises(st.UnsupportedType):
            st.check_supported(abi_type)


class TestArrays:
    def test_dynamic(self):
        assert st.split_array("uint256[]") == ("uint256", None)

    def test_fixed(self):
        assert st.split_array("address[3]") == ("address", 3)

    def test_not_an_array(self):
        with pytest.raises(ValueError):
            st.split_array("uint256")


class TestDeclaration:
    def test_value_type_has_no_location(self):
        assert st.declaration("uint256", "x") == "uint256 x"

    def test_reference_types_get_memory(self):
        assert st.declaration("bytes", "x") == "bytes memory x"
        assert st.declaration("string", "x") == "string memory x"
        assert st.declaration("uint256[]", "x") == "uint256[] memory x"


class TestLiterals:
    def test_address_uses_the_bench_actor(self):
        assert st.literal("address") == "BENCH_ACTOR"

    def test_narrow_uint_is_cast(self):
        assert st.literal("uint8") == "uint8(SEED / 2)"

    def test_uint256_is_bare(self):
        assert st.literal("uint256") == "SEED / 2"

    def test_dynamic_array_block(self):
        lines = st.array_literal_block("uint256[]", "xs")
        assert "new uint256[](2)" in lines[0]
        assert len(lines) == 3

    def test_fixed_array_block(self):
        lines = st.array_literal_block("address[3]", "xs")
        assert "address[3] memory xs;" in lines[0]
        assert len(lines) == 4
