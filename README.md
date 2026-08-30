# AI-Powered Solidity Gas Optimizer

Optimizes Solidity contracts for runtime gas using an LLM, wrapped in a
generate-and-verify loop. A proposal is accepted only if it is **provably
equivalent** to the original under differential fuzzing **and measurably
cheaper**.

Both halves matter. Equivalence alone accepts a candidate that changed nothing.

## How it works

```
test-cases/tc2.txt                     input contract
        │
        ▼
  model returns a complete contract     gas_optimizer/llm_client.py
        │
        ├─ shape check (name, pragma, non-empty, changed)   utils.py
        ▼
  foundry/src/ERC20.sol                 original
  foundry/src/ERC20Candidate.sol        candidate
        │
        ▼
  forge build ──▶ ABI + storage layout  verification/artifacts.py
        │
        ▼
  harness generated                     verification/harness_generator.py
        │
        ├─ EquivalenceTest.t.sol   differential fuzzing
        │     revert status · return data · storage writes · events
        │
        ├─ GasBench.t.sol          per-function gas, must not regress
        │
        └─ hevm equivalence        optional, advisory
        │
        ├── all gates pass ──▶ accepted, with a gas delta
        └── any gate fails ──▶ structured feedback to the model, retry (max 5)
```

The equivalence check is a **low-level call** to each contract with identical
calldata from identical state. That is what lets a reverting original count as a
pass (when the candidate reverts identically) and what makes return values and
revert reasons comparable as bytes. See [docs/validator.md](docs/validator.md)
for the details.

## Setup

Requires **Python 3.10+**, [Foundry](https://getfoundry.sh) (`forge`), and
optionally [hevm](https://github.com/ethereum/hevm).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env        # then add your Hugging Face token
```

`.env` is gitignored. The token is read from the environment at call time.

## Usage

```bash
gas-optimize test-cases/tc2.txt          # or: python run.py test-cases/tc2.txt
gas-optimize test-cases/tc4.txt -o optimized/Vault.sol
```

Compare two contracts directly, no model involved:

```bash
python -m gas_optimizer.validator tests/fixtures/ERC20.sol tests/fixtures/ERC20Candidate.sol
```

```
[VALIDATE] ERC20 (ERC20.sol) vs ERC20Candidate (ERC20Candidate.sol)
[VALIDATE] Building...
[VALIDATE] Generating equivalence + gas harness...
[VALIDATE] Covering 1 function(s): transfer(address,uint256)
[VALIDATE] Differential fuzzing (2000 runs)...
[VALIDATE] Equivalence holds.
[VALIDATE] Measuring gas...

ACCEPTED: equivalent and cheaper
  transfer(address,uint256): 32230 -> 32145 gas (-85)
  total: -85 gas
```

Inspect or toggle verification settings:

```bash
python -m gas_optimizer.validator --config
python -m gas_optimizer.validator --hevm-enable
python -m gas_optimizer.validator --hevm-disable
```

## Layout

```
├── run.py                  entry point (thin shim over gas_optimizer.cli)
├── pyproject.toml          package + pinned dependencies
├── validatorConfig.txt     fuzz/gas/hevm tunables
├── gas_optimizer/
│   ├── cli.py              argument handling
│   ├── config.py           paths + environment
│   ├── llm_client.py       Hugging Face Inference, with retry/backoff
│   ├── prompts.py          system/user prompts
│   ├── optimizer.py        the generate-and-verify loop
│   ├── utils.py            response cleaning + pre-compile shape checks
│   ├── validator.py        the gates
│   └── verification/
│       ├── artifacts.py            ABI + storage layout from build output
│       ├── solidity_types.py       ABI type helpers
│       └── harness_generator.py    emits the equivalence + gas harness
├── foundry/                Foundry workspace (forge runs here)
│   ├── src/, test/         scratch — cleared and regenerated every run
│   └── lib/forge-std       vendored
├── test-cases/             input contracts
├── tests/                  pytest suite + fixture contract pairs
└── docs/validator.md       validator internals
```

## Configuration

`validatorConfig.txt` controls verification depth:

| Key | Meaning | Default |
| --- | --- | --- |
| `fuzz_runs` | Fuzz iterations per generated test | `2000` |
| `require_gas_improvement` | Reject a candidate that is not strictly cheaper | `true` |
| `forge_timeout` | Per-command timeout for `forge`, in seconds | `900` |
| `max_array_length` | Cap applied to fuzzed dynamic arrays | `5` |
| `hevm_enabled` | Run the symbolic equivalence proof | `false` |
| `hevm_timeout` | Overall hevm timeout (seconds) | `300` |
| `hevm_solver` | SMT solver (`z3`, `bitwuzla`, `cvc5`) | `z3` |
| `hevm_smt_timeout` | Per-query SMT timeout, in **seconds** | `30` |
| `hevm_max_iterations` | Loop-unroll bound for hevm | `5` |

Environment (`.env`):

| Variable | Meaning |
| --- | --- |
| `HF_TOKEN` | Hugging Face token (required to run the loop) |
| `MODEL_ID` | Defaults to `Qwen/Qwen2.5-Coder-32B-Instruct` |
| `HF_PROVIDER` | Inference provider; empty lets the client choose |
| `MAX_RETRIES` | Attempts at the optimize-and-verify loop (default 5) |
| `API_RETRIES` | Attempts at a single API call (default 3) |

## Tests

```bash
pytest                       # full suite
pytest -m "not foundry"      # unit only, no forge required
```

No API token is needed: the suite drives the loop with a scripted model. The
fixture pairs in `tests/fixtures/` are controls with known verdicts —

| Pair | Verdict | What it pins down |
| --- | --- | --- |
| `ERC20` / `ERC20Candidate` | accepted¹ | Guarded `unchecked` add is equivalent and cheaper |
| `Vault` / `VaultCandidate` | accepted | `unchecked` after `require` is sound |
| `ERC4626` / `ERC4626Candidate` | rejected | `unchecked` subtraction wraps where the original reverts |
| `Vault` / `VaultBadReturn` | rejected | Identical storage, wrong return value |
| `Emitter` / `EmitterCandidate` | rejected | Identical storage and return, dropped event |
| `ERC20` / renamed copy | rejected | Equivalent but no gas saved |

¹ Under the fuzz and gas gates. hevm rejects it — see
[hevm is much stricter than the fuzzer](docs/validator.md#hevm-is-much-stricter-than-the-fuzzer).

## Notes

- `foundry/src/` and `foundry/test/` are **scratch**: cleared and regenerated on
  every run. Nothing there is source.
- The candidate is renamed to `<Name>Candidate` on write. Without that both
  files declare the same contract, the candidate overwrites the original, and
  the harness compares a contract to itself.
