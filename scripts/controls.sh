#!/usr/bin/env bash
# Fixture pairs with known verdicts, run through the real CLI. Each rejection
# corresponds to a soundness hole the harness used to have; each acceptance
# pins a capability. Exit codes matter: 0 = accepted, 1 = rejected, 2 =
# environment problem — an environment failure must never pass as a rejection.
set -uo pipefail
cd "$(dirname "$0")/.."

FUZZ="${FUZZ_RUNS:-500}"
FAILED=0

verdict() {
    gas-optimize validate "tests/fixtures/$1" "tests/fixtures/$2" \
        --fuzz-runs "$FUZZ" -q >/dev/null 2>&1
    echo $?
}

expect() { # expect <accept|reject> <original> <candidate> <why>
    local want="$1" original="$2" candidate="$3" why="$4"
    local rc
    rc=$(verdict "$original" "$candidate")
    case "$want:$rc" in
        accept:0) echo "ok   accepted  $original vs $candidate" ;;
        reject:1) echo "ok   rejected  $original vs $candidate" ;;
        *:2)
            echo "::error::environment failure while checking $candidate"
            FAILED=1 ;;
        *)
            echo "::error::$candidate should have been ${want}ed: $why"
            FAILED=1 ;;
    esac
}

expect accept ERC20.sol    ERC20Candidate.sol   "equivalent and cheaper"
expect accept Vault.sol    VaultCandidate.sol   "guarded unchecked subtraction is sound"
expect accept Registry.sol RegistryViewOpt.sol  "a view-only saving satisfies the gas gate"
expect reject ERC4626.sol  ERC4626Candidate.sol "wraps where the original reverts"
expect reject Vault.sol    VaultBadReturn.sol   "returns the wrong value"
expect reject Emitter.sol  EmitterCandidate.sol "stops emitting an event"
expect reject Counter.sol  CounterBadView.sol   "the getter lies about state"

exit "$FAILED"
