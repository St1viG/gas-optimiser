"""
CLI contract: subcommand routing, exit codes (0 accepted, 1 rejected, 2
environment/usage), JSON output shape, and the pre-subcommand back-compat
form. The pipeline itself is monkeypatched — no forge, no network.
"""

import json

import pytest

from gas_optimizer import cli, config, optimizer, preflight, validator

SOURCE = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract Foo { uint256 public x; function set(uint256 v) external { x = v; } }
"""

OK_CHECK = preflight.Check(name="x", ok=True, detail="fine")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No repo config, no ambient env, runs/ under tmp."""
    for name in ("GAS_OPTIMIZER_CONFIG", "MODEL_ID", "MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CONFIG_TOML_PATH", tmp_path / "gas-optimizer.toml")
    monkeypatch.setattr(config, "VALIDATOR_CONFIG_PATH", tmp_path / "validatorConfig.txt")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    return tmp_path


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(preflight, "check_workspace", lambda: OK_CHECK)
    monkeypatch.setattr(preflight, "check_forge", lambda: OK_CHECK)
    monkeypatch.setattr(preflight, "check_hf_token", lambda: OK_CHECK)


@pytest.fixture
def contract_file(tmp_path):
    path = tmp_path / "Foo.sol"
    path.write_text(SOURCE)
    return path


def _accepted(*args, **kwargs):
    return optimizer.OptimizationResult(
        True,
        "Equivalent and 85 gas cheaper.",
        optimized_code=SOURCE.replace("x = v;", "x =  v;"),
        attempts=1,
        gas=[validator.GasEntry("set(uint256)", 100, 15, True, True)],
        notes=["hevm proved the two bytecodes equivalent"],
    )


def _failed(*args, **kwargs):
    return optimizer.OptimizationResult(False, "No candidate passed", attempts=5)


class TestRouting:
    def test_bare_file_argument_is_treated_as_run(self, contract_file):
        assert cli._backcompat([str(contract_file)]) == ["run", str(contract_file)]

    def test_subcommands_pass_through_untouched(self):
        assert cli._backcompat(["doctor"]) == ["doctor"]
        assert cli._backcompat(["--version"]) == ["--version"]

    def test_no_arguments_shows_help_and_exits_2(self, capsys):
        assert cli.main([]) == 2
        assert "usage" in capsys.readouterr().out.lower()

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert "gas-optimize" in capsys.readouterr().out


class TestRunCommand:
    def test_missing_input_is_exit_2(self, isolated, healthy, tmp_path):
        assert cli.main(["run", str(tmp_path / "nope.sol")]) == 2

    def test_missing_forge_is_exit_2_before_any_attempt(
        self, isolated, contract_file, monkeypatch, capsys
    ):
        monkeypatch.setattr(preflight, "check_workspace", lambda: OK_CHECK)
        monkeypatch.setattr(preflight, "check_hf_token", lambda: OK_CHECK)
        monkeypatch.setattr(
            preflight,
            "check_forge",
            lambda: preflight.Check(
                name="forge",
                ok=False,
                detail="not found on PATH",
                hint="install Foundry: https://getfoundry.sh",
            ),
        )

        called = []
        monkeypatch.setattr(optimizer, "run_optimization_loop", lambda *a, **k: called.append(1))

        assert cli.main(["run", str(contract_file)]) == 2
        assert not called
        assert "getfoundry.sh" in capsys.readouterr().err

    def test_json_output_shape_on_success(
        self, isolated, healthy, contract_file, monkeypatch, capsys
    ):
        monkeypatch.setattr(optimizer, "run_optimization_loop", _accepted)

        code = cli.main(
            [
                "run",
                str(contract_file),
                "--json",
                "--no-save-run",
                "--no-output",
            ]
        )
        assert code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["attempts"] == 1
        assert payload["total_delta"] == -85
        assert payload["gas"][0]["signature"] == "set(uint256)"
        assert payload["notes"] == ["hevm proved the two bytecodes equivalent"]
        assert payload["optimized_code"].startswith("// SPDX")

    def test_failed_run_is_exit_1(self, isolated, healthy, contract_file, monkeypatch):
        monkeypatch.setattr(optimizer, "run_optimization_loop", _failed)
        code = cli.main(["run", str(contract_file), "--no-save-run", "-q"])
        assert code == 1

    def test_accepted_contract_lands_next_to_the_input(
        self, isolated, healthy, contract_file, monkeypatch
    ):
        monkeypatch.setattr(optimizer, "run_optimization_loop", _accepted)
        assert cli.main(["run", str(contract_file), "--no-save-run", "-q"]) == 0
        assert (contract_file.parent / "Foo.optimized.sol").is_file()

    def test_runs_are_recorded_by_default(self, isolated, healthy, contract_file, monkeypatch):
        monkeypatch.setattr(optimizer, "run_optimization_loop", _accepted)
        assert cli.main(["run", str(contract_file), "--no-output", "-q"]) == 0

        (run_dir,) = (isolated / "runs").iterdir()
        record = json.loads((run_dir / "run.json").read_text())
        assert record["success"] is True
        assert record["contract"] == "Foo"

    def test_settings_overrides_reach_the_loop(self, isolated, healthy, contract_file, monkeypatch):
        received = {}

        def spy(source, verbose=True, **kwargs):
            received.update(kwargs)
            return _failed()

        monkeypatch.setattr(optimizer, "run_optimization_loop", spy)
        cli.main(
            [
                "run",
                str(contract_file),
                "--fuzz-runs",
                "55",
                "--max-retries",
                "2",
                "--hevm",
                "--no-save-run",
                "-q",
            ]
        )

        assert received["settings"]["fuzz_runs"] == 55
        assert received["settings"]["hevm_enabled"] is True
        assert received["max_retries"] == 2


class TestValidateCommand:
    def test_json_verdict(self, isolated, healthy, contract_file, monkeypatch, capsys):
        failure = {
            "type": "gas_error",
            "error": "not cheaper",
            "compile": "true",
            "test": "GasBench",
            "trace": "",
        }
        monkeypatch.setattr(
            validator,
            "validate",
            lambda *a, **k: validator.ValidationResult(False, failure),
        )

        code = cli.main(
            [
                "validate",
                str(contract_file),
                str(contract_file),
                "--json",
            ]
        )
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["failure"]["type"] == "gas_error"

    def test_missing_candidate_is_exit_2(self, isolated, healthy, contract_file, tmp_path):
        assert cli.main(["validate", str(contract_file), str(tmp_path / "no.sol")]) == 2


class TestConfigCommand:
    def test_show_prints_effective_toml(self, isolated, capsys):
        assert cli.main(["config", "show"]) == 0
        out = capsys.readouterr().out
        assert "# source: defaults" in out
        assert "[validator]" in out
        assert "fuzz_runs = 2000" in out

    def test_set_then_show_round_trips(self, isolated, tmp_path, capsys):
        target = tmp_path / "custom.toml"
        assert cli.main(["config", "set", "hevm.enabled", "true", "--config", str(target)]) == 0
        assert cli.main(["config", "show", "--config", str(target)]) == 0
        assert "enabled = true" in capsys.readouterr().out

    def test_set_unknown_key_is_exit_2(self, isolated, tmp_path):
        assert (
            cli.main(["config", "set", "validator.nope", "1", "--config", str(tmp_path / "c.toml")])
            == 2
        )

    def test_migrate_converts_the_legacy_file(self, isolated, capsys):
        (isolated / "validatorConfig.txt").write_text("fuzz_runs=4242\n")
        assert cli.main(["config", "migrate"]) == 0
        assert (isolated / "gas-optimizer.toml").is_file()
        assert cli.main(["config", "show"]) == 0
        assert "fuzz_runs = 4242" in capsys.readouterr().out


class TestDoctorCommand:
    def test_healthy_environment_is_exit_0(self, monkeypatch, capsys):
        monkeypatch.setattr(preflight, "report", lambda: [OK_CHECK])
        assert cli.main(["doctor"]) == 0

    def test_missing_requirement_is_exit_2(self, monkeypatch, capsys):
        broken = preflight.Check(name="forge", ok=False, detail="missing", hint="install")
        monkeypatch.setattr(preflight, "report", lambda: [broken])
        assert cli.main(["doctor"]) == 2
        assert "install" in capsys.readouterr().out


class TestValidatorShim:
    def test_pair_form_forwards_to_validate(self, monkeypatch, capsys):
        forwarded = {}
        monkeypatch.setattr(cli, "main", lambda argv: forwarded.setdefault("argv", argv) and 0)

        validator.main(["a.sol", "b.sol"])
        assert forwarded["argv"] == ["validate", "a.sol", "b.sol"]
        assert "DEPRECATED" in capsys.readouterr().err
