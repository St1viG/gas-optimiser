// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IOriginal {
    function f(uint256 x, address a) external;
    function stateHash() external view returns (bytes32);
}

interface ICandidate {
    function f(uint256 x, address a) external;
    function stateHash() external view returns (bytes32);
}
