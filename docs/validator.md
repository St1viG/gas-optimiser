# Validator internals

`gas_optimizer.validator` decides whether a candidate contract may replace an
original. It answers one question — *is this equivalent and actually cheaper?* —
and it is designed so that the answer is never accidentally "yes".

## Gates

A candidate must clear all of these in order. Failing any one produces a
structured failure that the optimizer feeds back to the model.

| Gate | Failure type | What it catches |
| --- | --- | --- |
| Declared name differs from the original | `shape_error` | A candidate that would overwrite the original and be compared against itself |
| `forge build` | `compile_error` | Source that does not compile |
| Harness generation | `harness_error` | A changed public ABI, or a parameter type the harness cannot drive |
| Differential fuzzing | `equivalence_error` | Any behavioural difference |
| Gas benchmark | `gas_error` | A regression, or no improvement at all |
| `hevm equivalence` (optional) | `symbolic_counterexample` | Divergence the fuzzer missed |

## Introspection

Contract shape comes from Foundry build artifacts at
`foundry/out/<File>.sol/<Contract>.json` — the ABI, method selectors, and the
compiler's own `storageLayout`. This requires `extra_output = ["storageLayout"]`
in `foundry/foundry.toml`.

Nothing parses Solidity with regexes. Variable packing, inheritance, structs and
constants are the compiler's business, and slot numbers derived from declaration
order are wrong for all four.

## The equivalence harness

`verification/harness_generator.py` emits three files into `foundry/test/`:

- `HarnessBase.sol` — shared machinery
- `EquivalenceTest.t.sol` — differential fuzz tests
- `GasBench.t.sol` — deterministic gas comparison

Each mutating function is exercised like this:

```solidity
bytes memory payload = abi.encodeWithSignature("transfer(address,uint256)", to, val);
CallResult memory a = _exec(original, payload, 0);
CallResult memory b = _exec(candidate, payload, 0);
_assertEquivalent(a, b, "transfer(address,uint256)");
```

`_exec` performs a **low-level call** and records everything observable:

| Captured | How | Why it matters |
| --- | --- | --- |
| revert status | `.call` return flag | A reverting *original* is a pass when the candidate reverts too, not a false failure |
| return data | `.call` return bytes | Compares return values *and* revert reasons in one assertion |
| storage writes | `vm.record` + `vm.accesses` | Slots are discovered at runtime; no layout arithmetic is assumed |
| events | `vm.recordLogs` + `vm.getRecordedLogs` | Topics and data compared per log |
| gas | `vm.lastCallGas().gasTotalUsed` | Callee-perspective cost, excluding harness overhead |

`_assertStorageEq` compares the **union** of slots either side wrote, so a
candidate writing a slot the original never touches is caught too.

### Two fuzz variants per function

- `testFuzz_<fn>_raw` — arguments unbounded. Overflow and underflow divergence
  introduced by `unchecked` is reachable here and nowhere else.
- `testFuzz_<fn>_bounded` — arguments bounded to seeded ranges, so the happy
  path is explored deeply instead of bouncing off guard clauses.

Both are needed. Bounding everything hides unsound `unchecked` arithmetic;
bounding nothing means almost every input reverts on both sides and proves
little.

### State seeding

Mappings and full-width scalar slots are seeded to `SEED` (`1_000_000 ether`) on
**both** contracts before the call, using slot arithmetic derived from the
compiler's storage layout. Interesting keys are the address arguments plus
`address(this)`; nested mappings are seeded over the cross product.

`SEED` is deliberately far from `2**256`: seeding must not be able to manufacture
an overflow that no realistic deployment would ever reach.

### Nothing is skipped silently

If a function cannot be driven — an unsupported parameter type, a signature the
candidate does not have — generation raises `GenerationError` and the candidate
is rejected. A harness that emits zero tests is likewise a failure, not a pass.

## The gas gate

`GasBench.t.sol` measures each function once with fixed representative
arguments, under `forge test --isolate`. Each measurement:

1. logs `GASRESULT|<signature>|<original>|<candidate>|<okA>|<okB>` for the
   Python side to parse;
2. asserts `assertLe(candidateGas, originalGas)`, so a regression fails on its own.

Python additionally requires at least one function to be **strictly** cheaper.
Set `require_gas_improvement=false` in `validatorConfig.txt` to measure and
report without blocking.

`foundry.toml` enables the solc optimizer. Gas measured with it off does not
describe any real deployment.

## hevm

Advisory and off by default. Bytecode is read from the build artifact's
`deployedBytecode.object`, not scraped from `forge inspect` output.

The verdict is read from hevm's own wording, not from its exit status alone:

| hevm says | Verdict |
| --- | --- |
| `Contracts behave equivalently` / `No discrepancies found` | equivalent |
| `Contracts do not behave equivalently` / `Not equivalent` | **rejected**, with counterexamples |
| anything else — crash, rejected flag, solver timeout | **skipped**, recorded in `notes` |

Order matters when matching: `do not behave equivalently` contains
`behave equivalently` as a substring, so failure is tested first.

### hevm is much stricter than the fuzzer

hevm quantifies over **arbitrary storage**. The fuzz harness only explores
states reachable from its seeding (`SEED = 1_000_000 ether`), which is
deliberately far from `2**256`.

That difference is real, not theoretical. `tests/fixtures/ERC20Candidate.sol`
passes differential fuzzing and is rejected by hevm, correctly: given
`balanceOf[to]` near `2**256`, the original reverts with `Panic(0x11)` while the
`unchecked` candidate wraps and returns `true`. A deployed ERC20 maintains
`sum(balances) == totalSupply` so that state is unreachable in practice — but
hevm does not know your invariants and cannot assume them.

Practical consequence: **enabling hevm rejects most `unchecked` transforms**,
which are the bulk of what this optimizer proposes. Treat `hevm_enabled=true` as
a "prove it under arbitrary storage" mode, not the everyday setting.

### Installing hevm

Static binaries exist for macOS (arm64 and x86_64) and Linux x86_64; there is
no Windows or Linux-arm64 build, and no official container image. `z3` is
required separately — it is not bundled.

```bash
# macOS (Apple Silicon)
curl -sSL -o /usr/local/bin/hevm \
  https://github.com/ethereum/hevm/releases/download/release/0.58.0/hevm-arm64-macos
chmod +x /usr/local/bin/hevm
brew install z3
```

## Configuration

`validatorConfig.txt`:

| Key | Meaning |
| --- | --- |
| `fuzz_runs` | Fuzz iterations per generated test |
| `require_gas_improvement` | Reject candidates that are not strictly cheaper |
| `forge_timeout` | Per-command timeout for `forge`, in seconds |
| `max_array_length` | Cap applied to fuzzed dynamic arrays |
| `hevm_enabled` | Run the symbolic check |
| `hevm_timeout` | Overall hevm timeout (seconds) |
| `hevm_solver` | SMT solver: `z3`, `bitwuzla` or `cvc5` |
| `hevm_smt_timeout` | Per-query SMT timeout, in **seconds** (`--smt-timeout`) |
| `hevm_max_iterations` | Loop-unroll bound |

## Usage

```bash
# compare two contracts directly, no model involved
python -m gas_optimizer.validator tests/fixtures/ERC20.sol tests/fixtures/ERC20Candidate.sol

python -m gas_optimizer.validator --config
python -m gas_optimizer.validator --hevm-enable
python -m gas_optimizer.validator --hevm-disable
```

Exit code is `0` when the candidate is accepted, `1` otherwise.

Programmatically:

```python
from gas_optimizer import validator

result = validator.validate("path/to/Original.sol", "path/to/Candidate.sol")

if result.ok:
    print(result.gas_summary())        # per-function deltas
else:
    print(result.failure["type"])      # e.g. "equivalence_error"
    print(result.failure["error"])     # the assertion that fired
    print(result.failure["trace"])     # counterexample arguments
```

## Workspace

`foundry/src/` and `foundry/test/` are scratch. Both are cleared at the start of
every validation run and repopulated. Inputs live in `test-cases/`, fixtures in
`tests/fixtures/`; nothing under `foundry/src` or `foundry/test` is source.

## Known limitations

- Parameter types are limited to elementary types and arrays of them. Structs
  and nested arrays cause an explicit `harness_error` rather than a silent skip.
- Contracts are deployed with literal constructor arguments, identical on both
  sides. Constructor-dependent behaviour is not explored.
- `vm.lastCallGas()` is deprecated in newer Foundry in favour of `lastFrameGas`,
  which is not present in the vendored forge-std 1.12.0. The warning is
  cosmetic; switch when forge-std is next updated.
