# AI-Powered Solidity Gas Optimizer

Automates gas optimization of Solidity contracts using an LLM, wrapped in a strict
**generate-and-verify** loop: every proposed patch must prove it preserves the
contract's behaviour before it is accepted.

## How it works

```
test-cases/tc2.txt                 input contract
        │
        ▼
  LLM proposes a unified diff       gas_optimizer/llm_client.py
        │
        ▼
  patch applied fuzzily             gas_optimizer/utils.py
        │
        ▼
  foundry/src/ERC20.sol             original
  foundry/src/ERC20Candidate.sol    candidate
        │
        ▼
  fuzz test generated + run         gas_optimizer/verification/, forge test
  (optional) hevm equivalence       formal bytecode proof
        │
        ├── pass ──▶ done
        └── fail ──▶ failure details fed back to the LLM, retry (max 5)
```

## Layout

```
├── run.py                  entry point
├── requirements.txt
├── validatorConfig.txt     fuzz/hevm tunables
├── gas_optimizer/          Python package
│   ├── cli.py              argument handling
│   ├── config.py           paths + secrets
│   ├── llm_client.py       HuggingFace Inference API
│   ├── prompts.py          system/user prompts
│   ├── optimizer.py        the generate-and-verify loop
│   ├── utils.py            diff application, contract naming
│   ├── validator.py        equivalence checking
│   └── verification/
│       ├── fuzz_test_generator.py   emits Foundry fuzz tests
│       └── hevm_check.py            standalone symbolic check
├── foundry/                Foundry workspace (forge runs here)
│   ├── foundry.toml
│   ├── src/                originals + generated candidates
│   ├── test/               generated EquivalenceTest.t.sol
│   └── lib/                vendored forge-std, halmos-cheatcodes
├── test-cases/             input contracts
└── docs/validator.md       validator internals
```

## Setup

Requires **Python 3.10+**, [Foundry](https://getfoundry.sh) (`forge`), and
optionally [hevm](https://github.com/ethereum/hevm) for symbolic verification.

```bash
pip install -r requirements.txt

cp .env.example .env        # then add your Hugging Face token
```

`.env` is gitignored. The token is read from the environment at call time — never
hardcode it in source.

## Usage

```bash
python run.py test-cases/tc2.txt
```

Results land in `foundry/src/` as `<Name>.sol` and `<Name>Candidate.sol`.

Validate two contracts directly, without the LLM:

```bash
python -m gas_optimizer.validator foundry/src/ERC20.sol foundry/src/ERC20Candidate.sol
```

Inspect or toggle verification settings:

```bash
python -m gas_optimizer.validator --config
python -m gas_optimizer.validator --hevm-enable
python -m gas_optimizer.validator --hevm-disable
```

## Configuration

`validatorConfig.txt` controls verification depth:

| Key | Meaning |
| --- | --- |
| `fuzz_runs` | Foundry fuzz iterations per function |
| `hevm_enabled` | Run the hevm symbolic equivalence proof |
| `hevm_timeout` | Overall hevm timeout (seconds) |
| `hevm_solver_timeout` | Per-query SMT solver timeout (ms) |
| `hevm_max_iterations` | Loop-unroll bound for hevm |
| `max_array_length` | Max array length used when fuzzing |

Environment variables (`.env`): `HF_TOKEN` (required), `MODEL_ID`, `MAX_RETRIES`.

## Notes

- `foundry/src/*Candidate.sol` and `foundry/test/EquivalenceTest.t.sol` are
  **regenerated** on every run; treat them as output, not source.
- The candidate's contract is renamed to `<Name>Candidate` on write. Without that
  rename both files declare the same contract and the candidate silently
  overwrites the original, producing a test that compares a contract to itself.
