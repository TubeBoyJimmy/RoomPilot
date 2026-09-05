"""Independent counterexamples for v5 guards and historical diagnostic provenance."""
from copy import deepcopy

import numpy as np
import pytest

from roompilot import analysis as a
from roompilot.protection import ProtectionModel, CurveFootprint, added_deficit
from test_analysis import measurement, peak


def settings(**overrides):
    return a._settings(dict(guard_policy="v5", target_level=75., bands=4, max_q=9.,
                           low_cut_limit_db=6., **overrides))


def test_added_deficit_counts_crossing_originally_above_target():
    grid = np.geomspace(30, 200, 100)
    model = ProtectionModel(grid, np.full((1, len(grid)), 77.), 75., settings())
    d = model.diagnostics(np.full(len(grid), -4.))
    assert d["records"][0]["below_target_cut_rms_db"] == 0
    assert d["records"][0]["added_deficit_rms_db"] == 2
    assert d["records"][0]["newly_below_bin_count"] == len(grid)
    assert not d["feasible"]


def test_outer_positive_part_does_not_penalize_boost_improvement():
    actual = added_deficit(np.array([70., 74., 77., 77.]), np.array([2., 3., -1., -4.]), 75.)
    assert np.array_equal(actual, [0., 0., 0., 2.])


def test_local_guard_detects_band_hidden_by_wide_average():
    grid = a._log_grid(20., 2000.)
    response = np.where((grid >= 80.) & (grid <= 101.), -6., 0.)
    model = ProtectionModel(grid, np.full((1, len(grid)), 90.), 75., settings())
    d = model.diagnostics(response)
    assert np.mean(-response) < 1.
    assert d["local_cut"]["max_mean_db"] > 5.8
    assert any(c["name"] == "local_mean_cut" and not c["passed"] for c in d["constraints"])


def test_individual_repeats_cannot_hide_behind_their_average():
    grid = a._log_grid(30., 200.)
    ys = np.array([np.full(len(grid), 80.), np.full(len(grid), 74.)])
    response = np.full(len(grid), -2.)
    raw = ProtectionModel(grid, ys, 75., settings(), names=["repeat A", "repeat B"])
    averaged = ProtectionModel(grid, ys.mean(axis=0), 75., settings())
    assert not raw.diagnostics(response)["feasible"]
    assert averaged.diagnostics(response)["feasible"]
    assert raw.diagnostics(response)["records"][1]["added_deficit_rms_db"] == 2.


def test_vectorized_slacks_equal_scalar_and_keep_empty_historical_shape():
    grid = a._log_grid(30., 200.)
    ys = np.array([75. + np.sin(np.log(grid)), 76. + np.cos(np.log(grid))])
    responses = np.array([np.zeros(len(grid)), np.full(len(grid), -1.), np.full(len(grid), 1.)])
    model = ProtectionModel(grid, ys, 75., settings())
    assert np.allclose(model.slacks(responses), np.array([model.slacks(r) for r in responses]))
    assert model.slacks(responses).shape == (3, len(model.constraint_names))
    legacy = ProtectionModel(grid, ys, 75., {**settings(), "guard_policy": "legacy"})
    assert legacy.slacks(responses).shape == (3, 0)
    assert legacy.diagnostics(responses[1])["feasible"] is None


@pytest.mark.parametrize("lo,hi,scope,coverage", [(30., 200., "full_range", True),
                                               (100., 200., "available_overlap", False),
                                               (300., 500., "unavailable", False)])
def test_fixed_bass_range_coverage_is_explicit(lo, hi, scope, coverage):
    grid = a._log_grid(lo, hi)
    model = ProtectionModel(grid, np.full((1, len(grid)), 80.), 75., settings())
    d = model.diagnostics(np.full(len(grid), -2.))["bass_mean_cut"]
    assert d["scope"] == scope and d["coverage_complete"] is coverage
    if scope == "unavailable":
        assert d["mean_db"] is None and d["actual_min_hz"] is None
        assert not any("bass" in n for n in model.constraint_names)
    else:
        assert d["mean_db"] == 2.
        assert (d["coverage_fraction"] == 1.) is coverage


def test_manual_guard_preserves_historical_setting_and_rejects_raw_repeat_issue():
    ms = [measurement("L", offset=5.), measurement("L", offset=-1.)]
    filters = {"L": [peak(100., -3., 2.)]}
    before = deepcopy(ms)
    old = a.evaluate_peq(ms, dict(target_level=75., max_q=9.), filters)
    assert old["settings"]["guard_policy"] == "legacy"
    assert not old["explanation"]["protection"]["enforced"]
    assert old["explanation"]["protection"]["channels"]["L"]["record_count"] == 2
    with pytest.raises(ValueError, match="逐筆量測保護"):
        a.evaluate_peq(ms, settings(added_deficit_rms_limit_db=.1), filters)
    assert ms == before


def test_posthoc_legacy_explanation_reports_unenforced_new_guards_without_editing():
    ms = [measurement("L")]
    filters = {"L": [peak(100., -6., 9.)]}
    before = deepcopy(filters)
    result = a.explain_peq(ms, {"max_q": 9.}, filters, 75.)
    assert result["cost_provenance"] == "posthoc_current_policy"
    assert not result["protection"]["enforced"]
    assert filters == before


def test_v5_settings_explicitly_select_peak_without_changing_q_cap():
    assert a._settings({})["guard_policy"] == "legacy"
    s = a._settings(dict(guard_policy="v5", max_q=9.))
    assert s["objective_mode"] == "peak" and s["max_q"] == 9.
    with pytest.raises(ValueError, match="added_deficit"):
        a._settings(dict(added_deficit_rms_limit_db=float("nan")))


def test_boost_support_requires_each_recording_not_only_position_average():
    ms = [measurement("L", [peak(90., -3., 1.5)], position=pos)
          for pos in ("P0", "P-10", "P+10")]
    ms.append(measurement("L", position="P0"))
    filters = {"L": [peak(90., .5, 1.5)]}
    s = settings(objective_mode="shape", allow_boost=True)
    # The average P0 is a shallow dip, but one P0 repeat has no dip to support
    # boosting. Guard v5 must not infer consistent evidence from that average.
    old = a.evaluate_peq(ms, {**s, "guard_policy": "legacy"}, filters)
    assert old["filters"]["L"][0]["gain"] == .5
    with pytest.raises(ValueError, match="增益缺少"):
        a.evaluate_peq(ms, s, filters)


def test_complete_500_hz_incumbent_survives_weaker_bass_first_stages(monkeypatch):
    from roompilot import solver

    ms = [measurement("L", [peak(90., 4., 4.), peak(350., 3., 3.)])]
    s = settings(f_max=500., allow_extended=True, strategy="bass_first", mode="standard")
    incumbent = a.evaluate_peq(ms, s, {"L": [peak(90., -2., 4.), peak(350., -1.5, 3.)]})
    incumbent["id"] = "known-wide-plan"
    def weaker_fit(*args, **kwargs):
        return [], 1, dict(skipped=[], reason_codes=[], merges=0)
    monkeypatch.setattr(solver, "fit_strategy", weaker_fit)
    result = a.generate_peq(ms, s, incumbent_peq=incumbent)
    assert result["filters"] == incumbent["filters"]
    assert result["complete_incumbent_check"]["retained"]
    assert result["allocation"]["retained_complete_incumbent"]
    assert result["allocation"]["attempted_search"]["channels"]["L"]["new_count"] == 0
    assert result["explanation"]["cost_provenance"] == "current_policy_reused_feasible_solution"
    assert result["objective"]["channels"]["L"]["after"]["value"] == pytest.approx(
        result["explanation"]["summary"]["after"]["primary_rms_db"] ** 2)
    assert result["settings"]["guard_policy"] == "v5" and result["target_level"] == 75.


def test_newly_covered_window_reports_exact_rejection_instead_of_implicit_relaxation():
    full = a._log_grid(30., 500.)
    cut = np.where((full >= 190.) & (full <= 220.), -6., 0.)
    s = settings(local_cut_limit_db=3.)
    narrow = full <= 200.
    before = ProtectionModel(full[narrow], np.full((1, narrow.sum()), 90.), 75., s)
    after = ProtectionModel(full, np.full((1, len(full)), 90.), 75., s)
    assert before.diagnostics(cut[narrow])["feasible"]
    assert not after.diagnostics(cut)["feasible"]
    message = after.describe_violations(cut)
    assert "局部" in message and "Hz 平均減益" in message and "上限 3.000 dB" in message


def test_boundary_stack_cannot_export_attenuation_outside_the_fitting_domain():
    ms = [measurement("L", offset=20.)]
    filters = {"L": [peak(200., -2.7, 2.62), peak(200., -5.9, 9.)]}
    s = settings()
    posthoc = a.explain_peq(ms, s, filters, 75.)["protection"]
    ch = posthoc["channels"]["L"]
    assert ch["measured_feasible"]
    assert not ch["feasible"] and not ch["footprint"]["feasible"]
    assert posthoc["summary"]["max_outside_cut_db"] > 8.59
    assert posthoc["summary"]["max_full_local_cut_db"] > 4.
    assert any(c["name"] == "outside_range_cut" and not c["passed"] for c in ch["constraints"])
    with pytest.raises(ValueError, match="完整 EQ 曲線保護"):
        a.evaluate_peq(ms, s, filters)
    # Historical snapshots are not silently subjected to a new restriction.
    old = a.evaluate_peq(ms, {**s, "guard_policy": "legacy"}, filters)
    assert not old["explanation"]["protection"]["channels"]["L"]["footprint"]["enforced"]


def test_full_curve_window_still_limits_boundary_stack_when_outside_cap_is_relaxed():
    s = settings(outside_cut_limit_db=30.)
    model = CurveFootprint(s)
    response = a.filter_response(model.grid, [peak(200., -2.7, 2.62), peak(200., -5.9, 9.)])
    slacks = model.slacks(response)
    assert slacks[0] > 0 and slacks[1] < 0
    assert "完整 EQ 的局部" in model.describe_violations(response)


def test_footprint_grid_has_boundary_probes_without_weighting_local_windows_twice():
    model = CurveFootprint(settings())
    assert model.grid[0] == 1. and model.grid[-1] == 24000.
    assert np.any(model.grid == 200. * (1 + 1e-7))
    assert np.any(model.grid == 30. * (1 - 1e-7))
    assert not np.any(model.local_grid == 200. * (1 + 1e-7))
    responses = np.array([np.zeros(len(model.grid)), np.full(len(model.grid), -2.), np.full(len(model.grid), -4.)])
    assert np.allclose(model.slacks(responses), np.array([model.slacks(r) for r in responses]))
    assert np.allclose(model.slacks(responses), [[3., 4.], [1., 2.], [-1., 0.]])
    legacy = CurveFootprint({**settings(), "guard_policy": "legacy"})
    assert legacy.slacks(responses).shape == (3, 0)
