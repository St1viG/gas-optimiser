// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract VaultBadReturn {
	mapping(address => uint) public deposits;

	function withdraw(uint amount) external returns (uint) {
		uint balance = deposits[msg.sender];
		require(balance >= amount, "insufficient balance");
		unchecked { deposits[msg.sender] = balance - amount; }
		return balance;
	}
}
