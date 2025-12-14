// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.20;

contract ERC20 {
	uint public totalSupply;
	mapping(address => uint) public balanceOf;
	function transfer(address to, uint val) external returns (bool) {
		balanceOf[msg.sender] -= val;
		unchecked { balanceOf[to] += val; }
		return true;
	}
	function _mint(address to, uint256 val) internal {
		totalSupply += val;
		unchecked { balanceOf[to] += val; }
	}
}