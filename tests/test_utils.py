from gas_optimizer import utils

CONTRACT = """// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract Token {
    uint256 public totalSupply;
}
"""


class TestCleanLlmResponse:
    def test_strips_solidity_fence(self):
        assert utils.clean_llm_response("```solidity\ncontract A {}\n```") == "contract A {}"

    def test_strips_bare_fence(self):
        assert utils.clean_llm_response("```\ncontract A {}\n```") == "contract A {}"

    def test_prefers_the_longest_block(self):
        response = (
            "Here is a snippet:\n```\nx\n```\n"
            "And the full file:\n```solidity\ncontract A {}\n```"
        )
        assert utils.clean_llm_response(response) == "contract A {}"

    def test_passes_through_unfenced_source(self):
        assert utils.clean_llm_response("  contract A {}  ") == "contract A {}"

    def test_handles_empty_input(self):
        assert utils.clean_llm_response("") == ""


class TestExtraction:
    def test_contract_name(self):
        assert utils.extract_contract_name(CONTRACT) == "Token"

    def test_contract_name_missing(self):
        assert utils.extract_contract_name("pragma solidity ^0.8.20;") is None

    def test_pragma(self):
        assert utils.extract_pragma(CONTRACT) == "^0.8.20"

    def test_pragma_whitespace_is_normalized(self):
        assert utils.extract_pragma("pragma   solidity   >=0.8.0   <0.9.0;") == ">=0.8.0 <0.9.0"


class TestRenameContract:
    def test_renames_declaration(self):
        renamed = utils.rename_contract(CONTRACT, "Token", "TokenCandidate")
        assert "contract TokenCandidate {" in renamed

    def test_leaves_similar_names_alone(self):
        source = "contract Token {} contract TokenVault {}"
        renamed = utils.rename_contract(source, "Token", "TokenCandidate")
        assert "contract TokenCandidate {}" in renamed
        assert "contract TokenVault {}" in renamed


class TestCheckCandidateSource:
    def test_accepts_a_real_change(self):
        candidate = CONTRACT.replace(
            "uint256 public totalSupply;", "uint256 public totalSupply; // x"
        )
        assert utils.check_candidate_source(CONTRACT, candidate) == []

    def test_rejects_empty(self):
        assert utils.check_candidate_source(CONTRACT, "   ") == ["the response was empty"]

    def test_rejects_unchanged_source(self):
        problems = utils.check_candidate_source(CONTRACT, CONTRACT)
        assert any("identical to the input" in p for p in problems)

    def test_rejects_renamed_contract(self):
        candidate = CONTRACT.replace("contract Token", "contract OptimizedToken")
        problems = utils.check_candidate_source(CONTRACT, candidate)
        assert any("renamed" in p for p in problems)

    def test_rejects_changed_pragma(self):
        candidate = CONTRACT.replace("^0.8.20", "^0.8.24")
        problems = utils.check_candidate_source(CONTRACT, candidate)
        assert any("pragma changed" in p for p in problems)

    def test_rejects_leftover_fencing(self):
        problems = utils.check_candidate_source(CONTRACT, CONTRACT + "\n```")
        assert any("markdown fencing" in p for p in problems)

    def test_rejects_missing_contract(self):
        problems = utils.check_candidate_source(CONTRACT, "pragma solidity ^0.8.20;\n")
        assert any("no `contract` declaration" in p for p in problems)
