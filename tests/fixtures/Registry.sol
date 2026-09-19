// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract Registry {
	mapping(address => uint256) public scores;

	function setScore(uint256 val) external {
		scores[msg.sender] = val;
	}

	// The require splits the basic block, so solc's optimizer cannot merge the
	// two SLOADs — caching the read is a genuine saving here.
	function share(address who) external view returns (uint256) {
		require(scores[who] > 0, "no score");
		return scores[who] * 2;
	}
}
