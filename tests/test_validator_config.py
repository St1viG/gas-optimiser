from gas_optimizer import validator

SAMPLE = """# Validator Configuration

# how many
fuzz_runs=4242
hevm_enabled=yes
require_gas_improvement=false
unknown_key=ignored
malformed line without equals
"""


def test_defaults_when_file_is_absent(tmp_path):
    settings = validator.load_config(tmp_path / "nope.txt")
    assert settings == validator.DEFAULT_SETTINGS


def test_parses_ints_bools_and_ignores_noise(tmp_path):
    path = tmp_path / "validatorConfig.txt"
    path.write_text(SAMPLE)

    settings = validator.load_config(path)
    assert settings["fuzz_runs"] == 4242
    assert settings["hevm_enabled"] is True
    assert settings["require_gas_improvement"] is False
    assert "unknown_key" not in settings
    # untouched keys keep their defaults
    assert settings["max_array_length"] == validator.DEFAULT_SETTINGS["max_array_length"]


def test_save_preserves_comments_and_round_trips(tmp_path):
    path = tmp_path / "validatorConfig.txt"
    path.write_text(SAMPLE)

    settings = validator.load_config(path)
    settings["hevm_enabled"] = False
    settings["fuzz_runs"] = 10
    validator.save_config(settings, path)

    written = path.read_text()
    assert "# how many" in written
    assert "hevm_enabled=false" in written
    assert "fuzz_runs=10" in written

    assert validator.load_config(path)["hevm_enabled"] is False


def test_save_creates_a_file_when_missing(tmp_path):
    path = tmp_path / "new.txt"
    validator.save_config({"fuzz_runs": 7, "hevm_enabled": True}, path)
    assert validator.load_config(path)["fuzz_runs"] == 7


class TestGasEntry:
    def test_improvement(self):
        e = validator.GasEntry("f()", 100, 90, True, True)
        assert e.improved and not e.regressed and e.delta == -10

    def test_regression(self):
        e = validator.GasEntry("f()", 100, 110, True, True)
        assert e.regressed and not e.improved

    def test_reverted_calls_are_not_comparable(self):
        e = validator.GasEntry("f()", 100, 10, False, True)
        assert not e.comparable and not e.improved
        assert "not measured" in e.describe()


def test_run_command_reports_a_missing_binary():
    code, _, err = validator.run_command(["definitely-not-a-real-binary-xyz"])
    assert code == 127 and "not found" in err


def test_run_command_times_out_without_hanging():
    code, _, err = validator.run_command(["sleep", "5"], timeout=0.2)
    assert code == 124 and "timed out" in err
