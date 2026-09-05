"""v5 policy invariants on synthetic responses; no user databases or files."""
from copy import deepcopy
import json

import numpy as np
import pytest

from roompilot import analysis as a
from roompilot.protection import ProtectionModel
from roompilot.solver import (fit_strategy, merge_curve_equivalent,
                             objective_components, peak_regions)


def band(fc=90., gain=-3., q=4., **kw):
    return dict(frequency=fc,gain=gain,q=q,enabled=True,type="PK",**kw)


def settings(**kw):
    return {**a.DEFAULTS,"objective_mode":"peak","guard_policy":"v5",
            "bands":3,"max_q":9.,"mode":"standard",**kw}


def fit(f,ys,s=None,**kw):
    return fit_strategy(f,np.atleast_2d(ys),75.,s or settings(),
                        kw.pop("boost_allowed",False),kw.pop("cancel",None),None,**kw)


def test_raw_record_constraints_survive_averaging_and_rounding():
    f=a._log_grid(30.,200.)
    first=73.+8*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    raw=np.array([first,np.full(len(f),73.)])
    ys=np.mean(raw,axis=0,keepdims=True)
    s=settings(low_cut_limit_db=.2,freq_step=2.,gain_step=.2,q_step=.1)
    bands,_,trace=fit(f,ys,s,raw_ys=raw,raw_names=[("L","P0","A"),("L","P0","B")])
    eq=a.filter_response(f,bands)
    assert trace["protection"]["record_count"]==2
    assert np.sqrt(np.mean(np.maximum(-eq,0.)**2))<=.2+1e-7
    assert np.min(ProtectionModel(f,raw,75.,s).slacks(eq))>=-1e-7
    assert trace["peak_regions"][0]["raw_support_fraction"]==.5
    assert trace["peak_regions"][0]["raw_excess_min_db"]<0
    json.dumps(trace,allow_nan=False)


def test_new_deficit_protects_initially_above_target_bins_during_search():
    f=a._log_grid(30.,200.)
    ys=np.full((1,len(f)),76.)
    s=settings(added_deficit_max_limit_db=.15,added_deficit_rms_limit_db=.1)
    bands,_,trace=fit(f,ys,s)
    eq=a.filter_response(f,bands)
    assert bands
    assert np.max(np.maximum(75-ys-eq,0.))<=.15+1e-7
    assert trace["final_cost"]["worst_below_target_cut_rms_db"]==0
    assert trace["final_cost"]["peak_mse"]<1.
    assert trace["protection"]["worst_added_deficit_max_db"]<=.15+1e-7


def test_peak_width_evidence_distinguishes_narrow_and_broad_features():
    f=a._log_grid(30.,200.)
    narrow=75.+5*np.exp(-.5*(np.log2(f/65.)/.05)**2)
    broad=75.+5*np.exp(-.5*(np.log2(f/140.)/.2)**2)
    ns=peak_regions(f,narrow[None,:],75.,settings(max_q=30.))[0]
    bs=peak_regions(f,broad[None,:],75.,settings(max_q=30.))[0]
    assert ns["seed_q"]>bs["seed_q"]*3
    assert ns["left_shoulder_hz"]<ns["center_hz"]<ns["right_shoulder_hz"]
    assert ns["raw_record_count"]==1
    assert ns["raw_support_fraction"]==1.


@pytest.mark.parametrize("q",[2.,8.])
def test_joint_refit_recovers_known_broad_or_narrow_peak_without_q_penalty(q):
    f=a._log_grid(30.,200.)
    y=75.+a.filter_response(f,[band(90.,4.,q)])
    bands,_,trace=fit(f,y,settings(bands=1,low_cut_limit_db=2.))
    assert len(bands)==1
    assert bands[0]["frequency"]==pytest.approx(90.,abs=1.)
    assert bands[0]["q"]==pytest.approx(q,abs=.15)
    assert bands[0]["gain"]==pytest.approx(-4.,abs=.1)
    assert trace["final_cost"]["peak_mse"]<.001


def test_feasible_incumbent_is_immutable_fallback_when_bounds_are_loosened():
    f=a._log_grid(30.,200.)
    prior=[band(90.,-4.,3.)]
    before=deepcopy(prior)
    y=75.+a.filter_response(f,[band(90.,4.,3.)])
    s=settings(bands=2,max_q=12.)
    bands,_,trace=fit(f,y,s,incumbent=prior)
    assert prior==before
    assert trace["incumbent"]["accepted"]
    assert trace["incumbent"]["not_worse_than_incumbent"]
    assert objective_components(f,y[None,:],75.,s,bands)["value"]<=objective_components(f,y[None,:],75.,s,prior)["value"]+1e-9


@pytest.mark.parametrize("prior,reason",[
    ([band(q=12.)],"incumbent_parameter_bounds"),
    ([band(fc=90.5)],"incumbent_entry_steps"),
    ([band(gain=1.)],"incumbent_parameter_bounds"),
    ([band(),band(),band(),band()],"incumbent_band_budget"),
    ([band(q=float("nan"))],"incumbent_nonfinite_parameter"),
])
def test_incompatible_incumbent_rejected_with_reason_without_blocking_fit(prior,reason):
    f=a._log_grid(30.,200.)
    bands,_,trace=fit(f,np.full(len(f),73.),incumbent=prior)
    assert bands==[]
    assert trace["incumbent"]["reason_code"]==reason
    assert not trace["incumbent"]["accepted"]
    json.dumps(trace,allow_nan=False)


def test_curve_equivalent_merge_fits_complete_response_and_honors_validator():
    original=[band(100.,-1.5,4.),band(100.,-1.5,4.)]
    merged,records=merge_curve_equivalent(original,settings())
    assert len(merged)==1 and records[0]["accepted"]
    f=np.unique(np.r_[np.geomspace(1.,23999.,30000),100.])
    delta=a.filter_response(f,merged)-a.filter_response(f,original)
    assert np.max(np.abs(delta))<=.05+1e-6
    assert np.sqrt(np.mean(delta**2))<=.01+1e-6
    assert original==[band(100.,-1.5,4.),band(100.,-1.5,4.)]
    rejected,info=merge_curve_equivalent(original,settings(),validator=lambda _:False)
    assert rejected==original
    assert info[0]["reason_code"]=="protection_or_device_constraint"


def test_stacked_depth_larger_than_single_band_limit_is_retained_and_explained():
    original=[band(91.,-3.3,9.),band(91.,-3.3,9.)]
    merged,records=merge_curve_equivalent(original,settings(max_cut=6.))
    assert merged==original
    assert records[0]["reason_code"]=="single_band_depth_limit"
    assert records[0]["required_peak_gain_db"]==pytest.approx(6.6,abs=1e-6)
    assert records[0]["single_band_gain_limit_db"]==6.


def test_frozen_rows_not_involved_in_merge_or_incumbent_refinement():
    f=a._log_grid(30.,200.)
    frozen=[band(90.,-1.23,4.56)]
    original=deepcopy(frozen)
    y=75.+a.filter_response(f,[band(90.,4.,4.56)])
    bands,_,trace=fit(f,y,settings(bands=2),frozen=frozen)
    assert trace["final_filters"][:1]==original
    assert frozen==original
    assert len(bands)<=1


def test_frozen_violation_names_record_metric_and_numeric_limit():
    f=a._log_grid(30.,200.)
    frozen=[band(90.,-3.,4.)]
    before=deepcopy(frozen)
    with pytest.raises(ValueError) as error:
        fit(f,np.full(len(f),76.),settings(added_deficit_max_limit_db=.15),
            frozen=frozen,raw_names=[("L","P0","take A")])
    message=str(error.value)
    assert "take A" in message and "新增低處最大值" in message
    assert "上限 0.150 dB" in message
    assert frozen==before


def test_raw_repeat_disagreement_does_not_create_boost_support():
    f=a._log_grid(30.,300.)
    raw=np.array([np.full(len(f),71.),np.full(len(f),75.)])
    y=raw.mean(axis=0,keepdims=True)
    s=settings(objective_mode="shape",allow_boost=True,allow_extended=True,f_max=300.)
    bands,_,_=fit(f,y,s,raw_ys=raw,boost_allowed=True)
    assert not any(b["gain"]>0 for b in bands)


def test_missing_guard_policy_retains_legacy_result_and_does_not_enforce_new_caps():
    f=a._log_grid(30.,200.)
    y=73.+7*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    s=settings(added_deficit_max_limit_db=0.)
    s.pop("guard_policy")
    first,_,info=fit(f,y,s)
    explicit,_,_=fit(f,y,{**s,"guard_policy":"legacy"})
    assert first==explicit
    assert info["guard_policy"]=="legacy"
    assert info["protection"] is None
    assert first


def test_cancellation_during_new_search_does_not_return_partial_result():
    f=a._log_grid(30.,200.)
    calls=[0]
    def cancel():
        calls[0]+=1
        return calls[0]>20
    with pytest.raises(InterruptedError):
        fit(f,np.full(len(f),78.),cancel=cancel)


def test_v5_keeps_using_available_bands_for_separate_supported_features():
    f=a._log_grid(30.,500.)
    y=73.+sum(6*np.exp(-.5*(np.log2(f/fc)/.065)**2) for fc in [48.,75.,120.,190.,300.,450.])
    s=settings(f_max=500.,allow_extended=True,bands=8,min_q=4.,max_q=15.,
               max_total_cut=12.,low_cut_limit_db=.8)
    bands,_,trace=fit(f,y,s)
    assert len(bands)>3
    assert trace["protection"]["feasible"]
    assert trace["final_cost"]["peak_mse"]<objective_components(f,y[None,:],75.,s,[])["peak_mse"]*.5


def test_generated_shape_cost_support_and_manual_evaluation_agree_on_raw_repeats():
    from test_analysis import measurement, peak
    ms=[measurement("L",[peak(fc=120.,gain=-2.5+offset,q=1.4)],position=pos,suffix=suffix)
        for pos in ("P0","P-10","P+10") for suffix,offset in (("A",-.1),("B",.1))]
    s=settings(objective_mode="shape",allow_boost=True,target_level=75.,bands=2)
    generated=a.generate_peq(ms,s)
    manual=a.evaluate_peq(ms,generated["settings"],generated["filters"],generated["target_level"])
    assert any(b["gain"]>0 for b in generated["filters"]["L"])
    assert manual["preamp_db"]==generated["preamp_db"]
    assert manual["metrics"]["predicted_rmse_db"]==pytest.approx(generated["metrics"]["predicted_rmse_db"],abs=1e-10)
    actual=generated["objective"]["channels"]["L"]["after"]
    assert manual["explanation"]["bands"][0]["native_cost"]["with_band"]==pytest.approx(actual)
    stage=generated["allocation"]["channels"]["L"]["stages"][-1]["search"]
    assert stage["final_cost"]==pytest.approx(actual)


def test_shared_biquad_stays_accurate_at_high_rate_low_center_dc_and_nyquist():
    from roompilot.biquad import responses
    from roompilot.solver import _responses
    fs=768000.
    filters=[band(10.,-6.,20.)]
    f=np.array([[0.,10.],[200.,fs/2]])
    response=a.filter_response(f,filters,fs)
    assert response.shape==f.shape
    assert response[0,0]==0.
    assert response[1,1]==pytest.approx(0.,abs=1e-12)
    assert response[0,1]==pytest.approx(-6.,abs=1e-12)
    assert a.filter_response(10.,filters,fs)==pytest.approx(-6.,abs=1e-12)
    np.testing.assert_array_equal(response,responses(f,filters,fs).sum(axis=0))
    np.testing.assert_array_equal(_responses(f.ravel(),filters,fs).sum(axis=0),response.ravel())
    inverse=[band(10.,6.,20.)]
    np.testing.assert_allclose(response+a.filter_response(f,inverse,fs),0.,atol=1e-12,rtol=0)


def test_high_sample_rate_boundary_feasible_in_search_also_passes_manual_validation():
    from roompilot.biquad import responses
    fs=768000.
    grid=a._log_grid(10.,200.)
    filters=[band(10.,-6.,20.)]
    eq=responses(grid,filters,fs).sum(axis=0)
    # A whole set of bins lands exactly on the 1 dB new-deficit boundary.
    # Unstable complex-coefficient subtraction differed by 2.65e-6 dB here.
    y=75.+np.maximum(-eq-1.,0.)
    ms=[dict(id="L",name="L high rate",channel="L",position="P0",role="baseline",
             frequency=grid.tolist(),spl=y.tolist(),metadata=dict(cal_status="loaded",clipping=False))]
    s=settings(f_min=10.,sample_rate=fs,max_q=20.,target_level=75.,
               added_deficit_max_limit_db=1.,added_deficit_rms_limit_db=3.,
               local_cut_limit_db=9.,bass_mean_cut_limit_db=9.,low_cut_limit_db=6.,outside_cut_limit_db=9.)
    assert np.min(ProtectionModel(grid,y[None,:],75.,s).slacks(eq))>=-1e-12
    manual=a.evaluate_peq(ms,s,{"L":filters},75.)
    assert manual["filters"]["L"]==filters
    assert manual["metrics"]["protection"]["channels"]["L"]["worst_added_deficit_max_db"]<=1.+1e-12


def test_edge_peak_cannot_hide_large_filter_tail_beyond_selected_range():
    from roompilot.protection import CurveFootprint
    f=a._log_grid(30.,200.)
    y=75.+a.filter_response(f,[band(200.,6.,6.)])
    s=settings(bands=2,outside_cut_limit_db=3.,local_cut_limit_db=6.)
    previous=[band(200.,-6.,6.)]
    protected,_,trace=fit(f,y,s,incumbent=previous)
    eq=a.filter_response(np.geomspace(200.00001,1000.,5000),protected)
    assert eq.min()>=-3.-1e-7
    assert not trace["incumbent"]["accepted"]
    assert trace["incumbent"]["reason_code"]=="incumbent_current_protection"
    assert trace["curve_footprint"]["feasible"]
    domain=CurveFootprint(s)
    assert np.min(domain.slacks(a.filter_response(domain.grid,protected)))>=-1e-7
    # A user may explicitly permit a stronger tail; it is never silently
    # grandfathered from the old candidate or inferred from missing SPL data.
    permitted,_,loose=fit(f,y,{**s,"outside_cut_limit_db":9.},incumbent=previous)
    assert loose["incumbent"]["accepted"]
    assert a.filter_response([200.00001],permitted)[0]<-5.5
    assert loose["final_cost"]["value"]<trace["final_cost"]["value"]


def test_frozen_outside_tail_failure_reports_curve_scope_and_keeps_original():
    f=a._log_grid(30.,200.)
    frozen=[band(200.,-6.,6.)]
    saved=deepcopy(frozen)
    y=75.+a.filter_response(f,[band(200.,6.,6.)])
    with pytest.raises(ValueError) as error:
        fit(f,y,settings(outside_cut_limit_db=3.),frozen=frozen)
    message=str(error.value)
    assert "完整 EQ" in message and "3.000" in message
    assert "不代表已量到該處 SPL" in message
    assert frozen==saved
