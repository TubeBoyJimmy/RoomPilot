from copy import deepcopy
import json

import numpy as np
import pytest

from roompilot.analysis import (DEFAULTS, GRID_PPO, _log_grid, _target_reference,
                               compare_verification, evaluate_peq, filter_response,
                               generate_peq, quality_report, _settings)
from test_analysis import measurement, peak, verified


def baseline_pair():
    return [measurement("L", [peak(70, 6, 3), peak(360, 5, 3)]),
            measurement("R", [peak(105, 5, 4), peak(420, 4, 3)])]


def existing():
    return dict(id="existing", target_level=75., settings=dict(bands=4, target_level=75., max_q=9),
                filters={"L": [peak(70, -5, 3)], "R": [peak(105, -4, 4)]})


def test_fixed_target_independent_of_frequency_range_mode_and_offsets():
    ms = baseline_pair()
    small = generate_peq(ms, dict(f_max=200, bands=1))
    large = generate_peq(ms, dict(f_max=500, allow_extended=True, bands=1, mode="standard"))
    assert small["target_level"] == large["target_level"]
    assert small["target_reference"] == large["target_reference"]
    assert small["target_reference"]["mode"] == "fixed_reference"
    assert small["target_reference"]["requested_min"] == 80
    assert small["target_reference"]["requested_max"] == 200
    assert small["settings"]["mode"] == DEFAULTS["mode"] == "deep"
    offset = measurement("L", [peak(gain=16)], offset=10, position="P+10")
    assert _target_reference(ms + [offset], _settings({}))[0] == small["target_level"]


def test_reference_matches_documented_statistic_and_manual_bypasses_missing_reference():
    ms = baseline_pair()
    grid = np.geomspace(80, 200, int(np.ceil(np.log2(200 / 80) * GRID_PPO)) + 1)
    ys = [np.interp(np.log(grid), np.log(m["frequency"]), m["spl"]) for m in ms]
    expected = np.percentile(np.median(ys, axis=0), 35)
    assert _target_reference(ms, _settings({}))[0] == expected
    m = measurement()
    keep = np.asarray(m["frequency"]) >= 100
    m["frequency"] = np.asarray(m["frequency"])[keep].tolist()
    m["spl"] = np.asarray(m["spl"])[keep].tolist()
    with pytest.raises(ValueError, match="固定目標參考"):
        generate_peq([m], dict(f_min=110, f_max=200))
    result = generate_peq([m], dict(f_min=110, f_max=200, target_level=74))
    assert result["target_level"] == 74
    assert result["target_reference"]["mode"] == "manual"


def test_log_grid_has_identical_interior_points_when_extended():
    a, b = _log_grid(30, 200), _log_grid(30, 500)
    assert np.array_equal(a[a < 200], b[b < 200])


def test_bass_first_freezes_exact_solution_then_adds_other_bands():
    ms = baseline_pair()
    small = generate_peq(ms, dict(bands=4, target_level=75, max_q=9))
    large = generate_peq(ms, dict(bands=4, target_level=75, max_q=9, f_max=500, allow_extended=True))
    for channel in ("L", "R"):
        frozen = small["filters"][channel]
        assert large["filters"][channel][:len(frozen)] == frozen
        assert all(b["frequency"] > 200 for b in large["filters"][channel][len(frozen):])
        assert large["allocation"]["frozen_counts"][channel] == len(frozen)
        assert large["allocation"]["bass_drift_db"][channel] <= .5
    assert any(c["new_count"] > 0 for c in large["allocation"]["channels"].values())
    assert "objective" in large
    assert large["metrics"]["objective_evaluations"] > 0
    assert large["metrics"]["elapsed_seconds"] > 0


def test_extend_existing_preserves_all_values_order_extra_fields_and_target():
    ms, old = baseline_pair(), existing()
    old["filters"]["L"][0]["note"] = "keep this"
    old["filters"]["L"].append({**peak(160, -1.7, 2.2), "enabled": False})
    before = deepcopy(old)
    result = generate_peq(ms, dict(strategy="extend_existing", bands=4, max_q=9,
                                    f_max=500, allow_extended=True), base_peq=old)
    assert old == before
    assert result["target_level"] == old["target_level"]
    assert result["target_reference"]["mode"] == "inherited"
    for ch in ("L", "R"):
        n = len(old["filters"][ch])
        assert result["filters"][ch][:n] == old["filters"][ch]
        assert all(b["frequency"] > 200 for b in result["filters"][ch][n:])
    json.dumps(result, allow_nan=False)


def test_extend_uses_no_budget_without_rearranging_and_insufficient_raises():
    ms, old = baseline_pair(), existing()
    result = generate_peq(ms, dict(strategy="extend_existing", bands=1, max_q=9,
                                    f_max=500, allow_extended=True), base_peq=old)
    assert result["filters"] == old["filters"]
    assert all("no_budget" in c["reason_codes"] for c in result["allocation"]["channels"].values())
    old["filters"]["L"].append(peak(150, -1, 2))
    with pytest.raises(ValueError, match="原方案需要 2 Band"):
        generate_peq(ms, dict(strategy="extend_existing", bands=1, max_q=9,
                              f_max=500, allow_extended=True), base_peq=old)


@pytest.mark.parametrize("change", [dict(independent=False), dict(f_max=150),
                                      dict(f_min=40), dict(max_cut=2), dict(freq_step=3),
                                      dict(sample_rate=96000), dict(target_level=72)])
def test_extend_rejects_incompatible_limits_without_changing_base(change):
    old = existing()
    snapshot = deepcopy(old)
    s = dict(strategy="extend_existing", bands=4, max_q=9, f_max=500, allow_extended=True)
    s.update(change)
    with pytest.raises(ValueError):
        generate_peq(baseline_pair(), s, base_peq=old)
    assert old == snapshot


@pytest.mark.parametrize("limit", [0., .1, .5])
def test_extension_tail_bound_on_dense_independent_grid(limit):
    ms = [measurement("L", [peak(70, 5, 3), peak(225, 9, 1)])]
    old = existing()
    old["filters"] = {"L": old["filters"]["L"]}
    result = generate_peq(ms, dict(strategy="extend_existing", bands=4, max_q=9,
                                    f_max=500, allow_extended=True, bass_drift_limit_db=limit), base_peq=old)
    assert result["filters"]["L"][0] == old["filters"]["L"][0]
    grid = np.geomspace(30, 200, 10000)
    added = result["filters"]["L"][1:]
    assert np.max(np.abs(filter_response(grid, added))) <= limit + 1e-7


def test_repeated_runs_are_deterministic_for_joint_and_independent_channels():
    ms = baseline_pair()
    s = dict(strategy="joint", bands=4, f_max=500, allow_extended=True, target_level=75, max_q=9)
    a, b = generate_peq(ms, s), generate_peq(ms, s)
    assert a["filters"] == b["filters"]
    assert a["objective"] == b["objective"]
    assert a["filters"]["L"] != a["filters"]["R"]
    for ch in ("L", "R"):
        assert a["objective"]["channels"][ch]["after"]["value"] < a["objective"]["channels"][ch]["before"]["value"]


def test_quality_clipping_is_acknowledgeable_but_invalid_arrays_block():
    m = measurement("L", [peak()])
    m["metadata"]["clipping"] = True
    result = quality_report([m], {})
    clipping = [i for i in result if i["code"] == "clipping"][0]
    assert clipping["level"] == "warning" and clipping["blocking"] is False
    peq = generate_peq([m], {"bands": 1})
    assert any("Clipping" in w for w in peq["warnings"])
    m["spl"].pop()
    assert any(i["blocking"] and i["code"] == "invalid_arrays" for i in quality_report([m]))


def test_metadata_override_is_provisional_and_preserves_reasons():
    b = measurement("L", [peak()])
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq)
    a["metadata"]["output_device"] = "Different computer / same DAC"
    normal = compare_verification([b], [a], peq)
    assert normal["status"] == "incomparable"
    assert normal["allow_override"] is True
    bypass = compare_verification([b], [a], peq, allow_mismatch=True)
    assert bypass["status"] == "provisional"
    assert bypass["override_applied"] is True
    assert bypass["reasons"] == normal["reasons"]
    assert bypass["metrics"]["shape_assessment"] == "improved"


def test_project_record_device_names_are_not_imported_recording_evidence():
    b = measurement("L", [peak()])
    b["metadata"].update(conditions_source="project_record", session_conditions=dict(output_device="PC A"))
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq)
    a["metadata"]["session_conditions"]["output_device"] = "PC B"
    result = compare_verification([b], [a], peq)
    assert result["status"] == "improved"
    assert not any("所選輸出裝置" in d for d in result["details"])


def test_override_cannot_create_missing_spectral_data_or_missing_primary_pairs():
    b = baseline_pair()
    peq = existing()
    after = [verified(m, peq) for m in b]
    after[0]["metadata"]["output_device"] = "PC B"
    for m in after:
        keep = np.asarray(m["frequency"]) < 150
        m["spl"] = np.asarray(m["spl"])[keep].tolist()
        m["frequency"] = np.asarray(m["frequency"])[keep].tolist()
    result = compare_verification(b, after, peq, allow_mismatch=True)
    assert result["status"] == "incomparable" and not result["allow_override"]
    assert "improvement_db" not in result["metrics"]
    result = compare_verification(b, [verified(b[0], peq)], peq, allow_mismatch=True)
    assert result["status"] == "needs_confirmation" and not result["allow_override"]


def test_worse_shape_without_metadata_mismatch_cannot_be_skipped():
    b = measurement("L")
    peq = dict(id="p", filters={"L": [peak(gain=-5)]}, settings={}, target_level=75)
    a = verified(b, peq)
    result = compare_verification([b], [a], peq, allow_mismatch=True)
    assert result["metrics"]["shape_assessment"] == "worse"
    assert result["status"] != "provisional"
    assert not result["allow_override"]


def test_pairwise_repeatability_matches_raw_two_recording_difference():
    a = measurement("L", [peak()], offset=1, suffix="A")
    b = measurement("L", [peak()], offset=-1, suffix="B")
    result = generate_peq([a, b], dict(target_level=75, bands=1))
    assert result["metrics"]["repeatability_db"] == pytest.approx(2.)
    assert "配對差值" in result["metrics"]["repeatability_definition"]
    report = quality_report([a, b])
    assert any("2.00 dB RMS" in i["detail"] for i in report if i["code"] == "repeatability")


def test_clipped_baseline_can_only_produce_provisional_verification():
    b = measurement("L", [peak()])
    b["metadata"]["clipping"] = True
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq)
    a["metadata"]["clipping"] = False
    result = compare_verification([b], [a], peq)
    assert result["status"] == "incomparable"
    assert result["allow_override"] is True
    assert any("Baseline" in r and "Clipping" in r for r in result["reasons"])
    assert compare_verification([b], [a], peq, allow_mismatch=True)["status"] == "provisional"
