// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "../src/ERC4626.sol";
import "../src/ERC4626.sol";

/**
 * @title Equivalence Fuzz Test
 * @notice Verifies ERC4626 and ERC4626 behave identically
 * @dev Run: forge test --match-contract EquivalenceTest -vvv
 */
contract EquivalenceTest is Test {
    ERC4626 original;
    ERC4626 candidate;

    function setUp() public {
        original = new ERC4626();
        candidate = new ERC4626();
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

    function testFuzz_redeem(uint256 shares, address to, address from) public {
        shares = bound(shares, 0, 1000000 ether);
        vm.assume(to != address(0));
        vm.assume(from != address(0));
        vm.assume(to != from);

        original.redeem(shares, to, from);
        candidate.redeem(shares, to, from);

        // Compare state

    }
}
