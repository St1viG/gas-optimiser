// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract Counter {
	uint256 private count;

	function increment() external {
		count += 1;
	}

	function current() external view returns (uint256) {
		return count;
	}
}
