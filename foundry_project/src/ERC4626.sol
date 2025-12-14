// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract ERC4626 {
	mapping(address=>mapping(address=>uint)) allow;
	function redeem(uint shares, address to, address from) public {
		uint a = allow[from][msg.sender];
		if (msg.sender!=from && a!=type(uint).max) {
			unchecked { allow[from][msg.sender] = a - shares; }
		}
	}	
}