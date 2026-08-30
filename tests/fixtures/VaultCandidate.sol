// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract VaultCandidate {
	mapping(address => uint) public deposits;

	function withdraw(uint amount) external returns (uint) {
		uint balance = deposits[msg.sender];
		require(balance >= amount, "insufficient balance");
		uint remaining;
		unchecked { remaining = balance - amount; }
		deposits[msg.sender] = remaining;
		return remaining;
	}
}
