// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract EmitterCandidate {
	event Ping(address indexed who, uint amount);
	mapping(address => uint) public deposits;

	function bump(uint amount) external {
		deposits[msg.sender] += amount;
	}
}
