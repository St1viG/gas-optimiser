// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "../src/ERC20.sol";
import "../src/ERC20Candidate.sol";

/**
 * @title Equivalence Fuzz Test
 * @notice Verifies ERC20 and ERC20Candidate behave identically
 * @dev Run: forge test --match-contract EquivalenceTest -vvv
 */
contract EquivalenceTest is Test {
    ERC20 original;
    ERC20Candidate candidate;

    function setUp() public {
        original = new ERC20();
        candidate = new ERC20Candidate();
    }

    /*//////////////////////////////////////////////////////////////
                               HELPERS
    //////////////////////////////////////////////////////////////*/

    /// @dev Get storage slot for mapping(address => X) 
    function _mappingSlot(address key, uint256 baseSlot) internal pure returns (bytes32) {
        return keccak256(abi.encode(key, baseSlot));
    }
    
    /// @dev Get storage slot for mapping(uint256 => X) 
    function _mappingSlotUint(uint256 key, uint256 baseSlot) internal pure returns (bytes32) {
        return keccak256(abi.encode(key, baseSlot));
    }
    
    /// @dev Get storage slot for nested mapping: mapping(address => mapping(uint256 => X))
    function _nestedMappingSlot(address key1, uint256 key2, uint256 baseSlot) internal pure returns (bytes32) {
        bytes32 outerSlot = keccak256(abi.encode(key1, baseSlot));
        return keccak256(abi.encode(key2, uint256(outerSlot)));
    }

    /// @dev Set mapping[key] = value for both contracts
    function _setMapping(address key, uint256 baseSlot, uint256 value) internal {
        bytes32 slot = _mappingSlot(key, baseSlot);
        vm.store(address(original), slot, bytes32(value));
        vm.store(address(candidate), slot, bytes32(value));
    }
    
    /// @dev Set nested mapping[key1][key2] = value for both contracts
    function _setNestedMapping(address key1, uint256 key2, uint256 baseSlot, uint256 value) internal {
        bytes32 slot = _nestedMappingSlot(key1, key2, baseSlot);
        vm.store(address(original), slot, bytes32(value));
        vm.store(address(candidate), slot, bytes32(value));
    }

    /// @dev Assert mapping[key] is equal in both contracts
    function _assertMappingEq(address key, uint256 baseSlot, string memory ctx) internal view {
        bytes32 slot = _mappingSlot(key, baseSlot);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": mapping mismatch"));
    }
    
    /// @dev Assert nested mapping[key1][key2] is equal in both contracts
    function _assertNestedMappingEq(address key1, uint256 key2, uint256 baseSlot, string memory ctx) internal view {
        bytes32 slot = _nestedMappingSlot(key1, key2, baseSlot);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": nested mapping mismatch"));
    }

    /// @dev Assert simple storage slot is equal
    function _assertSlotEq(uint256 slotNum, string memory ctx) internal view {
        bytes32 slot = bytes32(slotNum);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": slot ", vm.toString(slotNum), " mismatch"));
    }

    /*//////////////////////////////////////////////////////////////
                              FUZZ TESTS
    //////////////////////////////////////////////////////////////*/

    function testFuzz_transfer(address to, uint256 val) public {
        val = bound(val, 0, 1000000 ether);
        vm.assume(to != address(0));
        vm.assume(to != address(this));
        _setMapping(address(this), 1, 10000000 ether);
        _setMapping(to, 1, 5000000 ether);

        original.transfer(to, val);
        candidate.transfer(to, val);

        // Compare state
        _assertSlotEq(0, "transfer: totalSupply");
        _assertMappingEq(address(this), 1, "transfer: balanceOf[sender]");
        _assertMappingEq(to, 1, "transfer: balanceOf[to]");
    }
}
