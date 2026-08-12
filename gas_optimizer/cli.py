"""
Command-line entry point for the Solidity gas optimizer.

    python run.py test-cases/tc2.txt
    python -m gas_optimizer test-cases/tc2.txt
"""

import sys
from pathlib import Path

from . import config
from . import optimizer


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print("Usage: python run.py <input_file>")
        print()
        print("Example: python run.py test-cases/tc2.txt")
        return 1

    input_file = Path(argv[0])

    if not input_file.is_file():
        print(f"[ERROR] File not found: {input_file}")
        return 1

    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)

    try:
        print(f"[LOAD] Reading '{input_file}'...")
        original_code = input_file.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERROR] Failed to load input: {e}")
        return 1

    if not original_code.strip():
        print(f"[ERROR] Input file is empty: {input_file}")
        return 1

    result = optimizer.run_optimization_loop(original_code)

    if result.success:
        print("\n" + "=" * 50)
        print("SUCCESS! Code optimized and validated.")
        print(f"Results are saved in: {config.SOL_FOLDER}")
        return 0

    print(f"FAILED: {result.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
