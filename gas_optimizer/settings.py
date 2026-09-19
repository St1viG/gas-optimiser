"""
Layered configuration: CLI flag > environment > TOML file > defaults.

The TOML file (`gas-optimizer.toml`, gitignored; `gas-optimizer.example.toml`
is the tracked template) replaces the legacy `validatorConfig.txt`. The legacy
file is still read — with a deprecation warning — when no TOML exists, and
`migrate()` converts it. The validator keeps consuming a flat settings dict
(`fuzz_runs`, `hevm_enabled`, ...), so `Settings.validator` preserves that
shape; new code should go through `resolve()`.
"""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: the tomli backport, declared as a conditional dependency
    import tomli as tomllib

from . import config


class SettingsError(ValueError):
    """A config file or override that cannot be used as written."""


@dataclass(frozen=True)
class _Key:
    section: str
    name: str  # key inside the TOML section
    flat: str  # legacy/flat name, also the CLI-override and env suffix
    type: type
    default: object
    env_aliases: tuple[str, ...] = ()

    @property
    def env(self) -> str:
        return f"GAS_OPTIMIZER_{self.flat.upper()}"

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.name}"


SCHEMA: tuple[_Key, ...] = (
    _Key("validator", "fuzz_runs", "fuzz_runs", int, 2000),
    _Key("validator", "max_array_length", "max_array_length", int, 5),
    _Key("validator", "require_gas_improvement", "require_gas_improvement", bool, True),
    _Key("validator", "forge_timeout", "forge_timeout", int, 900),
    _Key("hevm", "enabled", "hevm_enabled", bool, False),
    _Key("hevm", "timeout", "hevm_timeout", int, 300),
    _Key("hevm", "smt_timeout", "hevm_smt_timeout", int, 30),
    _Key("hevm", "solver", "hevm_solver", str, "z3"),
    _Key("hevm", "max_iterations", "hevm_max_iterations", int, 5),
    _Key("llm", "model", "model", str, "Qwen/Qwen2.5-Coder-32B-Instruct", ("MODEL_ID",)),
    _Key("llm", "max_retries", "max_retries", int, 5, ("MAX_RETRIES",)),
    _Key("llm", "api_retries", "api_retries", int, 3, ("API_RETRIES",)),
    _Key("gui", "host", "host", str, "127.0.0.1"),
    _Key("gui", "port", "port", int, 8765),
)

_BY_FLAT = {key.flat: key for key in SCHEMA}
_BY_DOTTED = {key.dotted: key for key in SCHEMA}

# The flat keys that make up the validator's settings dict.
_VALIDATOR_FLAT = tuple(k.flat for k in SCHEMA if k.section in ("validator", "hevm"))

# Legacy validatorConfig.txt parsing (the old validator.load_config behavior).
LEGACY_BOOL_KEYS = {"hevm_enabled", "require_gas_improvement"}
LEGACY_STR_KEYS = {"hevm_solver"}
_TRUTHY = ("true", "1", "yes", "on")


def default_validator_settings() -> dict:
    """The flat dict the validator has always consumed."""
    return {k.flat: k.default for k in SCHEMA if k.flat in _VALIDATOR_FLAT}


@dataclass(frozen=True)
class Settings:
    validator: dict  # flat legacy keys: fuzz_runs, hevm_enabled, ...
    llm: dict  # model, max_retries, api_retries
    gui: dict  # host, port
    source: str  # "defaults" | "toml:<path>" | "legacy:<path>"

    def nested(self) -> dict:
        """Effective values in TOML shape, for `config show` and `migrate`."""
        out: dict[str, dict] = {}
        flat = {**self.validator, **self.llm, **self.gui}
        for key in SCHEMA:
            out.setdefault(key.section, {})[key.name] = flat[key.flat]
        return out


def parse_legacy(config_path: Path | str) -> dict:
    """Parse validatorConfig.txt exactly as the old loader did."""
    settings = default_validator_settings()

    path = Path(config_path)
    if not path.exists():
        return settings

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = (part.strip() for part in line.split("=", 1))
        if key not in settings:
            continue
        if key in LEGACY_BOOL_KEYS:
            settings[key] = value.lower() in _TRUTHY
        elif key in LEGACY_STR_KEYS:
            settings[key] = value
        else:
            try:
                settings[key] = int(value)
            except ValueError:
                settings[key] = value

    return settings


def find_config_path(explicit: Path | str | None = None) -> Path:
    """Where the TOML config should live: --config > $GAS_OPTIMIZER_CONFIG > project root."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("GAS_OPTIMIZER_CONFIG", "").strip()
    if env:
        return Path(env)
    return config.CONFIG_TOML_PATH


def _check_type(key: _Key, value: object, origin: str) -> object:
    # bool first: isinstance(True, int) is True.
    if key.type is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{origin}: {key.dotted} must be a boolean, got {value!r}")
    elif key.type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{origin}: {key.dotted} must be an integer, got {value!r}")
    elif not isinstance(value, str):
        raise SettingsError(f"{origin}: {key.dotted} must be a string, got {value!r}")
    return value


def parse_value(key_dotted_or_flat: str, raw: str) -> tuple[_Key, object]:
    """Turn a "section.key" (or flat) name and a string into a typed value."""
    key = _BY_DOTTED.get(key_dotted_or_flat) or _BY_FLAT.get(key_dotted_or_flat)
    if key is None:
        known = ", ".join(sorted(_BY_DOTTED))
        raise SettingsError(f"unknown setting {key_dotted_or_flat!r}; known: {known}")
    if key.type is bool:
        lowered = raw.strip().lower()
        if lowered in _TRUTHY:
            return key, True
        if lowered in ("false", "0", "no", "off"):
            return key, False
        raise SettingsError(f"{key.dotted} expects true/false, got {raw!r}")
    if key.type is int:
        try:
            return key, int(raw)
        except ValueError:
            raise SettingsError(f"{key.dotted} expects an integer, got {raw!r}") from None
    return key, raw


def _load_toml(path: Path) -> dict:
    """Flat {flat_key: value} from a TOML file, rejecting unknown keys."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: invalid TOML: {exc}") from exc

    flat: dict[str, object] = {}
    known_sections = {k.section for k in SCHEMA}
    for section, body in data.items():
        if section not in known_sections:
            raise SettingsError(f"{path}: unknown section [{section}]")
        if not isinstance(body, dict):
            raise SettingsError(f"{path}: [{section}] must be a table")
        for name, value in body.items():
            key = _BY_DOTTED.get(f"{section}.{name}")
            if key is None:
                raise SettingsError(f"{path}: unknown setting {section}.{name}")
            flat[key.flat] = _check_type(key, value, str(path))
    return flat


def _env_layer() -> dict:
    flat: dict[str, object] = {}
    for key in SCHEMA:
        for env_name in (key.env, *key.env_aliases):
            raw = os.environ.get(env_name, "").strip()
            if raw:
                _, value = parse_value(key.dotted, raw)
                flat[key.flat] = value
                break
    return flat


def resolve(
    cli_overrides: dict | None = None,
    config_path: Path | str | None = None,
) -> Settings:
    """Resolve effective settings: CLI > env > TOML (or legacy file) > defaults."""
    flat = {key.flat: key.default for key in SCHEMA}
    source = "defaults"

    toml_path = find_config_path(config_path)
    if toml_path.exists():
        flat.update(_load_toml(toml_path))
        source = f"toml:{toml_path}"
    elif config_path is not None:
        raise SettingsError(f"config file not found: {toml_path}")
    elif config.VALIDATOR_CONFIG_PATH.exists():
        flat.update(parse_legacy(config.VALIDATOR_CONFIG_PATH))
        source = f"legacy:{config.VALIDATOR_CONFIG_PATH}"
        warnings.warn(
            f"{config.VALIDATOR_CONFIG_PATH.name} is deprecated; run "
            "`gas-optimize config migrate` to convert it to gas-optimizer.toml",
            DeprecationWarning,
            stacklevel=2,
        )

    flat.update(_env_layer())

    for name, value in (cli_overrides or {}).items():
        key = _BY_FLAT.get(name)
        if key is None:
            raise SettingsError(f"unknown override {name!r}")
        if value is not None:
            flat[name] = _check_type(key, value, "override")

    return Settings(
        validator={name: flat[name] for name in _VALIDATOR_FLAT},
        llm={
            "model": flat["model"],
            "max_retries": flat["max_retries"],
            "api_retries": flat["api_retries"],
        },
        gui={"host": flat["host"], "port": flat["port"]},
        source=source,
    )


# --- writing -----------------------------------------------------------------


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_toml(nested: dict) -> str:
    """Render {section: {key: value}} in schema order."""
    lines: list[str] = []
    for section in dict.fromkeys(k.section for k in SCHEMA):
        body = nested.get(section)
        if not body:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key in SCHEMA:
            if key.section == section and key.name in body:
                lines.append(f"{key.name} = {_render_scalar(body[key.name])}")
    return "\n".join(lines) + "\n"


def migrate(
    legacy_path: Path | str | None = None,
    toml_path: Path | str | None = None,
) -> Path:
    """Write the TOML equivalent of the legacy config file. Returns its path."""
    legacy = Path(legacy_path) if legacy_path is not None else config.VALIDATOR_CONFIG_PATH
    target = Path(toml_path) if toml_path is not None else find_config_path()

    flat = {key.flat: key.default for key in SCHEMA}
    flat.update(parse_legacy(legacy))

    nested: dict[str, dict] = {}
    for key in SCHEMA:
        nested.setdefault(key.section, {})[key.name] = flat[key.flat]

    target.write_text(render_toml(nested), encoding="utf-8")
    return target


def set_value(name: str, raw: str, config_path: Path | str | None = None) -> Path:
    """Persist one setting ("hevm.enabled true") into the TOML file."""
    key, value = parse_value(name, raw)
    target = find_config_path(config_path)

    nested: dict[str, dict] = {}
    if target.exists():
        for flat_name, existing in _load_toml(target).items():
            existing_key = _BY_FLAT[flat_name]
            nested.setdefault(existing_key.section, {})[existing_key.name] = existing

    nested.setdefault(key.section, {})[key.name] = value
    target.write_text(render_toml(nested), encoding="utf-8")
    return target
