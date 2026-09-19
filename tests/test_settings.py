"""
Layered configuration: CLI flag > env > TOML > legacy file > defaults.
"""

import pytest

from gas_optimizer import config, settings, validator

ENV_NAMES = (
    "GAS_OPTIMIZER_CONFIG",
    "GAS_OPTIMIZER_FUZZ_RUNS",
    "GAS_OPTIMIZER_HEVM_ENABLED",
    "GAS_OPTIMIZER_MODEL",
    "MODEL_ID",
    "MAX_RETRIES",
)


@pytest.fixture(autouse=True)
def clean_environment(tmp_path, monkeypatch):
    """No ambient env vars or repo config files may leak into these tests."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CONFIG_TOML_PATH", tmp_path / "gas-optimizer.toml")
    monkeypatch.setattr(config, "VALIDATOR_CONFIG_PATH", tmp_path / "validatorConfig.txt")
    return tmp_path


class TestPrecedence:
    def test_defaults_when_nothing_is_configured(self):
        resolved = settings.resolve()
        assert resolved.validator == settings.default_validator_settings()
        assert resolved.llm["max_retries"] == 5
        assert resolved.source == "defaults"

    def test_toml_overrides_defaults(self, tmp_path):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text("[validator]\nfuzz_runs = 777\n\n[hevm]\nenabled = true\n")

        resolved = settings.resolve(config_path=path)
        assert resolved.validator["fuzz_runs"] == 777
        assert resolved.validator["hevm_enabled"] is True
        # untouched keys keep their defaults
        assert resolved.validator["forge_timeout"] == 900
        assert resolved.source == f"toml:{path}"

    def test_env_overrides_toml(self, tmp_path, monkeypatch):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text("[validator]\nfuzz_runs = 777\n")
        monkeypatch.setenv("GAS_OPTIMIZER_FUZZ_RUNS", "1234")

        resolved = settings.resolve(config_path=path)
        assert resolved.validator["fuzz_runs"] == 1234

    def test_cli_override_beats_everything(self, tmp_path, monkeypatch):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text("[validator]\nfuzz_runs = 777\n")
        monkeypatch.setenv("GAS_OPTIMIZER_FUZZ_RUNS", "1234")

        resolved = settings.resolve({"fuzz_runs": 42}, config_path=path)
        assert resolved.validator["fuzz_runs"] == 42

    def test_none_overrides_are_ignored(self):
        resolved = settings.resolve({"fuzz_runs": None})
        assert resolved.validator["fuzz_runs"] == 2000

    def test_legacy_env_aliases_still_work(self, monkeypatch):
        monkeypatch.setenv("MODEL_ID", "some/other-model")
        assert settings.resolve().llm["model"] == "some/other-model"

    def test_env_alias_loses_to_the_dedicated_name(self, monkeypatch):
        monkeypatch.setenv("MODEL_ID", "aliased/model")
        monkeypatch.setenv("GAS_OPTIMIZER_MODEL", "dedicated/model")
        assert settings.resolve().llm["model"] == "dedicated/model"


class TestErrors:
    def test_unknown_toml_key_is_rejected(self, tmp_path):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text("[validator]\nfuz_runs = 777\n")
        with pytest.raises(settings.SettingsError, match="unknown setting"):
            settings.resolve(config_path=path)

    def test_unknown_section_is_rejected(self, tmp_path):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text("[valdiator]\nfuzz_runs = 777\n")
        with pytest.raises(settings.SettingsError, match="unknown section"):
            settings.resolve(config_path=path)

    def test_wrong_type_is_rejected(self, tmp_path):
        path = tmp_path / "gas-optimizer.toml"
        path.write_text('[validator]\nfuzz_runs = "many"\n')
        with pytest.raises(settings.SettingsError, match="integer"):
            settings.resolve(config_path=path)

    def test_explicit_missing_config_is_an_error(self, tmp_path):
        with pytest.raises(settings.SettingsError, match="not found"):
            settings.resolve(config_path=tmp_path / "nope.toml")

    def test_junk_env_value_is_rejected(self, monkeypatch):
        monkeypatch.setenv("GAS_OPTIMIZER_HEVM_ENABLED", "maybe")
        with pytest.raises(settings.SettingsError, match="true/false"):
            settings.resolve()


class TestLegacyFallback:
    def test_legacy_file_is_read_with_a_deprecation_warning(self, tmp_path):
        legacy = tmp_path / "validatorConfig.txt"
        legacy.write_text("fuzz_runs=4242\nhevm_enabled=yes\n")

        with pytest.warns(DeprecationWarning, match="config migrate"):
            resolved = settings.resolve()

        assert resolved.validator["fuzz_runs"] == 4242
        assert resolved.validator["hevm_enabled"] is True
        assert resolved.source == f"legacy:{legacy}"

    def test_toml_wins_over_the_legacy_file(self, tmp_path):
        (tmp_path / "validatorConfig.txt").write_text("fuzz_runs=4242\n")
        (tmp_path / "gas-optimizer.toml").write_text("[validator]\nfuzz_runs = 99\n")

        resolved = settings.resolve()
        assert resolved.validator["fuzz_runs"] == 99

    def test_migrate_round_trips(self, tmp_path):
        legacy = tmp_path / "validatorConfig.txt"
        legacy.write_text("fuzz_runs=4242\nhevm_enabled=yes\nhevm_solver=bitwuzla\n")
        target = tmp_path / "gas-optimizer.toml"

        written = settings.migrate(legacy, target)
        assert written == target

        resolved = settings.resolve(config_path=target)
        assert resolved.validator["fuzz_runs"] == 4242
        assert resolved.validator["hevm_enabled"] is True
        assert resolved.validator["hevm_solver"] == "bitwuzla"

    def test_validator_load_config_still_parses_explicit_legacy_paths(self, tmp_path):
        legacy = tmp_path / "someConfig.txt"
        legacy.write_text("fuzz_runs=7\n")
        assert validator.load_config(legacy)["fuzz_runs"] == 7


class TestWriting:
    def test_set_value_creates_and_updates(self, tmp_path):
        target = tmp_path / "gas-optimizer.toml"

        settings.set_value("hevm.enabled", "true", target)
        assert settings.resolve(config_path=target).validator["hevm_enabled"] is True

        settings.set_value("validator.fuzz_runs", "55", target)
        resolved = settings.resolve(config_path=target)
        assert resolved.validator["fuzz_runs"] == 55
        # the earlier write survives
        assert resolved.validator["hevm_enabled"] is True

    def test_set_value_rejects_unknown_keys(self, tmp_path):
        with pytest.raises(settings.SettingsError, match="unknown setting"):
            settings.set_value("validator.nope", "1", tmp_path / "c.toml")

    def test_example_template_parses_and_matches_defaults(self):
        example = config.PACKAGE_DIR.parent / "gas-optimizer.example.toml"
        resolved = settings.resolve(config_path=example)
        assert resolved.validator == settings.default_validator_settings()
        assert resolved.llm["model"] == "Qwen/Qwen2.5-Coder-32B-Instruct"
