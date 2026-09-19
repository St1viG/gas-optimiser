"""
Generates the Solidity harness that decides whether a candidate is accepted.

Two files are emitted into the Foundry test dir:

  EquivalenceTest.t.sol  differential fuzzing: for every public function (views
                         included), call original and candidate with identical
                         calldata from identical state and require identical
                         revert status, return data, storage writes and events.
                         After each mutating call, every zero-argument view is
                         additionally compared, so a getter that lies about
                         mutated state is caught.
  GasBench.t.sol         deterministic per-function gas measurement, asserting
                         the candidate never regresses.

Both are driven by `forge build` artifacts (see artifacts.py), not by parsing
Solidity, so packing, inheritance and constants are handled by the compiler
rather than approximated.

Design notes worth keeping in mind when editing:

* Calls go through `address.call(payload)` rather than a typed interface. That
  is what makes a reverting *original* a pass instead of a false failure, and it
  is what lets return data (and revert reasons) be compared as bytes.
* Storage is compared over the union of slots either side wrote, discovered at
  runtime via `vm.record`/`vm.accesses`. No slot arithmetic is assumed.
* Each function gets two fuzz tests. `_raw` leaves arguments unbounded so
  overflow/underflow divergence introduced by `unchecked` is reachable;
  `_bounded` constrains them to seeded ranges so the happy path is explored
  deeply. Dropping `_raw` is how the previous harness let an unsound
  `unchecked` subtraction through.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import solidity_types as st
from .artifacts import AbiFunction, ContractArtifact, StorageLayout

EQUIVALENCE_FILE = "EquivalenceTest.t.sol"
GAS_BENCH_FILE = "GasBench.t.sol"
BASE_FILE = "HarnessBase.sol"

# Seed amount for mappings and scalar slots. Large enough that realistic
# arithmetic succeeds, far enough from 2**256 that seeding alone cannot
# manufacture an overflow neither contract would ever see in practice.
SEED_AMOUNT = "1_000_000 ether"

_RESERVED = {
    "original",
    "candidate",
    "vm",
    "SEED",
    "BENCH_ACTOR",
    "payload",
    "value",
    "assembly",
    "return",
    "returns",
    "memory",
    "storage",
    "calldata",
    "function",
    "contract",
    "address",
    "bool",
    "bytes",
    "string",
    "int",
    "uint",
    "this",
    "msg",
    "block",
    "tx",
    "now",
    "gasleft",
    "type",
    "new",
    "delete",
    "emit",
}


class GenerationError(Exception):
    """The harness could not be generated — treated as a validation failure."""


@dataclass(frozen=True)
class Harness:
    files: dict[str, str]
    covered: tuple[str, ...]
    benched: tuple[str, ...]


# --- helpers -----------------------------------------------------------------


def _param_names(func: AbiFunction) -> list[str]:
    names: list[str] = []
    for i, p in enumerate(func.inputs):
        name = p.name
        if not name or not name.isidentifier() or name in _RESERVED or name in names:
            name = f"arg{i}"
        names.append(name)
    return names


def _test_suffixes(functions: tuple[AbiFunction, ...]) -> dict[str, str]:
    """Unique, readable test-name suffixes (overloads get an index)."""
    counts: dict[str, int] = {}
    for f in functions:
        counts[f.name] = counts.get(f.name, 0) + 1

    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for f in functions:
        if counts[f.name] == 1:
            out[f.signature] = f.name
        else:
            idx = seen.get(f.name, 0)
            seen[f.name] = idx + 1
            out[f.signature] = f"{f.name}_{idx}"
    return out


def _key_expr(key_type: str, value_expr: str) -> str | None:
    """Cast `value_expr` to the bytes32 form used when hashing a mapping key."""
    if key_type == "address":
        return f"bytes32(uint256(uint160({value_expr})))"
    if key_type == "bool":
        return f"bytes32(uint256(({value_expr}) ? 1 : 0))"
    if key_type == "bytes32":
        return value_expr
    if key_type.startswith("uint"):
        return f"bytes32(uint256({value_expr}))"
    if key_type.startswith("int"):
        return f"bytes32(uint256(int256({value_expr})))"
    return None


def _seed_lines(
    storage: StorageLayout,
    address_exprs: list[str],
    uint_exprs: list[str],
    indent: str = "        ",
) -> list[str]:
    """Store identical state into both contracts before the differential call.

    Without this almost every fuzz input reverts on both sides for lack of
    balance/allowance, and the run is vacuously equal.
    """
    lines: list[str] = []

    for entry in storage.plain_values:
        label = storage.label(entry.type_id)
        if label.startswith("uint") and storage.num_bytes(entry.type_id) == 32:
            lines.append(f"{indent}_seedSlot({entry.slot}, SEED);")

    by_type = {"address": address_exprs, "uint": uint_exprs}

    for entry in storage.mappings:
        if storage.mapping_value_encoding(entry.type_id) != "inplace":
            continue  # value is itself a struct/array: not a single-slot store

        chain = storage.mapping_key_chain(entry.type_id)
        if not 1 <= len(chain) <= 2:
            continue

        def choices(key_type: str) -> list[str]:
            if key_type == "address":
                return by_type["address"]
            if key_type.startswith("uint") or key_type.startswith("int"):
                return by_type["uint"]
            return []

        if len(chain) == 1:
            for expr in choices(chain[0]):
                key = _key_expr(chain[0], expr)
                if key:
                    lines.append(f"{indent}_seedBoth(_slot1({key}, {entry.slot}), SEED);")
        else:
            outer, inner = choices(chain[0])[:3], choices(chain[1])[:3]
            for a in outer:
                for b in inner:
                    ka, kb = _key_expr(chain[0], a), _key_expr(chain[1], b)
                    if ka and kb:
                        lines.append(f"{indent}_seedBoth(_slot2({ka}, {kb}, {entry.slot}), SEED);")

    return lines


def _array_truncation(func: AbiFunction, names: list[str], max_len: int) -> list[str]:
    """Cap fuzzed dynamic arrays so a huge length cannot dominate the run."""
    lines = []
    for name, p in zip(names, func.inputs, strict=True):
        if st.is_array(p.type) and st.split_array(p.type)[1] is None:
            lines.append(f"        if ({name}.length > {max_len}) {{")
            lines.append(f'            assembly ("memory-safe") {{ mstore({name}, {max_len}) }}')
            lines.append("        }")
    return lines


def _assumptions(func: AbiFunction, names: list[str]) -> list[str]:
    """Rule out inputs whose divergence would be an artifact of the harness.

    Only the two contract addresses are excluded: passing one of them as an
    argument makes the two calls structurally different (self-transfer versus
    cross-transfer), which is not a semantic difference between the contracts.
    """
    lines = []
    for name, p in zip(names, func.inputs, strict=True):
        if st.is_address(p.type):
            lines.append(f"        vm.assume({name} != original && {name} != candidate);")
    return lines


def _bounds(func: AbiFunction, names: list[str]) -> list[str]:
    lines = []
    for name, p in zip(names, func.inputs, strict=True):
        if st.is_unsigned(p.type) and p.type == "uint256":
            lines.append(f"        {name} = bound({name}, 0, SEED);")
        elif st.is_array(p.type):
            base, _ = st.split_array(p.type)
            if base == "uint256":
                lines.append(f"        for (uint256 _i = 0; _i < {name}.length; _i++) {{")
                lines.append(f"            {name}[_i] = bound({name}[_i], 0, SEED / 16);")
                lines.append("        }")
    return lines


def _payload(func: AbiFunction, names: list[str]) -> str:
    if not names:
        return f'abi.encodeWithSignature("{func.signature}")'
    return f'abi.encodeWithSignature("{func.signature}", {", ".join(names)})'


def _deploy(artifact: ContractArtifact) -> str:
    args = ", ".join(st.literal(p.canonical_type) for p in artifact.constructor_inputs)
    return f"new {artifact.name}({args})"


# --- equivalence harness -----------------------------------------------------


def _equivalence_test(
    func: AbiFunction,
    suffix: str,
    storage: StorageLayout,
    max_array_length: int,
    bounded: bool,
    sweep_views: bool = False,
) -> str:
    names = _param_names(func)
    for p in func.inputs:
        st.check_supported(p.canonical_type)

    params = [st.declaration(p.canonical_type, n) for n, p in zip(names, func.inputs, strict=True)]
    if func.is_payable:
        params.append("uint256 callValue")

    body: list[str] = []
    body += _array_truncation(func, names, max_array_length)
    body += _assumptions(func, names)
    if bounded:
        body += _bounds(func, names)
    if func.is_payable:
        body.append("        callValue = bound(callValue, 0, 10 ether);")
        body.append("        vm.deal(address(this), callValue * 2 + 1 ether);")

    address_exprs = ["address(this)"] + [
        n for n, p in zip(names, func.inputs, strict=True) if st.is_address(p.type)
    ]
    uint_exprs = [n for n, p in zip(names, func.inputs, strict=True) if st.is_unsigned(p.type)] + [
        "0",
        "1",
    ]
    body += _seed_lines(storage, address_exprs, uint_exprs)

    value = "callValue" if func.is_payable else "0"
    ctx = f"{func.signature}"

    body.append("")
    body.append(f"        bytes memory payload = {_payload(func, names)};")
    body.append(f"        CallResult memory a = _exec(original, payload, {value});")
    body.append(f"        CallResult memory b = _exec(candidate, payload, {value});")
    body.append("")
    body.append(f'        _assertEquivalent(a, b, "{ctx}");')
    if sweep_views:
        # Views read over *mutated* state — this is what catches a getter that
        # only lies after a state change the seeder never produces.
        body.append(f'        _assertViewsMatch("{ctx}");')

    kind = "bounded" if bounded else "raw"
    return (
        f"    function testFuzz_{suffix}_{kind}({', '.join(params)}) public {{\n"
        + "\n".join(body)
        + "\n    }\n"
    )


def _views_match_helper(views: list[AbiFunction]) -> str:
    """The post-mutation sweep: every zero-argument view must agree."""
    if not views:
        return ""
    lines = [
        "    /// @dev Called after every mutating differential call, so getters are",
        "    ///      checked over mutated state, not just the seeded baseline.",
        "    function _assertViewsMatch(string memory ctx) internal {",
    ]
    for func in views:
        lines.append(
            f'        _assertViewEq(abi.encodeWithSignature("{func.signature}"), '
            f'string.concat(ctx, " -> {func.signature}"));'
        )
    lines.append("    }\n")
    return "\n" + "\n".join(lines)


def _equivalence_source(
    original: ContractArtifact,
    candidate: ContractArtifact,
    max_array_length: int,
) -> tuple[str, list[str]]:
    suffixes = _test_suffixes(original.functions)
    zero_arg_views = [f for f in original.view_functions if not f.inputs]

    tests: list[str] = []
    covered: list[str] = []
    for func in original.functions:
        suffix = suffixes[func.signature]
        for bounded in (False, True):
            tests.append(
                _equivalence_test(
                    func,
                    suffix,
                    original.storage,
                    max_array_length,
                    bounded,
                    sweep_views=func.is_mutating and bool(zero_arg_views),
                )
            )
        covered.append(func.signature)

    header = f"""// SPDX-License-Identifier: MIT
// GENERATED by gas_optimizer.verification.harness_generator — do not edit.
pragma solidity ^0.8.20;

import {{HarnessBase}} from "./{BASE_FILE}";
import {{{original.name}}} from "../src/{original.source_name}";
import {{{candidate.name}}} from "../src/{candidate.source_name}";

/// @notice Differential fuzzing: {original.name} vs {candidate.name}.
contract EquivalenceTest is HarnessBase {{
    function setUp() public {{
        original = address({_deploy(original)});
        candidate = address({_deploy(candidate)});
    }}

"""
    return header + "\n".join(tests) + _views_match_helper(zero_arg_views) + "}\n", covered


# --- gas benchmark -----------------------------------------------------------


def _gas_test(func: AbiFunction, suffix: str, storage: StorageLayout) -> str:
    names = _param_names(func)
    body: list[str] = []
    args: list[str] = []

    for name, p in zip(names, func.inputs, strict=True):
        t = p.canonical_type
        if st.is_array(t):
            body += st.array_literal_block(t, name)
        else:
            body.append(f"        {st.declaration(t, name)} = {st.literal(t)};")
        args.append(name)

    address_exprs = ["address(this)", "BENCH_ACTOR"]
    uint_exprs = ["SEED / 2", "0", "1"]
    body += _seed_lines(storage, address_exprs, uint_exprs)

    value = "1 ether" if func.is_payable else "0"
    if func.is_payable:
        body.append("        vm.deal(address(this), 10 ether);")

    body.append("")
    body.append(f"        bytes memory payload = {_payload(func, args)};")
    body.append(f"        CallResult memory a = _exec(original, payload, {value});")
    body.append(f"        CallResult memory b = _exec(candidate, payload, {value});")
    body.append("")
    body.append(f'        _reportGas("{func.signature}", a, b);')

    return f"    function test_gas_{suffix}() public {{\n" + "\n".join(body) + "\n    }\n"


def _gas_source(original: ContractArtifact, candidate: ContractArtifact) -> tuple[str, list[str]]:
    suffixes = _test_suffixes(original.functions)

    tests: list[str] = []
    benched: list[str] = []
    for func in original.functions:
        tests.append(_gas_test(func, suffixes[func.signature], original.storage))
        benched.append(func.signature)

    header = f"""// SPDX-License-Identifier: MIT
// GENERATED by gas_optimizer.verification.harness_generator — do not edit.
pragma solidity ^0.8.20;

import {{HarnessBase}} from "./{BASE_FILE}";
import {{{original.name}}} from "../src/{original.source_name}";
import {{{candidate.name}}} from "../src/{candidate.source_name}";

/// @notice Deterministic per-function gas comparison.
contract GasBench is HarnessBase {{
    function setUp() public {{
        original = address({_deploy(original)});
        candidate = address({_deploy(candidate)});
    }}

"""
    return header + "\n".join(tests) + "}\n", benched


# --- shared base -------------------------------------------------------------

HARNESS_BASE = f"""// SPDX-License-Identifier: MIT
// GENERATED by gas_optimizer.verification.harness_generator — do not edit.
pragma solidity ^0.8.20;

import {{Test, Vm, console}} from "forge-std/Test.sol";

/// @notice Shared machinery for the generated equivalence and gas harnesses.
abstract contract HarnessBase is Test {{
    uint256 internal constant SEED = {SEED_AMOUNT};
    address internal constant BENCH_ACTOR = address(0xBEEF);

    address internal original;
    address internal candidate;

    struct CallResult {{
        bool ok;
        bytes ret;
        bytes32[] writes;
        Vm.Log[] logs;
        uint256 gas;
    }}

    /// @dev One side of a differential call, capturing everything observable.
    function _exec(address target, bytes memory payload, uint256 value)
        internal
        returns (CallResult memory r)
    {{
        vm.record();
        vm.recordLogs();
        (r.ok, r.ret) = target.call{{value: value}}(payload);
        r.gas = vm.lastCallGas().gasTotalUsed;
        (, r.writes) = vm.accesses(target);
        r.logs = vm.getRecordedLogs();
    }}

    function _assertEquivalent(CallResult memory a, CallResult memory b, string memory ctx)
        internal
        view
    {{
        assertEq(a.ok, b.ok, string.concat(ctx, ": revert-status mismatch"));
        assertEq(a.ret, b.ret, string.concat(ctx, ": returndata mismatch"));
        _assertStorageEq(a, b, ctx);
        _assertLogsEq(a, b, ctx);
    }}

    /// @dev Compare the union of slots either side wrote, so a candidate that
    ///      writes a slot the original never touches is caught too.
    function _assertStorageEq(CallResult memory a, CallResult memory b, string memory ctx)
        internal
        view
    {{
        _assertSlots(a.writes, ctx);
        _assertSlots(b.writes, ctx);
    }}

    function _assertSlots(bytes32[] memory slots, string memory ctx) internal view {{
        for (uint256 i = 0; i < slots.length; i++) {{
            assertEq(
                vm.load(original, slots[i]),
                vm.load(candidate, slots[i]),
                string.concat(ctx, ": storage mismatch at slot ", vm.toString(slots[i]))
            );
        }}
    }}

    /// @dev Views are compared through a plain call so a reverting getter is a
    ///      status mismatch on both sides, never a harness failure.
    function _assertViewEq(bytes memory payload, string memory ctx) internal {{
        (bool okA, bytes memory retA) = original.call(payload);
        (bool okB, bytes memory retB) = candidate.call(payload);
        assertEq(okA, okB, string.concat(ctx, ": view revert-status mismatch"));
        assertEq(retA, retB, string.concat(ctx, ": view returndata mismatch"));
    }}

    function _assertLogsEq(CallResult memory a, CallResult memory b, string memory ctx)
        internal
        pure
    {{
        assertEq(a.logs.length, b.logs.length, string.concat(ctx, ": event count mismatch"));
        for (uint256 i = 0; i < a.logs.length; i++) {{
            assertEq(
                a.logs[i].topics.length,
                b.logs[i].topics.length,
                string.concat(ctx, ": event topic count mismatch")
            );
            for (uint256 j = 0; j < a.logs[i].topics.length; j++) {{
                assertEq(
                    a.logs[i].topics[j],
                    b.logs[i].topics[j],
                    string.concat(ctx, ": event topic mismatch")
                );
            }}
            assertEq(a.logs[i].data, b.logs[i].data, string.concat(ctx, ": event data mismatch"));
        }}
    }}

    /// @dev Emitted for the Python side to parse, and gated here so a
    ///      regression fails the run on its own.
    function _reportGas(string memory name, CallResult memory a, CallResult memory b)
        internal
        pure
    {{
        console.log(
            string.concat(
                "GASRESULT|", name,
                "|", vm.toString(a.gas),
                "|", vm.toString(b.gas),
                "|", a.ok ? "1" : "0",
                "|", b.ok ? "1" : "0"
            )
        );
        assertEq(a.ok, b.ok, string.concat(name, ": gas bench revert-status mismatch"));
        if (a.ok) {{
            assertLe(b.gas, a.gas, string.concat(name, ": gas regressed"));
        }}
    }}

    /*//////////////////////////////////////////////////////////////
                              STATE SEEDING
    //////////////////////////////////////////////////////////////*/

    function _slot1(bytes32 key, uint256 base) internal pure returns (bytes32) {{
        return keccak256(abi.encode(key, base));
    }}

    function _slot2(bytes32 key1, bytes32 key2, uint256 base) internal pure returns (bytes32) {{
        return keccak256(abi.encode(key2, keccak256(abi.encode(key1, base))));
    }}

    function _seedBoth(bytes32 slot, uint256 value) internal {{
        vm.store(original, slot, bytes32(value));
        vm.store(candidate, slot, bytes32(value));
    }}

    function _seedSlot(uint256 slot, uint256 value) internal {{
        _seedBoth(bytes32(slot), value);
    }}
}}
"""


# --- entry point -------------------------------------------------------------


def generate(
    original: ContractArtifact,
    candidate: ContractArtifact,
    max_array_length: int = 5,
) -> Harness:
    """Build the harness sources for a contract pair.

    Raises GenerationError if the pair cannot be verified honestly — a missing
    function or an undrivable parameter type is a hard failure, never a comment
    in the generated file.
    """
    if not original.functions:
        raise GenerationError(
            f"{original.name} exposes no public functions; there is nothing to "
            f"verify or to optimize."
        )

    missing = sorted(f.signature for f in original.functions if not candidate.find(f.signature))
    if missing:
        raise GenerationError(
            "candidate is missing functions present in the original: " + ", ".join(missing)
        )
    added = sorted(f.signature for f in candidate.functions if not original.find(f.signature))
    if added:
        raise GenerationError(
            "candidate exposes functions the original does not: " + ", ".join(added)
        )

    for func in original.functions:
        for p in func.inputs:
            try:
                st.check_supported(p.canonical_type)
            except st.UnsupportedType as exc:
                raise GenerationError(
                    f"cannot drive {func.signature}: {exc}. Refusing to emit a harness "
                    f"that would silently skip it."
                ) from exc

    equivalence, covered = _equivalence_source(original, candidate, max_array_length)
    gas, benched = _gas_source(original, candidate)

    return Harness(
        files={
            BASE_FILE: HARNESS_BASE,
            EQUIVALENCE_FILE: equivalence,
            GAS_BENCH_FILE: gas,
        },
        covered=tuple(covered),
        benched=tuple(benched),
    )
