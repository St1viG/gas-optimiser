"""
Contract introspection backed by Foundry build artifacts.

`forge build` already produces everything the equivalence harness needs: the
canonical ABI, method selectors, and an exact storage layout with real slot and
offset assignments. Reading that is strictly better than parsing Solidity with
regexes, which cannot see variable packing, inheritance, structs, or constants.

Artifacts live at `foundry/out/<File>.sol/<Contract>.json` and require
`extra_output = ["storageLayout"]` in foundry.toml (see the repo's foundry.toml).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import config


class ArtifactError(RuntimeError):
    """Raised when an artifact is missing or unusable."""


# --- ABI ---------------------------------------------------------------------


@dataclass(frozen=True)
class AbiParam:
    name: str
    type: str
    components: tuple[AbiParam, ...] = ()

    @property
    def canonical_type(self) -> str:
        """The type as it appears in a function selector (tuples expanded)."""
        if not self.components:
            return self.type
        inner = ",".join(c.canonical_type for c in self.components)
        # `tuple`, `tuple[]`, `tuple[3]` -> `(...)`, `(...)[]`, `(...)[3]`
        suffix = self.type[len("tuple"):]
        return f"({inner}){suffix}"


@dataclass(frozen=True)
class AbiFunction:
    name: str
    inputs: tuple[AbiParam, ...]
    outputs: tuple[AbiParam, ...]
    state_mutability: str

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(p.canonical_type for p in self.inputs)})"

    @property
    def is_mutating(self) -> bool:
        return self.state_mutability not in ("view", "pure")

    @property
    def is_payable(self) -> bool:
        return self.state_mutability == "payable"


def _params(raw: list[dict]) -> tuple[AbiParam, ...]:
    return tuple(
        AbiParam(
            name=p.get("name", ""),
            type=p["type"],
            components=_params(p.get("components", [])),
        )
        for p in raw
    )


# --- Storage layout ----------------------------------------------------------


@dataclass(frozen=True)
class StorageEntry:
    label: str
    slot: int
    offset: int
    type_id: str


@dataclass
class StorageLayout:
    entries: tuple[StorageEntry, ...] = ()
    types: dict[str, dict] = field(default_factory=dict)

    def encoding(self, type_id: str) -> str:
        return self.types.get(type_id, {}).get("encoding", "inplace")

    def label(self, type_id: str) -> str:
        return self.types.get(type_id, {}).get("label", type_id)

    def num_bytes(self, type_id: str) -> int:
        return int(self.types.get(type_id, {}).get("numberOfBytes", 32))

    def key_type(self, type_id: str) -> str | None:
        """Canonical label of a mapping's key type, or None if not a mapping."""
        info = self.types.get(type_id)
        if not info or info.get("encoding") != "mapping":
            return None
        return self.label(info["key"])

    def value_type_id(self, type_id: str) -> str | None:
        info = self.types.get(type_id)
        if not info or info.get("encoding") != "mapping":
            return None
        return info["value"]

    def mapping_key_chain(self, type_id: str) -> list[str]:
        """Key type labels for a (possibly nested) mapping, outermost first."""
        chain: list[str] = []
        current: str | None = type_id
        while current is not None and self.encoding(current) == "mapping":
            key = self.key_type(current)
            if key is None:
                break
            chain.append(key)
            current = self.value_type_id(current)
        return chain

    def mapping_value_encoding(self, type_id: str) -> str:
        """Encoding of the innermost value behind a chain of mappings."""
        current = type_id
        while self.encoding(current) == "mapping":
            nxt = self.value_type_id(current)
            if nxt is None:
                break
            current = nxt
        return self.encoding(current)

    @property
    def mappings(self) -> tuple[StorageEntry, ...]:
        return tuple(e for e in self.entries if self.encoding(e.type_id) == "mapping")

    @property
    def plain_values(self) -> tuple[StorageEntry, ...]:
        """Entries stored directly in a slot (packed scalars included)."""
        return tuple(e for e in self.entries if self.encoding(e.type_id) == "inplace")


def _storage_layout(raw: dict | None) -> StorageLayout:
    if not raw:
        return StorageLayout()
    return StorageLayout(
        entries=tuple(
            StorageEntry(
                label=e["label"],
                slot=int(e["slot"]),
                offset=int(e["offset"]),
                type_id=e["type"],
            )
            for e in raw.get("storage", [])
        ),
        types=raw.get("types") or {},
    )


# --- Artifact ----------------------------------------------------------------


@dataclass
class ContractArtifact:
    name: str
    source_name: str
    functions: tuple[AbiFunction, ...]
    storage: StorageLayout
    deployed_bytecode: str
    constructor_inputs: tuple[AbiParam, ...] = ()

    @property
    def mutating_functions(self) -> tuple[AbiFunction, ...]:
        return tuple(f for f in self.functions if f.is_mutating)

    @property
    def view_functions(self) -> tuple[AbiFunction, ...]:
        return tuple(f for f in self.functions if not f.is_mutating)

    def signatures(self) -> set[str]:
        return {f.signature for f in self.functions}

    def find(self, signature: str) -> AbiFunction | None:
        return next((f for f in self.functions if f.signature == signature), None)


def artifact_path(source: Path, contract: str, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (config.FOUNDRY_DIR / "out")
    return out_dir / Path(source).name / f"{contract}.json"


def load(source: Path, contract: str, out_dir: Path | None = None) -> ContractArtifact:
    """Load the build artifact for `contract` as declared in `source`.

    Raises ArtifactError if the artifact is missing (run `forge build` first) or
    was produced without `extra_output = ["storageLayout"]`.
    """
    path = artifact_path(source, contract, out_dir)
    if not path.exists():
        raise ArtifactError(
            f"No build artifact at {path}. Run `forge build` in {config.FOUNDRY_DIR} first."
        )

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"Could not read artifact {path}: {exc}") from exc

    if "storageLayout" not in raw:
        raise ArtifactError(
            f"{path} has no storageLayout. Add `extra_output = [\"storageLayout\"]` "
            f"to foundry.toml and rebuild."
        )

    functions = tuple(
        AbiFunction(
            name=e["name"],
            inputs=_params(e.get("inputs", [])),
            outputs=_params(e.get("outputs", [])),
            state_mutability=e.get("stateMutability", "nonpayable"),
        )
        for e in raw.get("abi", [])
        if e.get("type") == "function"
    )

    ctor = next((e for e in raw.get("abi", []) if e.get("type") == "constructor"), None)

    deployed = raw.get("deployedBytecode") or {}
    if isinstance(deployed, dict):
        deployed = deployed.get("object", "")

    return ContractArtifact(
        name=contract,
        source_name=Path(source).name,
        functions=functions,
        storage=_storage_layout(raw.get("storageLayout")),
        deployed_bytecode=deployed or "",
        constructor_inputs=_params(ctor.get("inputs", [])) if ctor else (),
    )
