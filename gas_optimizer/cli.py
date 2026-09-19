"""
Command-line entry point.

    gas-optimize run test-cases/tc2.txt
    gas-optimize validate original.sol candidate.sol
    gas-optimize config show | init | set KEY VALUE | migrate
    gas-optimize doctor
    gas-optimize tui [input] / gas-optimize gui

`gas-optimize <file>` (the pre-subcommand form) still works and is treated as
`run <file>`. Exit codes: 0 accepted, 1 rejected/failed, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from . import config, events, optimizer, preflight, render, runstore, settings, utils, validator

COMMANDS = ("run", "validate", "config", "doctor", "tui", "gui")

_DESCRIPTION = """\
Optimize a Solidity contract for runtime gas, accepting a candidate only if it
is provably equivalent under differential fuzzing and measurably cheaper.
"""


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("gas-optimizer")
    except Exception:
        from . import __version__

        return __version__


def _fail(message: str, hint: str = "") -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    if hint:
        print(f"        {hint}", file=sys.stderr)
    return 2


def _preflight(*checks: preflight.Check) -> int | None:
    failed = preflight.require(list(checks))
    for check in failed:
        _fail(f"{check.name}: {check.detail}", check.hint)
    return 2 if failed else None


def _read_source(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.strip() else None


def _settings_overrides(args: argparse.Namespace) -> dict:
    overrides = {
        "fuzz_runs": getattr(args, "fuzz_runs", None),
        "forge_timeout": getattr(args, "forge_timeout", None),
        "hevm_enabled": getattr(args, "hevm", None),
        "model": getattr(args, "model", None),
        "max_retries": getattr(args, "max_retries", None),
    }
    return {key: value for key, value in overrides.items() if value is not None}


def _resolve(args: argparse.Namespace) -> settings.Settings:
    return settings.resolve(_settings_overrides(args), getattr(args, "config", None))


# --- run -----------------------------------------------------------------------


def _output_target(args: argparse.Namespace, contract: str) -> Path | None:
    if args.no_output:
        return None
    if args.output is not None:
        return args.output
    return args.input.parent / f"{contract}.optimized.sol"


def _cmd_run(args: argparse.Namespace) -> int:
    source = _read_source(args.input)
    if source is None:
        return _fail(f"cannot read {args.input} (missing, unreadable, or empty)")

    blocked = _preflight(
        preflight.check_workspace(), preflight.check_forge(), preflight.check_hf_token()
    )
    if blocked:
        return blocked

    try:
        resolved = _resolve(args)
    except settings.SettingsError as exc:
        return _fail(str(exc))

    if args.stream_json:
        sink: events.EventSink = render.JsonlRenderer()
    elif args.json:
        sink = events.NULL_SINK
    else:
        sink = render.ConsoleRenderer(quiet=args.quiet)
        if not args.quiet:
            print(f"[LOAD] {args.input}")

    contract = utils.extract_contract_name(source) or "unknown"

    recorder = None
    if not args.no_save_run:
        recorder = runstore.RunStore().start(
            source,
            contract,
            filename=args.input.name,
            options={**_settings_overrides(args), "model": resolved.llm["model"]},
        )
        sink = events.tee(sink, recorder.sink)

    result = optimizer.run_optimization_loop(
        source,
        verbose=not args.quiet,
        on_event=sink,
        settings=resolved.validator,
        max_retries=resolved.llm["max_retries"],
        model=resolved.llm["model"],
    )

    if result.success and result.optimized_code:
        target = _output_target(args, contract)
        if target is not None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.optimized_code, encoding="utf-8")
                result.output_path = target
            except OSError as exc:
                print(f"[WARN] Could not write {target}: {exc}", file=sys.stderr)

    record = recorder.finish(result) if recorder else None

    if args.json:
        payload = {
            "success": result.success,
            "message": result.message,
            "attempts": result.attempts,
            "total_delta": result.total_delta,
            "gas": [events.jsonable(entry) for entry in result.gas],
            "notes": list(result.notes),
            "output_path": str(result.output_path) if result.output_path else None,
            "run_id": record["id"] if record else None,
            "optimized_code": result.optimized_code,
        }
        print(json.dumps(payload, indent=2))
    elif not args.stream_json:
        print("\n" + "=" * 60)
        if not result.success:
            print(f"FAILED after {result.attempts} attempt(s)")
            print(result.message)
        else:
            print(f"SUCCESS after {result.attempts} attempt(s)")
            for entry in result.gas:
                print(f"  {entry.describe()}")
            print(f"  total: {result.total_delta:+d} gas")
            for note in result.notes:
                print(f"  note: {note}")
            if result.output_path:
                print(f"\nOptimized contract: {result.output_path}")
        if recorder:
            print(f"Run record:         {recorder.directory}")

    return 0 if result.success else 1


# --- validate --------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    blocked = _preflight(preflight.check_workspace(), preflight.check_forge())
    if blocked:
        return blocked

    for path in (args.original, args.candidate):
        if not path.is_file():
            return _fail(f"file not found: {path}")

    try:
        resolved = _resolve(args)
    except settings.SettingsError as exc:
        return _fail(str(exc))

    quiet = args.quiet or args.json
    result = validator.validate(
        args.original,
        args.candidate,
        resolved.validator,
        verbose=not quiet,
        on_event=events.NULL_SINK if quiet else None,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "failure": result.failure,
                    "gas": [events.jsonable(entry) for entry in result.gas],
                    "notes": list(result.notes),
                    "total_delta": result.total_delta,
                },
                indent=2,
            )
        )
        return 0 if result.ok else 1

    if result.ok:
        print("\nACCEPTED: equivalent and cheaper")
        print(result.gas_summary())
        for note in result.notes:
            print(f"  note: {note}")
        return 0

    failure = result.failure or {}
    print(f"\nREJECTED [{failure.get('type')}]: {failure.get('error')}")
    if failure.get("trace"):
        print(failure["trace"])
    if result.gas:
        print(result.gas_summary())
    return 1


# --- config / doctor ---------------------------------------------------------------


def _cmd_config(args: argparse.Namespace) -> int:
    try:
        if args.action == "show":
            resolved = settings.resolve(config_path=getattr(args, "config", None))
            print(f"# source: {resolved.source}")
            print(settings.render_toml(resolved.nested()), end="")
            return 0

        if args.action == "init":
            target = settings.find_config_path(getattr(args, "config", None))
            if target.exists():
                return _fail(f"{target} already exists")
            example = config.PROJECT_ROOT / "gas-optimizer.example.toml"
            if example.is_file():
                target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                target.write_text(
                    settings.render_toml(settings.resolve().nested()), encoding="utf-8"
                )
            print(f"Wrote {target}")
            return 0

        if args.action == "set":
            target = settings.set_value(args.key, args.value, getattr(args, "config", None))
            print(f"Set {args.key} = {args.value} in {target}")
            return 0

        if args.action == "migrate":
            if not config.VALIDATOR_CONFIG_PATH.is_file():
                return _fail(f"nothing to migrate: {config.VALIDATOR_CONFIG_PATH} not found")
            target = settings.migrate()
            print(f"Wrote {target} from {config.VALIDATOR_CONFIG_PATH.name}")
            print("You can delete the legacy file now.")
            return 0
    except settings.SettingsError as exc:
        return _fail(str(exc))

    return _fail(f"unknown config action: {args.action}")


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks = preflight.report()
    for check in checks:
        marker = "ok" if check.ok else ("--" if not check.required else "!!")
        print(f"[{marker}] {check.name:<10} {check.detail}")
        if check.hint and not check.ok:
            print(f"     {check.hint}")
    return 2 if preflight.require(checks) else 0


# --- frontends ---------------------------------------------------------------------


def _cmd_tui(args: argparse.Namespace) -> int:
    blocked = _preflight(preflight.check_workspace(), preflight.check_forge())
    if blocked:
        return blocked
    try:
        from .frontends import tui
    except ImportError:
        return _fail(
            "the TUI needs the optional textual dependency",
            'install it with: pip install -e ".[tui]"',
        )
    return tui.main(args.input)


def _cmd_gui(args: argparse.Namespace) -> int:
    blocked = _preflight(preflight.check_workspace())
    if blocked:
        return blocked
    try:
        from .frontends.web import server
    except ImportError:
        return _fail(
            "the web GUI needs the optional fastapi + uvicorn dependencies",
            'install them with: pip install -e ".[gui]"',
        )
    resolved = settings.resolve(config_path=getattr(args, "config", None))
    host = args.host or resolved.gui["host"]
    port = args.port or resolved.gui["port"]
    return server.main(host=host, port=port, open_browser=not args.no_browser)


# --- parser ------------------------------------------------------------------------


def _add_common_validator_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fuzz-runs", type=int, metavar="N", help="differential fuzzing runs per test"
    )
    parser.add_argument(
        "--forge-timeout", type=int, metavar="SECONDS", help="timeout for each forge invocation"
    )
    parser.add_argument(
        "--hevm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run the advisory hevm symbolic check",
    )
    parser.add_argument(
        "--config", type=Path, metavar="PATH", help="TOML config file (default: gas-optimizer.toml)"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gas-optimize",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: gas-optimize run test-cases/tc2.txt",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="optimize a contract through the LLM loop")
    run.add_argument("input", type=Path, help="Solidity source to optimize (.sol or .txt)")
    out = run.add_mutually_exclusive_group()
    out.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="where to write the accepted contract (default: <input dir>/<Contract>.optimized.sol)",
    )
    out.add_argument(
        "--no-output",
        action="store_true",
        help="do not write the accepted contract next to the input",
    )
    run.add_argument(
        "--max-retries", type=int, metavar="N", help="attempts at the optimize-and-verify loop"
    )
    run.add_argument("--model", metavar="ID", help="Hugging Face model id")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--json", action="store_true", help="print one final JSON object instead of progress"
    )
    mode.add_argument(
        "--stream-json", action="store_true", help="stream progress as JSONL events on stdout"
    )
    run.add_argument(
        "--no-save-run", action="store_true", help="do not record this run under runs/"
    )
    _add_common_validator_flags(run)
    run.set_defaults(func=_cmd_run)

    val = sub.add_parser("validate", help="run the acceptance gates on a contract pair")
    val.add_argument("original", type=Path)
    val.add_argument("candidate", type=Path)
    val.add_argument("--json", action="store_true", help="print the verdict as JSON")
    _add_common_validator_flags(val)
    val.set_defaults(func=_cmd_validate)

    cfg = sub.add_parser("config", help="show or edit the layered configuration")
    cfg_sub = cfg.add_subparsers(dest="action", required=True)
    cfg_show = cfg_sub.add_parser("show", help="print the effective settings")
    cfg_show.add_argument("--config", type=Path, metavar="PATH")
    cfg_init = cfg_sub.add_parser("init", help="create gas-optimizer.toml from the template")
    cfg_init.add_argument("--config", type=Path, metavar="PATH")
    cfg_set = cfg_sub.add_parser("set", help="persist one setting (e.g. hevm.enabled true)")
    cfg_set.add_argument("key")
    cfg_set.add_argument("value")
    cfg_set.add_argument("--config", type=Path, metavar="PATH")
    cfg_sub.add_parser("migrate", help="convert legacy validatorConfig.txt to TOML")
    cfg.set_defaults(func=_cmd_config)

    doctor = sub.add_parser("doctor", help="check forge, hevm, HF_TOKEN and the workspace")
    doctor.set_defaults(func=_cmd_doctor)

    tui = sub.add_parser("tui", help="interactive terminal UI")
    tui.add_argument("input", type=Path, nargs="?", default=None)
    tui.set_defaults(func=_cmd_tui)

    gui = sub.add_parser("gui", help="local web UI")
    gui.add_argument("--host", default=None)
    gui.add_argument("--port", type=int, default=None)
    gui.add_argument(
        "--no-browser", action="store_true", help="do not open the browser automatically"
    )
    gui.add_argument("--config", type=Path, metavar="PATH")
    gui.set_defaults(func=_cmd_gui)

    return parser


def _backcompat(argv: list[str]) -> list[str]:
    """`gas-optimize <file>` predates subcommands; treat it as `run <file>`."""
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        if Path(argv[0]).is_file():
            print(f"[NOTE] assuming `gas-optimize run {argv[0]}`", file=sys.stderr)
            return ["run", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    # Deprecation notices from our own config migration should reach the user.
    warnings.filterwarnings("default", category=DeprecationWarning, module="gas_optimizer.*")

    argv = _backcompat(list(sys.argv[1:] if argv is None else argv))
    parser = _parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
