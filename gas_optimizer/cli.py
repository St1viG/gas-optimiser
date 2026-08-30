"""
Command-line entry point.

    python run.py test-cases/tc2.txt
    python -m gas_optimizer test-cases/tc2.txt
    gas-optimize test-cases/tc2.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config, optimizer

_DESCRIPTION = """\
Optimize a Solidity contract for runtime gas, accepting a candidate only if it
is provably equivalent under differential fuzzing and measurably cheaper.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gas-optimize",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: gas-optimize test-cases/tc2.txt",
    )
    parser.add_argument("input", type=Path, help="Solidity source to optimize (.sol or .txt)")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="write the accepted contract here as well as to foundry/src/",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress per-stage validator output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not args.input.is_file():
        print(f"[ERROR] File not found: {args.input}", file=sys.stderr)
        return 1

    try:
        original_code = args.input.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[ERROR] Could not read {args.input}: {exc}", file=sys.stderr)
        return 1

    if not original_code.strip():
        print(f"[ERROR] Input file is empty: {args.input}", file=sys.stderr)
        return 1

    print(f"[LOAD] {args.input}")
    result = optimizer.run_optimization_loop(original_code, verbose=not args.quiet)

    print("\n" + "=" * 60)
    if not result.success:
        print(f"FAILED after {result.attempts} attempt(s)")
        print(result.message)
        return 1

    print(f"SUCCESS after {result.attempts} attempt(s)")
    for entry in result.gas:
        print(f"  {entry.describe()}")
    print(f"  total: {result.total_delta:+d} gas")
    print(f"\nCandidate: {result.candidate_path}")
    print(f"Original:  {config.SOL_FOLDER}")

    if args.output and result.optimized_code:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result.optimized_code, encoding="utf-8")
            print(f"Written:   {args.output}")
        except OSError as exc:
            print(f"[WARN] Could not write {args.output}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
