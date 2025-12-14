# Solidity Equivalence Validator

Validates that two Solidity contracts are functionally equivalent using:

1. **Fuzz Testing** (Foundry) - Fast random testing
2. **Symbolic Execution** (hevm) - Formal verification (optional)

## Project Structure

```
project/
├── validator.py              # Main orchestrator
├── fuzz_test_generator.py    # Generates Foundry fuzz tests
├── validatorConfig.txt       # Configuration file
├── src/
│   ├── ContractName.sol          # Original contract
│   └── ContractNameCandidate.sol # Candidate (optimized) contract
└── test/
    └── EquivalenceTest.t.sol     # Auto-generated fuzz tests
```

## Requirements

- Python 3.10+
- Foundry (forge)
- hevm (optional, can be disabled)

## Configuration

Edit `validatorConfig.txt`:

```
# Fuzz test iterations
fuzz_runs=1000

# Enable/disable hevm (true/false)
hevm_enabled=true

# Hevm overall timeout in seconds
hevm_timeout=300

# Z3 solver timeout per query in milliseconds
hevm_solver_timeout=30000

# Max loop iterations (prevents infinite loops)
hevm_max_iterations=5

# Max array length for fuzz testing
max_array_length=5
```

## Usage

### Command Line

```bash
# Validate contracts
python3 validator.py ERC20

# Enable hevm symbolic verification
python3 validator.py --hevm-enable

# Disable hevm (fuzz testing only)
python3 validator.py --hevm-disable

# Show current configuration
python3 validator.py --config
```

### Programmatic

```python
from validator import Validate

# Returns (is_equivalent: bool, counterexamples: list)
success, counterexamples = Validate("ERC20")

if success:
    print("Contracts are equivalent!")
else:
    if counterexamples:
        print("Found differences:", counterexamples)
    else:
        print("Hevm timed out - inconclusive")
```

## Return Values

The `Validate()` function returns `(bool, list)`:

| Result         | Return Value          | Meaning                                                         |
| -------------- | --------------------- | --------------------------------------------------------------- |
| Equivalent     | `(True, [])`          | Fuzz + hevm confirm equivalence (or fuzz only if hevm disabled) |
| Not Equivalent | `(False, [examples])` | Found up to 5 counterexamples                                   |
| Timeout        | `(False, [])`         | Fuzz passed, but hevm timed out                                 |

## Hevm Troubleshooting

If hevm gets stuck or times out frequently:

1. **Disable hevm** (use fuzz testing only):

   ```bash
   python3 validator.py --hevm-disable
   ```

2. **Reduce solver timeout** in `validatorConfig.txt`:

   ```
   hevm_solver_timeout=10000  # 10 seconds per query
   ```

3. **Reduce max iterations** (for contracts with loops):

   ```
   hevm_max_iterations=3
   ```

4. **Increase fuzz runs** to compensate:
   ```
   fuzz_runs=10000
   ```

**Why hevm gets stuck:**

- Complex math (multiplication, division with symbolic values)
- Unbounded loops
- Nested mappings with symbolic keys
- Large state spaces

## Examples

### Testing Identical Contracts (ERC1155)

```bash
python3 validator.py ERC1155
# Expected: EQUIVALENT ✅
```

### Testing Buggy Contract (ERC20)

```bash
python3 validator.py ERC20
# Expected: NOT EQUIVALENT ❌ (candidate adds +4 to transfers)
```

### Fast Mode (Fuzz Only)

```bash
python3 validator.py --hevm-disable
python3 validator.py ERC1155
# Skips hevm, only runs fuzz tests
```

## How It Works

1. **Generate Tests**: Creates `test/EquivalenceTest.t.sol` with fuzz tests
2. **Run Fuzz Tests**: Executes `forge test --fuzz-runs N`
3. **If Fuzz Passes & Hevm Enabled**: Runs `hevm equivalence` for symbolic verification
4. **Report Results**: Returns equivalence status and any counterexamples
