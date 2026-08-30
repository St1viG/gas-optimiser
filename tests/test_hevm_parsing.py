"""
Pinned against real `hevm 0.58.0` output.

hevm is optional and usually absent, which is exactly why its result handling
needs tests: a parser nobody exercises drifts into reporting "inconclusive" for
a proven divergence, and a candidate that hevm rejected gets accepted.
"""

from pathlib import Path

import pytest

from gas_optimizer import validator

OUTPUT = Path(__file__).parent / "fixtures" / "hevm"


def _read(name: str) -> str:
    return (OUTPUT / f"{name}.txt").read_text()


class TestVerdict:
    def test_equivalent(self):
        verdict, counterexamples = validator.parse_hevm_output(_read("equivalent"))
        assert verdict is True
        assert counterexamples == []

    def test_not_equivalent(self):
        verdict, counterexamples = validator.parse_hevm_output(_read("not-equivalent"))
        assert verdict is False
        assert counterexamples

    def test_failure_wins_over_the_substring_it_contains(self):
        """'do not behave equivalently' contains 'behave equivalently'."""
        verdict, _ = validator.parse_hevm_output("[FAIL] Contracts do not behave equivalently")
        assert verdict is False

    @pytest.mark.parametrize(
        "output",
        ["", "Invalid option `--smttimeout'", "hevm: solver timeout", "some unrelated noise"],
    )
    def test_no_verdict_is_inconclusive_not_a_rejection(self, output):
        verdict, counterexamples = validator.parse_hevm_output(output)
        assert verdict is None
        assert counterexamples == []

    def test_a_rejected_flag_never_looks_like_a_counterexample(self):
        """The old --smttimeout spelling was rejected outright by hevm 0.58."""
        verdict, _ = validator.parse_hevm_output(_read("invalid-option"))
        assert verdict is None

    def test_ansi_colour_codes_are_stripped(self):
        verdict, _ = validator.parse_hevm_output(
            "\x1b[32m[PASS]\x1b[0m Contracts behave equivalently"
        )
        assert verdict is True


class TestCounterexampleSummary:
    def test_leads_with_the_difference_and_calldata(self):
        _, counterexamples = validator.parse_hevm_output(_read("not-equivalent"))
        summary = validator.hevm_summary(counterexamples[0])
        assert summary.startswith("Difference:")
        assert "calldata=0x" in summary

    def test_falls_back_when_there_is_no_difference_line(self):
        assert "divergent behaviour" in validator.hevm_summary("something opaque")


def test_hevm_flags_match_the_cli(tmp_path):
    """`--smt-timeout` in seconds, not `--smttimeout` in milliseconds."""
    settings = validator.DEFAULT_SETTINGS
    assert settings["hevm_smt_timeout"] == 30
    assert "hevm_solver_timeout" not in settings
