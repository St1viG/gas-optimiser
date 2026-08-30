#!/usr/bin/env python3
"""
Entry point for the gas optimizer pipeline.

    python run.py test-cases/tc2.txt

Equivalent to `python -m gas_optimizer` and to the `gas-optimize` console
script installed by `pip install -e .`.
"""

import sys

from gas_optimizer.cli import main

if __name__ == "__main__":
    sys.exit(main())
