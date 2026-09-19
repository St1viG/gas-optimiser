# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-19

### Added
- **View/pure functions are now verified and gas-benched.** Every public
  function gets differential fuzz tests, mutating tests re-check every
  zero-argument view over the mutated state, and view savings can satisfy the
  strictly-cheaper gate. Two new control fixtures pin this
  (`Counter`/`CounterBadView` rejected, `Registry`/`RegistryViewOpt` accepted).
- Structured progress events (`gas_optimizer.events`): the optimizer and
  validator emit a typed event stream consumed by every frontend.
- Subcommand CLI: `gas-optimize run|validate|config|doctor|tui|gui`, with
  `--fuzz-runs`, `--max-retries`, `--model`, `--hevm/--no-hevm`,
  `--forge-timeout`, `--json` (final object) and `--stream-json` (JSONL
  events). The old `gas-optimize <file>` form still works.
- `gas-optimize doctor`: preflight report for forge, hevm, HF_TOKEN and the
  workspace. `run`/`validate` fail fast with exit code 2 when the environment
  is broken instead of blaming the model.
- Run history: every run is recorded under `runs/<id>/` (events, original,
  accepted contract, outcome + diff). Accepted contracts are also written to
  `<input dir>/<Contract>.optimized.sol` by default.
- Terminal UI (`gas-optimize tui`, extra `[tui]`) and a local web GUI
  (`gas-optimize gui`, extra `[gui]`): chat-style dark interface with run
  history, live gate progress, gas table and diff.
- Layered TOML configuration (`gas-optimizer.toml`, gitignored;
  `gas-optimizer.example.toml` is the template): CLI flag > `GAS_OPTIMIZER_*`
  env > TOML > defaults. `gas-optimize config show|init|set|migrate`.
- Dev tooling: Makefile, pre-commit config, mypy (strict on the core), CI
  caching, LICENSE file (GPL-3.0), single-sourced version.

### Changed
- **Behavior change:** candidates that alter what a view returns are now
  rejected, and contracts whose views take unsupported parameter types
  (structs, nested arrays) fail harness generation instead of passing
  unverified.
- hevm notes (proved equivalent / timed out / skipped) now survive to the
  final result, the CLI summary, and the run record.
- A missing `forge` binary is reported as `environment_error` and stops the
  loop immediately (previously misreported as `compile_error` for 5 attempts).
- Exit codes: 0 accepted, 1 rejected/failed, 2 environment or usage error.

### Deprecated
- `validatorConfig.txt` — still read (with a warning) when no TOML config
  exists; convert with `gas-optimize config migrate`. Will be removed in 0.3.
- `python -m gas_optimizer.validator` — now a shim over `gas-optimize
  validate` / `gas-optimize config`. Will be removed in 0.3.

### Removed
- `--hevm-enable`/`--hevm-disable` (mutated a tracked file). Use the per-run
  `--hevm` flag or `gas-optimize config set hevm.enabled true`.

## [0.1.0]

Initial release: generate-and-verify loop, differential fuzzing harness,
gas gate, advisory hevm check, argparse CLI.
