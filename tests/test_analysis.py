import json
from copy import deepcopy

import numpy as np
import pytest

from roompilot.analysis import compare_verification, evaluate_peq, filter_response, generate_peq, quality_report


def measurement(channel="L", bands=None, offset=0., position="P0", suffix="A", complete=True):
    f = np.geomspace(20, 20000, 1800)
    y = 75. + filter_response(f, bands or []) + offset
    md = dict(cal_status="loaded", cal_name="12345.txt", clipping=False,
              headroom_db=12., sample_rate=48000, sweep_length=131072,
              sweep_level_dbfs=-12., input_device="UMIK", output_device="USB",
              timing_reference="Acoustic", spl_calibrated=True, snr_db=50.) if complete else {}
    return dict(id=channel + position + suffix, name=channel + suffix, channel=channel,
                position=position, role="baseline", applied_peq_id="",
                frequency=f.tolist(), spl=y.tolist(), metadata=md)


def peak(fc=70., gain=6., q=3.):
    return dict(frequency=fc, gain=gain, q=q, enabled=True, type="PK")


def test_biquad_center_gain_reciprocity_and_disable():
    f = np.array([0., 20., 70., 200., 20000., 24000.])
    b = peak()
    assert filter_response([70.], [b])[0] == pytest.approx(6., abs=1e-7)
    assert np.max(np.abs(filter_response(f, [b, peak(gain=-6)]))) < 1e-7
    assert np.max(np.abs(filter_response(f, [{**b, "enabled": False}]))) == 0
    assert np.all(np.isfinite(filter_response(f, [b], 48000)))


def test_synthetic_known_peak_recovers_and_rounds():
    ms = [measurement("L", [peak()], suffix=x) for x in ("A", "B")]
    result = generate_peq(ms, dict(bands=3, target_level=75.))
    filters = result["filters"]["L"]
    assert 1 <= len(filters) <= 3
    main = min(filters, key=lambda b: b["gain"])
    assert abs(main["frequency"] - 70) <= 3
    assert result["metrics"]["predicted_rmse_db"] < .6
    assert result["metrics"]["improvement_db"] > 1
    assert result["preamp_db"] == 0
    assert result["replacement"] is True
    json.dumps(result, allow_nan=False)
    for b in filters:
        assert -6 <= b["gain"] <= 0
        assert 30 <= b["frequency"] <= 200
        assert .4 <= b["q"] <= 6
        assert b["frequency"] == round(b["frequency"])
        assert b["gain"] * 10 == pytest.approx(round(b["gain"] * 10))
        assert b["q"] * 100 == pytest.approx(round(b["q"] * 100))


def test_shared_protects_opposite_channel_deep_dip():
    ms = [measurement("L", [peak(gain=8)]), measurement("R", [peak(gain=-8)])]
    result = generate_peq(ms, dict(bands=4, independent=False, target_level=75.))
    response = filter_response(np.geomspace(30, 200, 3000), result["filters"]["Shared"])
    assert abs(filter_response([70], result["filters"]["Shared"])[0]) < .75
    assert response.max() <= .00001
    assert set(result["filters"]) == {"Shared"}


def test_rounded_combined_cut_bound_and_unusual_steps():
    ms = [measurement("L", [peak(60, 10, 2), peak(80, 8, 3)])]
    result = generate_peq(ms, dict(bands=4, target_level=75., max_total_cut=4., max_cut=3., freq_step=3., q_step=.07, gain_step=.3))
    bands = result["filters"]["L"]
    f = np.unique(np.r_[np.geomspace(1, 23999, 20000), [b["frequency"] for b in bands]])
    assert filter_response(f, bands).min() >= -4.00001
    for b in bands:
        assert -3 <= b["gain"] <= 0
        assert b["frequency"] / 3 == pytest.approx(round(b["frequency"] / 3))
        assert b["q"] / .07 == pytest.approx(round(b["q"] / .07))
        assert b["gain"] / .3 == pytest.approx(round(b["gain"] / .3))


def test_flat_and_rolloff_do_not_fill_bands_or_boost():
    m = measurement()
    f = np.asarray(m["frequency"])
    m["spl"] = (75 - np.maximum(0, 20 * np.log10(60 / f))).tolist()
    result = generate_peq([m], dict(bands=10, target_level=75.))
    assert result["filters"]["L"] == []


def test_repeats_average_db_without_phase_or_level_normalization():
    left_a = measurement("L", [peak()], offset=1, suffix="A")
    left_b = measurement("L", [peak()], offset=-1, suffix="B")
    left_a["phase"] = [0] * len(left_a["frequency"])
    left_b["phase"] = [180] * len(left_b["frequency"])
    right = measurement("R", [peak()], offset=-4)
    result = generate_peq([left_a, left_b, right], dict(bands=1, target_level=75.))
    base = {c["channel"]: c for c in result["curves"] if c["kind"] == "baseline"}
    assert np.mean(np.array(base["L"]["spl"]) - base["R"]["spl"]) == pytest.approx(4.)
    assert result["metrics"]["repeatability_db"] == pytest.approx(2.)
    assert result["target_level"] == 75


def test_quality_unknown_never_becomes_pass_and_missing_cal_warns():
    a = measurement(complete=False)
    b = measurement("R", offset=-5)
    b["metadata"]["cal_status"] = "missing"
    report = quality_report([a, b])
    for code in ("mic_cal", "clipping", "headroom", "sweep_settings", "spl_calibration", "noise"):
        assert any(x["code"] == code and x["measurement_ids"] == [a["id"]] and x["level"] == "unknown" for x in report)
    assert any(x["code"] == "mic_cal" and x["level"] == "warning" for x in report)
    assert any(x["code"] == "imbalance" and x["level"] == "warning" for x in report)
    assert not any(x["level"] == "error" for x in report)


@pytest.mark.parametrize("change", [lambda m: m["spl"].pop(), lambda m: m["spl"].__setitem__(10, float("nan")), lambda m: m["frequency"].__setitem__(10, m["frequency"][9])])
def test_corrupt_arrays_rejected(change):
    m = measurement()
    change(m)
    assert any(i["level"] == "error" and i["code"] == "invalid_arrays" for i in quality_report([m]))
    with pytest.raises(ValueError):
        generate_peq([m], {})


@pytest.mark.parametrize("settings", [dict(bands=0), dict(bands=2.5), dict(f_max=1000), dict(min_q=8, max_q=2), dict(freq_step=1000), dict(gain_step=float("nan")), dict(sample_rate=0)])
def test_invalid_settings(settings):
    with pytest.raises(ValueError):
        generate_peq([measurement()], settings)


def test_boost_requires_multipoz_evidence_and_no_revision_stacking():
    m = measurement("L", [peak(gain=-2, q=1)])
    result = generate_peq([m], dict(bands=2, target_level=75., allow_boost=True))
    assert all(b["gain"] <= 0 for b in result["filters"]["L"])
    assert any("10 cm" in w for w in result["warnings"])
    m["applied_peq_id"] = "previous"
    with pytest.raises(ValueError, match="Baseline"):
        generate_peq([m], {})


def verified(baseline, peq, extra_db=0, actual_filters=None):
    m = deepcopy(baseline)
    m.update(id=m["id"] + "_verified", role="verification", applied_peq_id=peq["id"])
    bs = actual_filters if actual_filters is not None else peq["filters"].get(m["channel"], peq["filters"].get("Shared", []))
    m["spl"] = (np.asarray(m["spl"]) + filter_response(m["frequency"], bs) + peq.get("preamp_db", 0) + extra_db).tolist()
    return m


def test_verification_true_correction_vs_volume_only():
    ms = [measurement("L", [peak()], suffix=x) for x in ("A", "B")]
    peq = generate_peq(ms, dict(bands=2, target_level=75.))
    peq["id"] = "v1"
    check = compare_verification(ms, [verified(ms[0], peq)], peq)
    assert check["status"] == "improved"
    assert check["metrics"]["predicted_agreement_db"] < .01
    volume_only = compare_verification(ms, [verified(ms[0], peq, extra_db=-4, actual_filters=[])], peq)
    assert volume_only["status"] == "needs_confirmation"
    assert volume_only["metrics"]["shape_assessment"] == "unchanged"
    assert abs(volume_only["metrics"]["improvement_db"]) < .0001
    json.dumps(volume_only, allow_nan=False)


def test_verification_no_metadata_no_unqualified_success_and_offset_not_before_after():
    b = measurement("L", [peak()], complete=False)
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    after = verified(b, peq)
    result = compare_verification([b], [after], peq)
    assert result["status"] == "needs_confirmation"
    assert any("無法自動確認" in d for d in result["details"])
    after["position"] = "P+10"
    result = compare_verification([b], [after], peq)
    assert result["status"] == "consistency_only"
    assert "improvement_db" not in result["metrics"]


def test_cancel_is_observed():
    with pytest.raises(InterruptedError):
        generate_peq([measurement("L", [peak()])], {}, cancel=lambda: True)


def test_session_route_change_is_incomparable_and_volume_note_needs_confirmation():
    b = measurement("L", [peak()])
    b["metadata"]["session_conditions"] = dict(mic_orientation="90°", route_note="Roon file", volume_note="-20 dB")
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq)
    a["metadata"]["session_conditions"]["route_note"] = "REW direct"
    assert compare_verification([b], [a], peq)["status"] == "incomparable"
    a = verified(b, peq)
    a["metadata"]["session_conditions"]["volume_note"] = "-25 dB"
    assert compare_verification([b], [a], peq)["status"] == "needs_confirmation"


def test_same_cal_name_different_contents_warns():
    left, right = measurement("L"), measurement("R")
    left["metadata"]["cal_fingerprint"] = "abc"
    right["metadata"]["cal_fingerprint"] = "def"
    assert any(i["code"] == "inconsistent_cal_fingerprint" and i["level"] == "warning" for i in quality_report([left, right]))


def test_missing_right_verification_not_whole_system_success():
    b = [measurement("L", [peak()]), measurement("R", [peak()])]
    peq = dict(id="p", filters={"Shared": [peak(gain=-6)]}, settings={}, target_level=75)
    result = compare_verification(b, [verified(b[0], peq)], peq)
    assert result["status"] == "needs_confirmation"
    assert any("R · P0" in d for d in result["details"])


def test_fft_boundary_tolerance_clamps_instead_of_extrapolating():
    m = measurement("L", [peak()])
    f = np.arange(30.029296875, 500, .3662109375)
    m["frequency"] = f.tolist()
    m["spl"] = (75 + filter_response(f, [peak()])).tolist()
    assert not any(i["code"] == "coverage" for i in quality_report([m], {"f_min": 30, "f_max": 200}))
    result = generate_peq([m], dict(bands=1, target_level=75))
    assert result["curves"][0]["frequency"][0] == 30.029296875
    assert result["metrics"]["evaluated_f_min"] == 30.029296875
    assert all(c["frequency"][0] >= min(m["frequency"]) for c in result["curves"])
    with pytest.raises(ValueError, match="校正頻段"):
        generate_peq([m], {"f_min": 20})


def test_unpaired_offsets_are_visible_alongside_p0_and_have_current_spread():
    b = measurement("L", [peak()])
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    after = [verified(b, peq)]
    for pos, offset in (("P-10", 1.), ("P+10", -1.)):
        m = verified(b, peq, extra_db=offset)
        m["position"] = pos
        after.append(m)
    result = compare_verification([b], after, peq)
    assert {c["position"] for c in result["curves"] if c["kind"] == "verified"} == {"P0", "P-10", "P+10"}
    spread = result["metrics"]["spatial_consistency"]["L"]
    assert spread["rms_spread_db"] == pytest.approx(2.)
    assert spread["scope"] == "after_only"
    assert set(result["metrics"]["positions"]) == {"L:P0"}


def test_one_channel_worse_cannot_hide_behind_mean_improvement():
    b = [measurement("L", [peak(gain=10)]), measurement("R")]
    peq = dict(id="p", filters={"L": [peak(gain=-10)], "R": [peak(gain=-3)]}, settings={"max_cut": 12}, target_level=75)
    after = [verified(m, peq) for m in b]
    result = compare_verification(b, after, peq)
    assert result["metrics"]["improvement_db"] > 0
    assert result["metrics"]["worst_position_improvement_db"] < -.5
    assert result["metrics"]["shape_assessment"] == "worse"
    assert result["status"] != "improved"
    assert any("R · P0" in d and "惡化" in d for d in result["details"])


def test_window_origin_timing_noise_is_not_a_config_change():
    b = measurement("L", [peak()])
    b["metadata"]["analysis"] = dict(smoothing="None", fdw_enabled=False, windows=dict(startTime=-1.000000001, winRefIndex=55000, preType="Hann", postType="Hann", preImpulseDurn=100., postImpulseDurn=500.))
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq)
    a["metadata"]["analysis"]["windows"].update(startTime=-1.000000006, winRefIndex=55002)
    assert compare_verification([b], [a], peq)["status"] == "improved"
    a["metadata"]["analysis"]["windows"]["postImpulseDurn"] = 100.
    assert compare_verification([b], [a], peq)["status"] == "needs_confirmation"


def test_existing_band_mismatch_produces_actionable_check_not_new_filters():
    b = measurement("L", [peak()])
    peq = dict(id="p", filters={"L": [peak(gain=-6)]}, settings={}, target_level=75)
    a = verified(b, peq, actual_filters=[])
    result = compare_verification([b], [a], peq)
    assert result["adjustments"]
    assert result["adjustments"][0]["frequency"] == 70
    assert "核對" in result["adjustments"][0]["suggestion"]
    assert "filters" not in result


def test_explicit_broad_boost_with_three_positions_computes_safe_preamp():
    ms = [measurement("L", [peak(fc=100, gain=-2.5, q=1)], position=p) for p in ("P0", "P-10", "P+10")]
    result = generate_peq(ms, dict(bands=2, target_level=75, allow_boost=True))
    bands = result["filters"]["L"]
    assert any(b["gain"] > 0 for b in bands)
    assert all(b["q"] <= 2 for b in bands if b["gain"] > 0)
    response = filter_response(np.geomspace(1, 23999, 10000), bands)
    assert response.max() + result["preamp_db"] <= -.49
    assert response.max() <= 3.0001


def test_manual_evaluation_preserves_evidence_metrics_and_disabled_band_order():
    ms = [measurement("L", [peak()], offset=shift, suffix=str(shift)) for shift in (-.1, .1)]
    bands = {"L": [peak(gain=-4), {**peak(fc=150, gain=-3), "enabled": False}]}
    before = deepcopy(bands)
    result = evaluate_peq(ms, dict(bands=3), bands, target_level=75.)
    assert result["filters"] == bands == before
    assert result["metrics"]["manual_edit"] is True
    assert result["metrics"]["repeatability_db"] == pytest.approx(.2)
    assert result["metrics"]["max_attenuation_db"] == pytest.approx(4., abs=.001)
    assert "L:P0" in result["metrics"]["channel_metrics"]
    assert result["target_level"] == 75.
    assert result["preamp_db"] == 0.
    assert any("未扣除前級" in line for line in result["rationale"])
    json.dumps(result, allow_nan=False)


def test_manual_boost_cannot_bypass_missing_positions_or_spatial_support():
    baseline = [measurement("L", [peak(gain=-2)])]
    with pytest.raises(ValueError, match="10 cm"):
        evaluate_peq(baseline, {"allow_boost": True}, {"L": [peak(gain=1, q=1)]}, 75.)
    baseline = [measurement("L", [peak(gain=-2, q=1)], position=p) for p in ("P0", "P-10", "P+10")]
    baseline[-1] = measurement("L", [peak(gain=3, q=1)], position="P+10")
    with pytest.raises(ValueError, match="一致"):
        evaluate_peq(baseline, {"allow_boost": True}, {"L": [peak(gain=1, q=1)]}, 75.)


def test_manual_cut_below_target_warns_and_overlapping_gain_rejected():
    ms = [measurement("L", [peak()]), measurement("R", [peak(gain=-8)])]
    result = evaluate_peq(ms, {"independent": False}, {"Shared": [peak(gain=-3)]}, 75.)
    assert result["filters"]["Shared"] == [peak(gain=-3)]
    assert any("額外減益" in w for w in result["warnings"])
    with pytest.raises(ValueError, match="總 Gain"):
        evaluate_peq([measurement("L", [peak(gain=12)])], {"max_total_cut": 4}, {"L": [peak(gain=-3), peak(gain=-3)]}, 75.)


def test_manual_and_generated_boost_share_preamp_and_prediction_convention():
    ms = [measurement("L", [peak(fc=100, gain=-2.5, q=1)], position=p) for p in ("P0", "P-10", "P+10")]
    generated = generate_peq(ms, dict(bands=2, target_level=75, allow_boost=True))
    manual = evaluate_peq(ms, generated["settings"], generated["filters"], generated["target_level"])
    assert manual["preamp_db"] == generated["preamp_db"]
    assert manual["metrics"]["predicted_rmse_db"] == pytest.approx(generated["metrics"]["predicted_rmse_db"], abs=1e-10)
    assert [c["spl"] for c in manual["curves"]] == [c["spl"] for c in generated["curves"]]


def test_manual_rounding_rejects_outside_boundary_and_band_count():
    ms = [measurement("L", [peak()])]
    with pytest.raises(ValueError, match="取整"):
        evaluate_peq(ms, {"freq_step": 7}, {"L": [peak(fc=200, gain=-1)]}, 75.)
    with pytest.raises(ValueError, match="Band 數"):
        evaluate_peq(ms, {"bands": 1}, {"L": [peak(gain=-1), peak(gain=-1)]}, 75.)
