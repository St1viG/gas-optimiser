// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

// Control: the only saving is one fewer SLOAD in a view. Before views were
// gas-benched, this pair was rejected as "not cheaper anywhere".
contract RegistryViewOpt {
	mapping(address => uint256) public scores;

	function setScore(uint256 val) external {
		scores[msg.sender] = val;
	}

	function share(address who) external view returns (uint256) {
		uint256 score = scores[who];
		require(score > 0, "no score");
		return score * 2;
	}
}
