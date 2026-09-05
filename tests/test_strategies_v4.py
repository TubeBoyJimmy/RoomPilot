"""End-to-end strategy semantics, separate from the solver's internal tests."""
from copy import deepcopy
import numpy as np
import pytest

from roompilot import analysis as a
from roompilot.comparison import generate_peq_candidates
from test_analysis import measurement, peak


def settings(**kw):
    return dict(objective_mode="peak", bands=4, target_level=75., max_q=9., mode="standard", **kw)


def test_three_strategies_share_target_use_internal_budgets_and_keep_identical_slots():
    ms = [measurement(ch, [peak(85, 5, 3), peak(130,-2,2)]) for ch in ("L", "R")]
    before=deepcopy(ms)
    result=generate_peq_candidates(ms,settings(),low_cut_limit_db=1.)
    assert result["schema_version"]==2 and len(result["candidates"])==3
    assert [c["low_cut_limit_db"] for c in result["candidates"]]==[.35,.65,1.]
    previous=float("inf")
    for c in result["candidates"]:
        q=c["result"]
        assert q["target_level"]==75.
        assert q["settings"]["low_cut_limit_db"]==c["low_cut_limit_db"]
        assert c["metrics"]["primary_rms_db"]<=previous+1e-9
        previous=c["metrics"]["primary_rms_db"]
        assert q["explanation"]["evaluation_policy"]["hard_low_cut_constraint"]
        for pos in q["explanation"]["positions"]:
            assert pos["after"]["below_target_cut_rms_db"]<=c["low_cut_limit_db"]+1e-6
    assert ms==before
    flat=generate_peq_candidates([measurement()],settings(),low_cut_limit_db=0.)
    assert len(flat["candidates"])==3
    assert all(len(c["identical_to"])==2 for c in flat["candidates"])


def test_auto_target_provenance_and_reopen_extension_budget_floor():
    ms=[measurement(ch,[peak(70,4,3),peak(360,4,3)]) for ch in ("L","R")]
    s=settings(); s["target_level"]=None
    pool=generate_peq_candidates(ms,s,low_cut_limit_db=1.)
    assert all(c["result"]["settings"]["target_level"] is None for c in pool["candidates"])
    base=pool["candidates"][1]["result"]; base["id"]="specific-variant"
    enlarged=generate_peq_candidates(ms,{**s,"f_max":500.,"allow_extended":True,"strategy":"extend_existing"},base_peq=base,low_cut_limit_db=1.)
    floor=enlarged["frozen_low_cut_floor_db"]
    for ratio,c in zip((.35,.65,1.),enlarged["candidates"]):
        assert c["low_cut_limit_db"]==pytest.approx(floor+(1-floor)*ratio)
        assert c["result"]["target_level"]==base["target_level"]
        assert c["result"]["target_reference"]["source_peq_id"]=="specific-variant"
        for channel,bands in base["filters"].items():
            assert c["result"]["filters"][channel][:len(bands)]==bands
        assert max(c["result"]["allocation"]["bass_drift_db"].values())<=.5+1e-6


def test_manual_peak_mode_cannot_bypass_low_cut_constraint():
    ms=[measurement("L",[peak(90,-2,2)])]
    with pytest.raises(ValueError,match="低處額外減益"):
        a.evaluate_peq(ms,{**settings(),"low_cut_limit_db":.01},{"L":[peak(90,-1,2)]},75.)


def test_boost_preamp_uses_summed_curve_and_separate_total_cap():
    ms=[measurement("L",[peak(90,-3,1.5)],position=pos) for pos in ("P0","P-10","P+10")]
    s={**settings(),"objective_mode":"shape","allow_boost":True,"max_boost":2.,"max_total_boost":4.,"supports_preamp":True,"preamp_margin_db":1.}
    filters={"L":[peak(90,1.8,1.5),peak(90,1.8,1.5)]}
    q=a.evaluate_peq(ms,s,filters,75.)
    assert q["preamp_db"]<=-4.6+1e-9
    assert q["metrics"]["max_boost_db"]>3.59
    with pytest.raises(ValueError,match="總 Gain"):
        a.evaluate_peq(ms,{**s,"max_total_boost":3.},filters,75.)
    with pytest.raises(ValueError,match="前級"):
        a.evaluate_peq(ms,{**s,"supports_preamp":False},filters,75.)


def test_more_than_twenty_bands_are_validated_and_shape_without_evidence_is_explicit():
    ms=[measurement("L",[peak(85,4,3),peak(130,-2,2)])]
    s={**settings(),"bands":32}
    q=a.evaluate_peq(ms,s,{"L":[peak(85,0,3) for _ in range(32)]},75.)
    assert len(q["filters"]["L"])==32
    q=a.generate_peq(ms,{**s,"bands":2,"objective_mode":"shape","allow_boost":True})
    assert all(b["gain"]<=0 for b in q["filters"]["L"])
    assert any("P0" in warning and "10 cm" in warning for warning in q["warnings"])


def test_extension_cannot_override_requested_manual_target_before_validation():
    ms=[measurement("L",[peak(75,4,3),peak(350,4,3)])]
    base=a.generate_peq(ms,settings()); base["id"]="base"
    with pytest.raises(ValueError,match="目標"):
        generate_peq_candidates(ms,{**settings(),"target_level":70.,"f_max":500.,"allow_extended":True,"strategy":"extend_existing"},base_peq=base)


def test_manual_boost_obeys_same_unsupported_tail_limit_as_solver():
    ms=[measurement("L",[peak(90,-3,1.5)],position=pos) for pos in ("P0","P-10","P+10")]
    s={**settings(),"objective_mode":"shape","allow_boost":True,"max_boost":6.,"max_total_boost":12.}
    with pytest.raises(ValueError,match="缺少支持"):
        a.evaluate_peq(ms,s,{"L":[peak(90,4,1.5),peak(90,4,1.5)]},75.)
