// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

// Control: storage and mutations match Counter exactly; only the getter lies.
// Before views were covered, this pair passed equivalence.
contract CounterBadView {
	uint256 private count;

	function increment() external {
		count += 1;
	}

	function current() external view returns (uint256) {
		return count + 1;
	}
}
