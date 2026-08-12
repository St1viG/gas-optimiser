#!/usr/bin/env python3
"""
Fuzz Test Generator for Solidity Equivalence Checking
Generates Foundry fuzz tests to verify original.sol and candidate.sol behave identically.
Uses direct function calls and proper state comparison including mappings.
"""

import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FunctionParam:
    name: str
    type: str


@dataclass
class SolidityFunction:
    name: str
    params: list[FunctionParam]
    state_mutability: str
    visibility: str
    returns: list[FunctionParam]


@dataclass
class StateVariable:
    name: str
    type: str
    slot: int
    is_mapping: bool = False
    mapping_key_type: str = ""
    mapping_value_type: str = ""
    is_public: bool = False


@dataclass 
class ContractInfo:
    name: str
    functions: list[SolidityFunction]
    constructor: Optional[SolidityFunction]
    state_variables: list[StateVariable] = field(default_factory=list)


def parse_contract(filepath: str) -> list[ContractInfo]:
    """Parse Solidity file using regex."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Remove comments
    content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    
    contracts = []
    contract_starts = list(re.finditer(r'contract\s+(\w+)[^{]*\{', content))
    
    for match in contract_starts:
        contract_name = match.group(1)
        start = match.end()
        
        # Find matching closing brace
        brace_count = 1
        pos = start
        while pos < len(content) and brace_count > 0:
            if content[pos] == '{':
                brace_count += 1
            elif content[pos] == '}':
                brace_count -= 1
            pos += 1
        
        contract_body = content[start:pos-1]
        
        # Remove function bodies to avoid matching local variables
        # We keep only the part before any function definitions for state variable detection
        state_var_section = contract_body
        first_func = re.search(r'\bfunction\s+\w+\s*\(', contract_body)
        if first_func:
            state_var_section = contract_body[:first_func.start()]
        
        # Also check for constructor
        first_constructor = re.search(r'\bconstructor\s*\(', contract_body)
        if first_constructor:
            state_var_section = state_var_section[:first_constructor.start()] if first_constructor.start() < len(state_var_section) else state_var_section
        
        functions = []
        constructor = None
        state_variables = []
        
        # Track declaration order for slot assignment
        declarations = []
        
        # Find nested mappings FIRST: mapping(keyType => mapping(keyType2 => valueType))
        nested_mapping_pattern = r'mapping\s*\(\s*(\w+)\s*=>\s*mapping\s*\(\s*(\w+)\s*=>\s*(\w+)\s*\)\s*\)\s*(public\s+|private\s+|internal\s+)?(\w+)\s*;'
        for m in re.finditer(nested_mapping_pattern, state_var_section):
            key1_type = m.group(1)
            key2_type = m.group(2)
            value_type = m.group(3)
            visibility = (m.group(4) or '').strip()
            var_name = m.group(5)
            is_public = visibility == 'public'
            declarations.append((m.start(), StateVariable(
                name=var_name,
                type=f"mapping({key1_type} => mapping({key2_type} => {value_type}))",
                slot=-1,
                is_mapping=True,
                mapping_key_type=key1_type,
                mapping_value_type=f"mapping({key2_type} => {value_type})",
                is_public=is_public
            )))
        
        # Track which names are already found (nested mappings)
        found_names = {d[1].name for d in declarations}
        
        # Find simple mappings: mapping(keyType => valueType)
        for m in re.finditer(r'mapping\s*\(\s*(\w+)\s*=>\s*(\w+)\s*\)\s*(public\s+|private\s+|internal\s+)?(\w+)\s*;', state_var_section):
            key_type = m.group(1)
            value_type = m.group(2)
            visibility = (m.group(3) or '').strip()
            var_name = m.group(4)
            # Skip if already found as nested mapping
            if var_name in found_names:
                continue
            is_public = visibility == 'public'
            declarations.append((m.start(), StateVariable(
                name=var_name,
                type=f"mapping({key_type} => {value_type})",
                slot=-1,
                is_mapping=True,
                mapping_key_type=key_type,
                mapping_value_type=value_type,
                is_public=is_public
            )))
        
        # Find simple variables (only in state variable section, not in functions)
        for m in re.finditer(r'^\s*(uint\d*|int\d*|address|bool|bytes\d*|string)\s+(public\s+|private\s+|internal\s+)?(constant\s+)?(\w+)\s*[;=]', state_var_section, re.MULTILINE):
            var_type = m.group(1)
            visibility = (m.group(2) or '').strip()
            is_constant = m.group(3) is not None
            var_name = m.group(4)
            
            if is_constant:
                continue  # constants don't use storage slots
            
            is_public = visibility == 'public'
            declarations.append((m.start(), StateVariable(
                name=var_name,
                type=var_type,
                slot=-1,
                is_mapping=False,
                is_public=is_public
            )))
        
        # Sort by position and assign slots
        declarations.sort(key=lambda x: x[0])
        for slot, (_, var) in enumerate(declarations):
            var.slot = slot
            state_variables.append(var)
        
        # Parse functions
        func_pattern = r'function\s+(\w+)\s*\(([^)]*)\)\s*((?:public|external|internal|private|\s|view|pure|payable|virtual|override|returns\s*\([^)]*\))*)'
        
        for func_match in re.finditer(func_pattern, contract_body):
            func_name = func_match.group(1)
            params_str = func_match.group(2)
            modifiers = func_match.group(3)
            
            visibility = "internal"
            for v in ['external', 'public', 'private', 'internal']:
                if v in modifiers:
                    visibility = v
                    break
            
            mutability = "nonpayable"
            for m in ['pure', 'view', 'payable']:
                if m in modifiers:
                    mutability = m
                    break
            
            returns_str = ""
            returns_match = re.search(r'returns\s*\(([^)]*)\)', modifiers)
            if returns_match:
                returns_str = returns_match.group(1)
            
            params = parse_params(params_str)
            returns = parse_params(returns_str)
            
            func = SolidityFunction(
                name=func_name,
                params=params,
                state_mutability=mutability,
                visibility=visibility,
                returns=returns
            )
            
            if visibility in ("public", "external"):
                functions.append(func)
        
        # Constructor
        constructor_match = re.search(r'constructor\s*\(([^)]*)\)', contract_body)
        if constructor_match:
            params = parse_params(constructor_match.group(1))
            constructor = SolidityFunction(
                name="constructor",
                params=params,
                state_mutability="nonpayable",
                visibility="public",
                returns=[]
            )
        
        contracts.append(ContractInfo(
            name=contract_name,
            functions=functions,
            constructor=constructor,
            state_variables=state_variables
        ))
    
    return contracts


def parse_params(params_str: str) -> list[FunctionParam]:
    """Parse parameter string into list of FunctionParam."""
    params = []
    if not params_str.strip():
        return params
    
    for param in params_str.split(','):
        param = param.strip()
        if not param:
            continue
        
        param = re.sub(r'\b(memory|storage|calldata)\b', '', param).strip()
        
        parts = param.split()
        if len(parts) >= 2:
            param_type = parts[0]
            param_name = parts[-1]
        elif len(parts) == 1:
            param_type = parts[0]
            param_name = ""
        else:
            continue
        
        params.append(FunctionParam(name=param_name, type=param_type))
    
    return params


def normalize_type(t: str) -> str:
    """Normalize uint/int to uint256/int256."""
    if t == 'uint':
        return 'uint256'
    if t == 'int':
        return 'int256'
    return t


def is_fuzzable_type(t: str) -> bool:
    """Check if type can be fuzzed by Foundry."""
    t = normalize_type(t)
    basic_types = {
        'uint8', 'uint16', 'uint32', 'uint64', 'uint128', 'uint256',
        'int8', 'int16', 'int32', 'int64', 'int128', 'int256',
        'bool', 'address', 'bytes32', 'bytes4', 'bytes8', 'bytes16', 'bytes20',
        'bytes1', 'bytes2', 'bytes3',
    }
    if t in basic_types:
        return True
    if t in ('bytes', 'string'):
        return True
    
    # Dynamic arrays of basic types: uint256[], address[], etc.
    if t.endswith('[]'):
        base = t[:-2]
        base = normalize_type(base)
        return base in basic_types
    
    # Fixed-size arrays: uint256[10], address[5], etc.
    match = re.match(r'(\w+)\[(\d+)\]', t)
    if match:
        base = normalize_type(match.group(1))
        return base in basic_types
    
    return False


def is_dynamic_array(t: str) -> bool:
    """Check if type is a dynamic array."""
    return t.endswith('[]')


def is_fixed_array(t: str) -> bool:
    """Check if type is a fixed-size array."""
    return bool(re.match(r'\w+\[\d+\]', t))


def get_array_base_type(t: str) -> str:
    """Get base type of array."""
    if t.endswith('[]'):
        return normalize_type(t[:-2])
    match = re.match(r'(\w+)\[(\d+)\]', t)
    if match:
        return normalize_type(match.group(1))
    return t


def generate_test_file(
    original_path: str,
    candidate_path: str,
    output_path: str,
    original_contract_name: Optional[str] = None,
    candidate_contract_name: Optional[str] = None
) -> str:
    """Generate the fuzz test Solidity file."""
    
    original_contracts = parse_contract(original_path)
    candidate_contracts = parse_contract(candidate_path)
    
    if not original_contracts:
        raise ValueError(f"No contracts found in {original_path}")
    if not candidate_contracts:
        raise ValueError(f"No contracts found in {candidate_path}")
    
    original = original_contracts[0] if not original_contract_name else \
        next((c for c in original_contracts if c.name == original_contract_name), None)
    candidate = candidate_contracts[0] if not candidate_contract_name else \
        next((c for c in candidate_contracts if c.name == candidate_contract_name), None)
    
    if not original:
        raise ValueError(f"Contract not found in {original_path}")
    if not candidate:
        raise ValueError(f"Contract not found in {candidate_path}")
    
    original_import = f"../src/{Path(original_path).name}"
    candidate_import = f"../src/{Path(candidate_path).name}"
    
    test_code = generate_test_code(original, candidate, original_import, candidate_import)
    
    with open(output_path, 'w') as f:
        f.write(test_code)
    
    return test_code


def generate_test_code(
    original: ContractInfo,
    candidate: ContractInfo,
    original_import: str,
    candidate_import: str
) -> str:
    """Generate the test contract code."""
    
    # Find mappings for state setup
    address_uint_mappings = [v for v in original.state_variables 
                            if v.is_mapping 
                            and v.mapping_key_type == 'address' 
                            and v.mapping_value_type in ('uint', 'uint256')]
    
    # Find public view functions that read state (for comparison)
    view_functions = [f for f in original.functions if f.state_mutability in ('view', 'pure')]
    
    # Find simple address->uint mappings
    address_uint_mappings = [v for v in original.state_variables 
                            if v.is_mapping 
                            and v.mapping_key_type == 'address' 
                            and v.mapping_value_type in ('uint', 'uint256')]
    
    # Find nested mappings: address -> (uint256 -> uint256) like ERC1155 balanceOf
    nested_mappings = [v for v in original.state_variables
                      if v.is_mapping
                      and v.mapping_key_type == 'address'
                      and v.mapping_value_type.startswith('mapping(')]
    
    # Generate test functions
    function_tests = []
    skipped = []
    
    for func in original.functions:
        if func.state_mutability in ('view', 'pure'):
            continue  # Skip view functions, we test them via state-changing functions
        
        # Check if candidate has this function
        cand_func = next((f for f in candidate.functions if f.name == func.name), None)
        if not cand_func:
            skipped.append((func.name, "not in candidate"))
            continue
        
        # Check if all params are fuzzable
        if not all(is_fuzzable_type(p.type) for p in func.params):
            skipped.append((func.name, "unfuzzable param types"))
            continue
        
        test_code = generate_function_test(
            func, original, candidate, 
            address_uint_mappings, nested_mappings, view_functions
        )
        function_tests.append(test_code)
    
    code = f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "{original_import}";
import "{candidate_import}";

/**
 * @title Equivalence Fuzz Test
 * @notice Verifies {original.name} and {candidate.name} behave identically
 * @dev Run: forge test --match-contract EquivalenceTest -vvv
 */
contract EquivalenceTest is Test {{
    {original.name} original;
    {candidate.name} candidate;

    function setUp() public {{
        original = new {original.name}();
        candidate = new {candidate.name}();
    }}

    /*//////////////////////////////////////////////////////////////
                               HELPERS
    //////////////////////////////////////////////////////////////*/

    /// @dev Get storage slot for mapping(address => X) 
    function _mappingSlot(address key, uint256 baseSlot) internal pure returns (bytes32) {{
        return keccak256(abi.encode(key, baseSlot));
    }}
    
    /// @dev Get storage slot for mapping(uint256 => X) 
    function _mappingSlotUint(uint256 key, uint256 baseSlot) internal pure returns (bytes32) {{
        return keccak256(abi.encode(key, baseSlot));
    }}
    
    /// @dev Get storage slot for nested mapping: mapping(address => mapping(uint256 => X))
    function _nestedMappingSlot(address key1, uint256 key2, uint256 baseSlot) internal pure returns (bytes32) {{
        bytes32 outerSlot = keccak256(abi.encode(key1, baseSlot));
        return keccak256(abi.encode(key2, uint256(outerSlot)));
    }}

    /// @dev Set mapping[key] = value for both contracts
    function _setMapping(address key, uint256 baseSlot, uint256 value) internal {{
        bytes32 slot = _mappingSlot(key, baseSlot);
        vm.store(address(original), slot, bytes32(value));
        vm.store(address(candidate), slot, bytes32(value));
    }}
    
    /// @dev Set nested mapping[key1][key2] = value for both contracts
    function _setNestedMapping(address key1, uint256 key2, uint256 baseSlot, uint256 value) internal {{
        bytes32 slot = _nestedMappingSlot(key1, key2, baseSlot);
        vm.store(address(original), slot, bytes32(value));
        vm.store(address(candidate), slot, bytes32(value));
    }}

    /// @dev Assert mapping[key] is equal in both contracts
    function _assertMappingEq(address key, uint256 baseSlot, string memory ctx) internal view {{
        bytes32 slot = _mappingSlot(key, baseSlot);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": mapping mismatch"));
    }}
    
    /// @dev Assert nested mapping[key1][key2] is equal in both contracts
    function _assertNestedMappingEq(address key1, uint256 key2, uint256 baseSlot, string memory ctx) internal view {{
        bytes32 slot = _nestedMappingSlot(key1, key2, baseSlot);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": nested mapping mismatch"));
    }}

    /// @dev Assert simple storage slot is equal
    function _assertSlotEq(uint256 slotNum, string memory ctx) internal view {{
        bytes32 slot = bytes32(slotNum);
        bytes32 a = vm.load(address(original), slot);
        bytes32 b = vm.load(address(candidate), slot);
        assertEq(a, b, string.concat(ctx, ": slot ", vm.toString(slotNum), " mismatch"));
    }}

    /*//////////////////////////////////////////////////////////////
                              FUZZ TESTS
    //////////////////////////////////////////////////////////////*/
'''
    
    for test in function_tests:
        code += test
    
    if skipped:
        code += '\n    // SKIPPED:\n'
        for name, reason in skipped:
            code += f'    // - {name}: {reason}\n'
    
    code += '}\n'
    return code


def generate_function_test(
    func: SolidityFunction,
    original: ContractInfo,
    candidate: ContractInfo,
    address_mappings: list[StateVariable],
    nested_mappings: list[StateVariable],
    view_functions: list[SolidityFunction]
) -> str:
    """Generate a test for a single function."""
    
    # Build parameter list - but convert arrays to seeds
    params = []
    param_names = []
    param_types_map = {}
    array_param_info = []  # (name, base_type)
    
    for i, p in enumerate(func.params):
        name = p.name if p.name else f"arg{i}"
        ptype = normalize_type(p.type)
        
        if is_dynamic_array(p.type):
            # Instead of fuzzing array directly, fuzz a seed
            base = normalize_type(ptype[:-2])
            params.append(f"uint256 {name}Seed")
            param_names.append(name)
            array_param_info.append((name, base))
            param_types_map[name] = p.type
        elif is_fixed_array(p.type) or p.type in ('bytes', 'string'):
            params.append(f"{ptype} memory {name}")
            param_names.append(name)
            param_types_map[name] = p.type
        else:
            params.append(f"{ptype} {name}")
            param_names.append(name)
            param_types_map[name] = p.type
    
    params_str = ", ".join(params)
    
    # Find address params
    address_params = [name for name, p in zip(param_names, func.params) if p.type == 'address']
    has_from_param = 'from' in address_params
    
    # Find uint params (non-array)
    uint_params = [name for name, p in zip(param_names, func.params) if p.type in ('uint', 'uint256')]
    
    # Build setup code
    setup_lines = []
    
    # Generate arrays from seeds
    array_names_for_call = []
    first_array_name = None
    for arr_name, base_type in array_param_info:
        if first_array_name is None:
            first_array_name = arr_name
            setup_lines.append(f"        uint256 arrLen = bound({arr_name}Seed, 1, {MAX_ARRAY_LENGTH});")
        
        setup_lines.append(f"        {base_type}[] memory {arr_name} = new {base_type}[](arrLen);")
        setup_lines.append(f"        for (uint i = 0; i < arrLen; i++) {{")
        setup_lines.append(f"            {arr_name}[i] = uint256(keccak256(abi.encode({arr_name}Seed, i))) % (1000 ether);")
        setup_lines.append(f"        }}")
        array_names_for_call.append(arr_name)
    
    # Build args string for function call
    args_for_call = []
    for name, p in zip(param_names, func.params):
        if is_dynamic_array(p.type):
            args_for_call.append(name)  # Use generated array
        else:
            args_for_call.append(name)
    args_str = ", ".join(args_for_call)
    
    # Bound uint params
    for up in uint_params:
        setup_lines.append(f"        {up} = bound({up}, 0, 1000000 ether);")
    
    # Handle addresses
    for ap in address_params:
        setup_lines.append(f"        vm.assume({ap} != address(0));")
    
    if 'to' in address_params and 'from' in address_params:
        setup_lines.append("        vm.assume(to != from);")
    elif 'to' in address_params:
        setup_lines.append("        vm.assume(to != address(this));")
    
    # Setup simple mappings
    for mapping in address_mappings:
        slot = mapping.slot
        if has_from_param:
            setup_lines.append(f"        _setMapping(from, {slot}, 10000000 ether);")
        else:
            setup_lines.append(f"        _setMapping(address(this), {slot}, 10000000 ether);")
        for ap in address_params:
            if ap != 'from':
                setup_lines.append(f"        _setMapping({ap}, {slot}, 5000000 ether);")
    
    # Setup nested mappings
    for mapping in nested_mappings:
        slot = mapping.slot
        if 'ids' in [name for name, _ in array_param_info]:
            addr_to_setup = 'from' if has_from_param else 'address(this)'
            setup_lines.append(f"        for (uint i = 0; i < arrLen; i++) {{")
            setup_lines.append(f"            _setNestedMapping({addr_to_setup}, ids[i], {slot}, 10000000 ether);")
            setup_lines.append(f"        }}")
        elif uint_params:
            addr_to_setup = 'from' if has_from_param else 'address(this)'
            for up in uint_params:
                if up in ('id', 'tokenId'):
                    setup_lines.append(f"        _setNestedMapping({addr_to_setup}, {up}, {slot}, 10000000 ether);")
                    break
    
    setup_code = "\n".join(setup_lines)
    
    # Build comparison code
    compare_lines = []
    
    # Compare simple storage slots
    for var in original.state_variables:
        if not var.is_mapping:
            compare_lines.append(f'        _assertSlotEq({var.slot}, "{func.name}: {var.name}");')
    
    # Compare simple mappings
    for mapping in address_mappings:
        if has_from_param:
            compare_lines.append(f'        _assertMappingEq(from, {mapping.slot}, "{func.name}: {mapping.name}[from]");')
        else:
            compare_lines.append(f'        _assertMappingEq(address(this), {mapping.slot}, "{func.name}: {mapping.name}[sender]");')
        for ap in address_params:
            if ap != 'from':
                compare_lines.append(f'        _assertMappingEq({ap}, {mapping.slot}, "{func.name}: {mapping.name}[{ap}]");')
    
    # Compare nested mappings
    for mapping in nested_mappings:
        slot = mapping.slot
        if 'ids' in [name for name, _ in array_param_info]:
            addr_to_check = 'from' if has_from_param else 'address(this)'
            compare_lines.append(f'        for (uint i = 0; i < arrLen; i++) {{')
            compare_lines.append(f'            _assertNestedMappingEq({addr_to_check}, ids[i], {slot}, "{func.name}: {mapping.name}[from][ids[i]]");')
            if 'to' in address_params:
                compare_lines.append(f'            _assertNestedMappingEq(to, ids[i], {slot}, "{func.name}: {mapping.name}[to][ids[i]]");')
            compare_lines.append(f'        }}')
    
    # Call view functions
    for vf in view_functions:
        if len(vf.params) == 0:
            compare_lines.append(f'        assertEq(original.{vf.name}(), candidate.{vf.name}(), "{func.name}: {vf.name}() mismatch");')
        elif len(vf.params) == 1 and vf.params[0].type == 'address':
            if has_from_param:
                compare_lines.append(f'        assertEq(original.{vf.name}(from), candidate.{vf.name}(from), "{func.name}: {vf.name}(from) mismatch");')
            for ap in address_params:
                if ap != 'from':
                    compare_lines.append(f'        assertEq(original.{vf.name}({ap}), candidate.{vf.name}({ap}), "{func.name}: {vf.name}({ap}) mismatch");')
    
    compare_code = "\n".join(compare_lines)
    
    # Generate function call
    is_payable = func.state_mutability == "payable"
    
    if is_payable:
        params_str = f"{params_str}, uint256 msgValue" if params_str else "uint256 msgValue"
        call_code = f'''
        msgValue = bound(msgValue, 0, 10 ether);
        vm.deal(address(this), msgValue * 2 + 1 ether);
        
        original.{func.name}{{value: msgValue}}({args_str});
        candidate.{func.name}{{value: msgValue}}({args_str});'''
    else:
        call_code = f'''
        original.{func.name}({args_str});
        candidate.{func.name}({args_str});'''
    
    return f'''
    function testFuzz_{func.name}({params_str}) public {{
{setup_code}
{call_code}

        // Compare state
{compare_code}
    }}
'''


def main():
    parser = argparse.ArgumentParser(
        description="Generate Foundry fuzz tests for Solidity contract equivalence"
    )
    parser.add_argument("original", help="Path to original Solidity file")
    parser.add_argument("candidate", help="Path to candidate/optimized Solidity file")
    parser.add_argument("-o", "--output", default="EquivalenceTest.t.sol",
                       help="Output test file path")
    parser.add_argument("--original-contract", help="Contract name in original file")
    parser.add_argument("--candidate-contract", help="Contract name in candidate file")
    parser.add_argument("--max-array-length", type=int, default=10,
                       help="Max length for dynamic arrays (default: 10)")
    
    args = parser.parse_args()
    
    # Store config globally for use in generation
    global MAX_ARRAY_LENGTH
    MAX_ARRAY_LENGTH = args.max_array_length
    
    try:
        generate_test_file(
            args.original,
            args.candidate,
            args.output,
            args.original_contract,
            args.candidate_contract
        )
        print(f"✓ Generated: {args.output}")
        print(f"\nRun with:")
        print(f"  forge test --match-contract EquivalenceTest -vvv")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0

MAX_ARRAY_LENGTH = 10  # Default


if __name__ == "__main__":
    exit(main())
