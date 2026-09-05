"""Policy separation, trace reconstruction, and bounded candidate semantics."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import numpy as np
import pytest

import roompilot.analysis as a
from roompilot.comparison import generate_peq_candidates


def band(fc=70., gain=6., q=3., enabled=True):
    return dict(frequency=fc, gain=gain, q=q, enabled=enabled, type="PK")


def measurement(ch="L", pos="P0", peaks=None, offset=0., suffix="A"):
    f = a._log_grid(20., 600.)
    y = 75. + offset + a.filter_response(f, peaks or [])
    return dict(id=ch+pos+suffix, name=ch+pos+suffix, channel=ch, position=pos,
                role="baseline", applied_peq_id="", frequency=f.tolist(), spl=y.tolist(),
                metadata=dict(cal_status="loaded", clipping=False))


def settings(**kw):
    return {**dict(bands=1, target_level=75., min_q=.4, max_q=9., max_cut=6., max_total_cut=9.,
                   f_min=30., f_max=200., mode="deep"), **kw}


def test_scale_one_preserves_known_v02_filter_and_scales_are_not_global():
    ms = [measurement(peaks=[band()])]
    control = a.generate_peq(ms, settings())
    assert control["filters"]["L"] == [band(70., -5.8, 3.47)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(a.generate_peq, ms, settings(overshoot_scale=scale)) for scale in (0.,1.)]
        weak, same = [f.result() for f in futures]
    assert same["filters"] == control["filters"]
    assert weak["filters"] != control["filters"]
    assert control["objective"]["overshoot_scale"] == 1.
    assert weak["objective"]["channels"]["L"]["after"]["overshoot"] == 0.
    # Fixed-filter diagnostics must be independent of the search penalty.
    d1 = a.explain_peq(ms, settings(), control["filters"], 75.)
    d0 = a.explain_peq(ms, settings(overshoot_scale=0.), control["filters"], 75.)
    assert d1["summary"] == d0["summary"]


@pytest.mark.parametrize("bad", [-.1, 1.1, float("nan"), float("inf"), True, "0"])
def test_overshoot_setting_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        a.generate_peq([measurement()], settings(overshoot_scale=bad))


def test_explanation_conditional_denominator_averages_repeats_and_marks_rows():
    ms = [measurement(peaks=[band(),band(130.,-8.,4.)], offset=offset, suffix=str(i)) for i,offset in enumerate([-1.,1.])]
    filters = {"L":[band(70.,-4.32,12.34), band(130.,-1.,2.,False)]}
    original = deepcopy(filters)
    e = a.explain_peq(ms, settings(), filters, 75., frozen_counts={"L":1})
    grid = a._log_grid(30.,200.)
    ys, _, _ = a._group_curves(ms, grid)
    response = a.filter_response(grid, filters["L"])
    lower = ys[0] < 75.
    expected = np.sqrt(np.mean(np.maximum(-response[lower],0.)**2))
    diluted = np.sqrt(np.mean(np.where(lower, np.maximum(-response,0.),0.)**2))
    p = e["positions"][0]["after"]
    assert p["below_target_cut_rms_db"] == pytest.approx(expected)
    assert expected > diluted
    assert p["below_target_bin_count"] == int(lower.sum())
    assert len(e["positions"]) == 1
    assert filters == original  # no rounding or current max-Q rejection
    assert e["bands"][0]["frozen"]
    disabled = e["bands"][1]
    assert disabled["enabled"] is False
    assert disabled["with_band"] == disabled["without_band"]
    assert all(v == 0 for v in disabled["native_cost"]["benefit"].values())
    assert e["cost_provenance"] == "posthoc_current_policy"
    json.dumps(e,allow_nan=False)


def test_explanation_weights_and_empty_lower_mask_are_explicit():
    ms = [measurement("L","P0",offset=4.), measurement("L","P-10",offset=2.),
          measurement("L","P+10",offset=2.), measurement("R","P0",offset=1.)]
    e = a.explain_peq(ms,settings(),{"L":[],"R":[]},75.)
    weights = {p["id"]:p["weight"] for p in e["positions"]}
    assert weights == pytest.approx({"L:P0":.335,"L:P-10":.0825,"L:P+10":.0825,"R:P0":.5})
    assert e["summary"]["after"]["residual_peak_rms_db"] == pytest.approx(np.sqrt(.335*16+.165*4+.5))
    assert all(p["after"]["below_target_bin_count"] == 0 for p in e["positions"])
    assert e["summary"]["after"]["worst_below_target_cut_rms_db"] == 0.


def test_explanation_does_not_hide_invalid_source_or_disabled_invalid_biquad():
    ms = [measurement()]
    ms[0]["spl"][10] = float("nan")
    with pytest.raises(ValueError):
        a.explain_peq(ms,settings(),{"L":[]},75.)
    with pytest.raises(ValueError):
        a.explain_peq([measurement()],settings(),{"L":[band(-1.,-1.,-2.,False)]},75.)


def test_search_trace_costs_reconstruct_rounded_candidate_and_band_counterfactual():
    ms = [measurement(peaks=[band(),band(140.,3.,5.)])]
    result = a.generate_peq(ms,settings(bands=3))
    s = result["settings"]
    grid = a._log_grid(30.,200.)
    ys,names,_ = a._group_curves(ms,grid)
    weights = a._position_weights(names)
    search = result["allocation"]["channels"]["L"]["stages"][0]["search"]
    assert search["rounds"]
    for row in search["rounds"]:
        before = a._objective(grid,ys,75.,s,row["before_filters"],weights,False)
        assert row["before_cost"] == pytest.approx(before)
        if row["candidate_filters"] is not None:
            candidate = a._objective(grid,ys,75.,s,row["candidate_filters"],weights,False)
            assert row["candidate_cost"] == pytest.approx(candidate)
            assert row["improvement"] == pytest.approx(before["value"]-candidate["value"])
        if row["accepted"]:
            assert row["improvement"] > .015
        else:
            assert "本輪搜尋未找到" in row["reason"]
    assert search["final_filters"] == result["filters"]["L"]
    assert search["final_cost"] == pytest.approx(result["objective"]["channels"]["L"]["after"])
    for row in result["explanation"]["bands"]:
        assert row["native_cost"]["benefit"]["value"] == pytest.approx(row["native_cost"]["without_band"]["value"]-row["native_cost"]["with_band"]["value"])


def test_comparison_limit_pareto_target_and_non_recommendation():
    ms = [measurement(peaks=[band(),band(130.,-8.,4.)])]
    values=[]
    c = generate_peq_candidates(ms,settings(),progress=values.append,low_cut_limit_db=0.)
    assert 1 <= len(c["candidates"]) <= 3
    assert {x["result"]["target_level"] for x in c["candidates"]} == {75.}
    assert not c["has_feasible_candidate"]
    assert c["recommended_key"] == ""
    assert all(not x["is_recommended"] and not x["pareto"] and x["limit_violations"] for x in c["candidates"])
    assert values == sorted(values) and values[-1] == 1.
    wide = generate_peq_candidates(ms,settings(),low_cut_limit_db=6.)
    feasible = [x for x in wide["candidates"] if x["within_limit"]]
    assert wide["recommended_key"]
    selected = next(x for x in feasible if x["key"]==wide["selected_key"])
    assert selected["metrics"]["worst_below_target_cut_rms_db"] == min(x["metrics"]["worst_below_target_cut_rms_db"] for x in feasible)
    for x in feasible:
        dominated=any(all(y["metrics"][k]<=x["metrics"][k]+1e-9 for k in wide["metric_axes"])
                      and any(y["metrics"][k]<x["metrics"][k]-1e-9 for k in wide["metric_axes"]) for y in feasible if y is not x)
        assert x["pareto"] is not dominated
    json.dumps(wide,allow_nan=False)


def test_comparison_dedupes_flat_without_recommending_empty_and_boost_is_preview():
    flat = generate_peq_candidates([measurement()],settings())
    assert len(flat["candidates"]) == 1
    assert flat["candidates"][0]["profile_scales"] == [1.,.25,0.]
    assert flat["recommended_key"] == ""
    assert not flat["candidates"][0]["recommendation_eligible"]
    boost = generate_peq_candidates([measurement(peaks=[band()])],settings(allow_boost=True))
    assert boost["recommended_key"] == ""
    assert all(not c["recommendation_eligible"] for c in boost["candidates"])
    assert all(b["gain"] <= 0 for c in boost["candidates"] for b in c["result"]["filters"]["L"])


def test_comparison_retains_auto_target_provenance_while_fixing_numeric_target(monkeypatch):
    import roompilot.comparison as comparison
    calls=[]
    original=comparison.generate_peq
    def record_call(ms,s,*args,**kwargs):
        calls.append(deepcopy(s))
        return original(ms,s,*args,**kwargs)
    monkeypatch.setattr(comparison,"generate_peq",record_call)
    result=comparison.generate_peq_candidates([measurement(peaks=[band(),band(130.,-8.,4.)])],settings(target_level=None))
    assert calls[0]["target_level"] is None
    assert [s["target_level"] for s in calls[1:]] == [result["target_level"]]*2
    for candidate in result["candidates"]:
        fitted=candidate["result"]
        assert fitted["settings"]["target_level"] is None
        assert fitted["target_reference"]["mode"] == "fixed_reference"
        assert "自動目標固定參考" in fitted["rationale"][1]
        assert fitted["comparison_context"]["target_fixed_across_candidates"]


def test_comparison_frozen_extension_keeps_parameters_target_and_labels():
    ms = [measurement(peaks=[band(),band(330.,4.,2.)])]
    base = a.generate_peq(ms,settings(bands=2))
    base["id"] = "incumbent"
    old = deepcopy(base)
    compared = generate_peq_candidates(ms,settings(bands=4,f_max=500.,allow_extended=True,strategy="extend_existing"),base_peq=base)
    count=len(base["filters"]["L"])
    for candidate in compared["candidates"]:
        result=candidate["result"]
        assert result["target_level"] == base["target_level"]
        assert result["filters"]["L"][:count] == base["filters"]["L"]
        assert all(row["frozen"] for row in result["explanation"]["bands"][:count])
    assert base == old


@pytest.mark.parametrize("bad", [-1., 6.1, float("nan"), True, "1"])
def test_comparison_budget_validation_and_cancellation(bad):
    with pytest.raises(ValueError):
        generate_peq_candidates([measurement()],settings(),low_cut_limit_db=bad)
    with pytest.raises(InterruptedError):
        generate_peq_candidates([measurement()],settings(),cancel=lambda:True)


def test_comparison_cancels_between_profiles_without_returning_partial_result():
    stopped = False
    def progress(value):
        nonlocal stopped
        stopped = value >= 1/3
    with pytest.raises(InterruptedError):
        generate_peq_candidates([measurement(peaks=[band()])],settings(),progress=progress,cancel=lambda:stopped)
