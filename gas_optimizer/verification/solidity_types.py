"""
ABI type helpers shared by the equivalence and gas harness generators.

Only elementary types and arrays of elementary types are supported. Anything
else (structs/tuples, nested arrays, mapping params) raises `UnsupportedType`:
a function we cannot drive is a function we cannot claim to have verified, so
the generator refuses to emit a harness rather than quietly skipping it.
"""

from __future__ import annotations

import re

ELEMENTARY = (
    {"address", "bool", "string", "bytes"}
    | {f"uint{8 * n}" for n in range(1, 33)}
    | {f"int{8 * n}" for n in range(1, 33)}
    | {f"bytes{n}" for n in range(1, 33)}
)

_ARRAY = re.compile(r"^(.*?)\[(\d*)\]$")

# Reference types need an explicit data location in a function signature.
_NEEDS_MEMORY = {"string", "bytes"}


class UnsupportedType(Exception):
    """An ABI type the generator cannot fuzz or build a literal for."""


def is_array(abi_type: str) -> bool:
    return bool(_ARRAY.match(abi_type))


def split_array(abi_type: str) -> tuple[str, int | None]:
    """`uint256[]` -> ("uint256", None); `uint256[3]` -> ("uint256", 3)."""
    m = _ARRAY.match(abi_type)
    if not m:
        raise ValueError(f"{abi_type} is not an array type")
    base, size = m.group(1), m.group(2)
    return base, int(size) if size else None


def check_supported(abi_type: str) -> None:
    """Raise UnsupportedType unless we can fuzz and build literals for it."""
    if abi_type in ELEMENTARY:
        return
    if is_array(abi_type):
        base, _ = split_array(abi_type)
        if base in ELEMENTARY:
            return
        raise UnsupportedType(f"arrays of {base} are not supported")
    raise UnsupportedType(f"unsupported ABI type: {abi_type}")


def declaration(abi_type: str, name: str) -> str:
    """A parameter declaration usable in a test function signature."""
    check_supported(abi_type)
    if is_array(abi_type) or abi_type in _NEEDS_MEMORY:
        return f"{abi_type} memory {name}"
    return f"{abi_type} {name}"


def is_unsigned(abi_type: str) -> bool:
    return abi_type.startswith("uint") and not is_array(abi_type)


def is_address(abi_type: str) -> bool:
    return abi_type == "address"


def literal(abi_type: str, seed_expr: str = "SEED / 2") -> str:
    """A deterministic literal for the gas benchmark and constructor args."""
    check_supported(abi_type)

    if abi_type == "address":
        return "BENCH_ACTOR"
    if abi_type == "bool":
        return "true"
    if abi_type == "string":
        return '"gas"'
    if abi_type == "bytes":
        return 'hex"00112233"'
    if abi_type.startswith("uint"):
        return seed_expr if abi_type == "uint256" else f"{abi_type}({seed_expr})"
    if abi_type.startswith("int"):
        return f"{abi_type}(int256({seed_expr}))"
    if abi_type.startswith("bytes"):  # bytesN
        return f"{abi_type}(bytes32(uint256(1)))"

    raise UnsupportedType(f"no literal for {abi_type}")


def array_literal_block(abi_type: str, name: str, indent: str = "        ") -> list[str]:
    """Statements declaring a small fixed array literal named `name`."""
    base, fixed = split_array(abi_type)
    length = fixed if fixed is not None else 2
    if fixed is None:
        lines = [f"{indent}{base}[] memory {name} = new {base}[]({length});"]
    else:
        lines = [f"{indent}{base}[{fixed}] memory {name};"]
    for i in range(length):
        lines.append(f"{indent}{name}[{i}] = {literal(base)};")
    return lines
