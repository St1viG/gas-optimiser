# Developer entry points. Everything here is also what CI runs.

PYTHON ?= python

.PHONY: install lint format typecheck test test-all controls run-tui run-gui clean

install:            ## editable install with every extra
	$(PYTHON) -m pip install -e ".[dev,ui]"

lint:               ## style + import order, no changes applied
	ruff check .
	ruff format --check .

format:             ## apply formatting
	ruff format .
	ruff check --fix .

typecheck:
	mypy gas_optimizer

test:               ## fast: unit tests only, no forge
	pytest -q -m "not foundry"

test-all:           ## the whole suite, forge required
	pytest -q

controls:           ## fixture pairs with known verdicts through the real CLI
	./scripts/controls.sh

run-tui:
	gas-optimize tui

run-gui:
	gas-optimize gui

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -not -path "./foundry/lib/*" -exec rm -rf {} +
