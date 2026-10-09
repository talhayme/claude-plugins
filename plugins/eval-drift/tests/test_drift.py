import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import drift  # noqa: E402

FX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FX / name).read_text(encoding="utf-8"))


def baseline_from(name: str) -> drift.Baseline:
    return drift.parse_result(load(name))


class TestParseResult:
    def test_reads_every_case(self):
        b = baseline_from("good.json")
        assert set(b.cases) == {"reports-total", "finds-duplicates", "handles-empty"}

    def test_pass_is_derived_from_threshold(self):
        b = baseline_from("flaky.json")
        assert b.threshold == 1.0
        assert b.cases["finds-duplicates"].passed is False   # 0.67 < 1.0
        assert b.cases["reports-total"].passed is True

    def test_threshold_from_suite_is_respected(self):
        raw = load("flaky.json")
        raw["suite"]["threshold"] = 0.6
        b = drift.parse_result(raw)
        assert b.cases["finds-duplicates"].passed is True    # 0.67 >= 0.6

    def test_delta_carried_when_present(self):
        b = baseline_from("good.json")
        assert b.cases["reports-total"].delta == pytest.approx(0.67)
        assert b.cases["handles-empty"].delta is None

    def test_failed_graders_are_named(self):
        b = baseline_from("regressed.json")
        assert "skill-fired" in b.cases["finds-duplicates"].failed_graders

    def test_partial_run_is_refused(self):
        # Half a baseline is worse than none: it would record a cost-ceiling
        # abort as if it were a verdict about the plugin.
        with pytest.raises(ValueError, match="partial"):
            baseline_from("partial.json")

    def test_unknown_schema_is_refused(self):
        raw = load("good.json")
        raw["schemaVersion"] = 99
        with pytest.raises(ValueError, match="schema"):
            drift.parse_result(raw)

    def test_empty_result_is_refused(self):
        raw = load("good.json")
        raw["cases"] = []
        with pytest.raises(ValueError, match="no cases"):
            drift.parse_result(raw)


class TestCompare:
    def test_clean_rerun_has_no_drift(self):
        good = baseline_from("good.json")
        d = drift.compare(good, good, drop_tolerance=0.15)
        assert not d.regressions and not d.improvements and not d.flaky
        assert d.blocking is False

    def test_pass_to_fail_is_a_regression(self):
        d = drift.compare(baseline_from("regressed.json"), baseline_from("good.json"),
                          drop_tolerance=0.15)
        names = [now.name for _, now in d.regressions]
        assert names == ["finds-duplicates"]
        assert d.blocking is True

    def test_regression_blocks_even_when_average_is_fine(self):
        # Three of four cases pass; the suite average is 0.75. A threshold
        # check on the average could still pass; the per-case check must not.
        d = drift.compare(baseline_from("regressed.json"), baseline_from("good.json"),
                          drop_tolerance=0.15)
        assert d.blocking is True

    def test_new_case_is_reported_not_blamed(self):
        d = drift.compare(baseline_from("regressed.json"), baseline_from("good.json"),
                          drop_tolerance=0.15)
        assert [c.name for c in d.new_cases] == ["brand-new-case"]
        assert "brand-new-case" not in [n.name for _, n in d.regressions]

    def test_removed_case_is_reported(self):
        d = drift.compare(baseline_from("good.json"), baseline_from("improved.json"),
                          drop_tolerance=0.15)
        assert d.removed_cases == ["was-failing"]

    def test_fail_to_pass_is_an_improvement(self):
        before = baseline_from("flaky.json")      # finds-duplicates failing at 0.67
        after = baseline_from("good.json")         # now 1.0
        d = drift.compare(after, before, drop_tolerance=0.15)
        assert [n.name for _, n in d.improvements] == ["finds-duplicates"]
        assert d.blocking is False

    def test_flaky_case_is_flagged_separately(self):
        d = drift.compare(baseline_from("flaky.json"), baseline_from("good.json"),
                          drop_tolerance=0.15)
        assert [c.name for c in d.flaky] == ["finds-duplicates"]

    def test_score_drop_on_passing_case_is_a_warning(self):
        raw = load("good.json")
        raw["suite"]["threshold"] = 0.5
        before = drift.parse_result(raw)
        raw2 = load("good.json")
        raw2["suite"]["threshold"] = 0.5
        raw2["cases"][0]["aggregates"]["score"] = 0.6   # still passes at 0.5
        after = drift.parse_result(raw2)
        d = drift.compare(after, before, drop_tolerance=0.15)
        assert [n.name for _, n in d.score_drops] == ["reports-total"]
        assert d.blocking is False

    def test_small_score_drop_is_within_tolerance(self):
        raw = load("good.json")
        raw["suite"]["threshold"] = 0.5
        before = drift.parse_result(raw)
        raw2 = load("good.json")
        raw2["suite"]["threshold"] = 0.5
        raw2["cases"][0]["aggregates"]["score"] = 0.9
        after = drift.parse_result(raw2)
        d = drift.compare(after, before, drop_tolerance=0.15)
        assert d.score_drops == []


class TestBaselineRoundTrip:
    def test_survives_json(self):
        b = baseline_from("good.json")
        back = drift.Baseline.from_json(json.loads(json.dumps(b.to_json())))
        assert set(back.cases) == set(b.cases)
        assert back.cases["reports-total"].delta == pytest.approx(0.67)
        assert back.threshold == b.threshold

    def test_wrong_schema_version_is_refused(self):
        payload = baseline_from("good.json").to_json()
        payload["schema_version"] = 0
        with pytest.raises(ValueError, match="schema"):
            drift.Baseline.from_json(payload)


class TestCli:
    def test_no_baseline_exits_zero_and_says_so(self, tmp_path, capsys):
        code = drift.main([str(FX / "good.json"), "--baseline", str(tmp_path / "b.json")])
        assert code == 0
        assert "No baseline found" in capsys.readouterr().out

    def test_update_baseline_writes_file(self, tmp_path):
        target = tmp_path / "nested" / "baseline.json"
        code = drift.main([str(FX / "good.json"), "--baseline", str(target), "--update-baseline"])
        assert code == 0
        assert target.exists()
        assert json.loads(target.read_text())["schema_version"] == drift.SCHEMA_VERSION

    def test_regression_exits_one(self, tmp_path, capsys):
        b = tmp_path / "baseline.json"
        drift.main([str(FX / "good.json"), "--baseline", str(b), "--update-baseline"])
        capsys.readouterr()
        code = drift.main([str(FX / "regressed.json"), "--baseline", str(b)])
        out = capsys.readouterr().out
        assert code == 1
        assert "REGRESSIONS" in out
        assert "finds-duplicates" in out
        assert "skill-fired" in out            # the failing grader is named

    def test_clean_run_exits_zero(self, tmp_path):
        b = tmp_path / "baseline.json"
        drift.main([str(FX / "good.json"), "--baseline", str(b), "--update-baseline"])
        assert drift.main([str(FX / "good.json"), "--baseline", str(b)]) == 0

    def test_json_verdict(self, tmp_path, capsys):
        b = tmp_path / "baseline.json"
        drift.main([str(FX / "good.json"), "--baseline", str(b), "--update-baseline"])
        capsys.readouterr()
        drift.main([str(FX / "regressed.json"), "--baseline", str(b), "--json"])
        verdict = json.loads(capsys.readouterr().out)
        assert verdict["blocking"] is True
        assert verdict["regressions"] == ["finds-duplicates"]
        assert verdict["new_cases"] == ["brand-new-case"]

    def test_partial_result_exits_two(self, tmp_path, capsys):
        code = drift.main([str(FX / "partial.json"), "--baseline", str(tmp_path / "b.json")])
        assert code == 2
        assert "partial" in capsys.readouterr().err

    def test_missing_result_file_is_a_clean_error(self, tmp_path):
        with pytest.raises(SystemExit, match="not found"):
            drift.main([str(tmp_path / "nope.json")])
