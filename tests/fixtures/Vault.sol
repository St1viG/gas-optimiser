// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract Vault {
	mapping(address => uint) public deposits;

	function withdraw(uint amount) external returns (uint) {
		uint balance = deposits[msg.sender];
		require(balance >= amount, "insufficient balance");
		deposits[msg.sender] = balance - amount;
		return deposits[msg.sender];
	}
}
