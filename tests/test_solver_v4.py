"""Numerical policy and constraint regressions; synthetic data, no user files."""
from copy import deepcopy
import json

import numpy as np
import pytest

from roompilot import analysis as a
from roompilot.solver import _responses, fit_strategy, objective_components


def band(fc=70.,gain=6.,q=3.,enabled=True):
    return dict(frequency=fc,gain=gain,q=q,enabled=enabled,type="PK")


def settings(**kw):
    return {**a.DEFAULTS,"objective_mode":"peak","low_cut_limit_db":1.,"bands":6,"max_q":9.,**kw}


def fit(f,y,s=None,**kw):
    y=np.asarray(y)
    if y.ndim==1:
        y=y[None,:]
    return fit_strategy(f,y,75.,s or settings(),kw.pop("boost_allowed",False),kw.pop("cancel",None),kw.pop("progress",None),**kw)


def costs(f,y,s,bs,boost=False):
    y=np.atleast_2d(y)
    return objective_components(f,y,75.,s,bs,boost_allowed=boost)


def test_batch_rbj_agrees_with_existing_forward_math_including_high_sample_rate():
    bs=[band(34.,-5.7,.4),band(112.,-3.2,9.),band(230.,2.2,1.2),band(enabled=False)]
    f=np.unique(np.r_[np.geomspace(10.,22000.,2000),[34.,112.,230.]])
    for fs in (48000.,192000.,768000.):
        actual=_responses(f,bs,fs).sum(axis=0)
        expected=a.filter_response(f,bs,fs)
        np.testing.assert_allclose(actual,expected,atol=1e-6,rtol=0)


def test_literal_peak_cost_uses_raw_measurements_and_conditional_position_limits():
    f=a._log_grid(30.,200.)
    ys=np.array([75.+2*np.sin(np.log(f)*5),75.+np.cos(np.log(f)*5)])
    s=settings()
    bs=[band(90.,-2.,4.)]
    c=objective_components(f,ys,75.,s,bs,np.array([.67,.33]))
    eq=a.filter_response(f,bs)
    expected=sum(w*np.mean(np.maximum(y+eq-75.,0.)**2) for w,y in zip([.67,.33],ys))
    limits=[np.sqrt(np.mean(np.maximum(-eq[y<75.],0.)**2)) for y in ys]
    assert c["value"]==pytest.approx(expected,abs=1e-9)
    assert c["worst_below_target_cut_rms_db"]==pytest.approx(max(limits),abs=1e-9)
    assert c["complexity"]==c["effort"]==c["overshoot"]==0.
    # A saved arbitrary preamp is not part of the acoustic optimization target.
    assert objective_components(f,ys,75.,{**s,"preamp_db":-30},bs,np.array([.67,.33]))==c


def test_no_low_bins_zero_error_tie_does_not_choose_gratuitous_broad_cut():
    f=a._log_grid(30.,200.)
    y=75.+a.filter_response(f,[band()])
    bs,_,info=fit(f,y,settings(bands=3))
    assert len(bs)==1
    assert bs[0]["frequency"]==pytest.approx(70.,abs=1.)
    assert bs[0]["q"]==pytest.approx(3.,abs=.08)
    assert info["final_cost"]["peak_mse"]<1e-5
    assert info["final_cost"]["low_cut_mse"]==0.


def test_new_peak_solver_uses_more_than_three_bands_when_beneficial_and_feasible():
    f=a._log_grid(30.,500.)
    y=73.+sum(6*np.exp(-.5*(np.log2(f/fc)/.065)**2) for fc in [48.,75.,120.,190.,300.,450.])
    s=settings(f_max=500.,allow_extended=True,bands=8,min_q=4.,max_q=15.,max_total_cut=12.,low_cut_limit_db=.8)
    progress=[]
    bs,_,info=fit(f,y,s,progress=progress.append)
    assert len(bs)>3
    assert info["final_cost"]["peak_mse"]<costs(f,y,s,[])["peak_mse"]*.4
    assert info["final_cost"]["worst_below_target_cut_rms_db"]<=.8+1e-7
    assert np.all(np.diff(progress)>=-1e-10) and progress[-1]==1.
    assert any(r["phase"] in ("interleaved_refinement","final_refinement","refine_before_reopen") for r in info["rounds"])
    assert info["rounds"][-1]["phase"]=="final_reopened_add_band" or len(bs)==s["bands"]
    json.dumps(info,allow_nan=False)


def test_raw_lower_budget_is_internal_not_posthoc_and_zero_means_zero():
    f=a._log_grid(30.,200.)
    y=73.+7*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    strict,_,si=fit(f,y,settings(low_cut_limit_db=.2))
    loose,_,li=fit(f,y,settings(low_cut_limit_db=1.))
    assert si["final_cost"]["worst_below_target_cut_rms_db"]<=.2+1e-8
    assert li["final_cost"]["worst_below_target_cut_rms_db"]<=1.+1e-8
    assert li["final_cost"]["peak_mse"]<si["final_cost"]["peak_mse"]
    zero,_,zi=fit(f,y,settings(low_cut_limit_db=0.))
    assert zero==[]
    assert zi["final_cost"]["worst_below_target_cut_rms_db"]==0.


def test_each_position_is_constrained_even_when_its_weight_is_small():
    f=a._log_grid(30.,200.)
    p0=73.+7*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    ys=np.array([p0,p0,73.+np.zeros(len(f))])
    s=settings(low_cut_limit_db=.3)
    bs,_,_=fit(f,ys,s,names=[("L","P0"),("L","P-10"),("L","P+10")])
    eq=a.filter_response(f,bs)
    assert all(np.sqrt(np.mean(np.maximum(-eq[y<75.],0.)**2))<=.3+1e-7 for y in ys)


def test_frozen_filters_are_exact_and_full_eq_not_only_additions_consumes_budget():
    f=a._log_grid(30.,500.)
    y=73.+6*np.exp(-.5*(np.log2(f/90.)/.12)**2)+6*np.exp(-.5*(np.log2(f/330.)/.12)**2)
    frozen=[band(90.,-1.23,4.56),band(160.,-1.,2.,False)]
    original=deepcopy(frozen)
    s=settings(f_max=500.,allow_extended=True,low_cut_limit_db=1.,bands=8,bass_drift_limit_db=.1)
    protected=a._log_grid(30.,200.)
    bs,_,info=fit(f,y,s,frozen=frozen,center_min=201.,protect_grid=protected)
    assert frozen==original
    assert info["final_filters"][:2]==original
    assert all(b["frequency"]>=201. for b in bs)
    assert np.max(np.abs(a.filter_response(protected,bs)))<=.1+1e-7
    assert costs(f,y,s,frozen+bs)["worst_below_target_cut_rms_db"]<=1.+1e-7
    with pytest.raises(ValueError,match="RMS"):
        fit(f,y,{**s,"low_cut_limit_db":0.},frozen=frozen,center_min=201.,protect_grid=protected)


def test_boost_mode_recovers_known_supported_broad_dip_without_shifting_target():
    f=a._log_grid(30.,300.)
    y=75.+a.filter_response(f,[band(130.,-2.,1.5)])
    ys=np.array([y,y,y])
    s=settings(objective_mode="shape",f_max=300.,allow_extended=True,allow_boost=True,max_total_boost=3.,bands=3)
    bs,_,info=fit(f,ys,s,boost_allowed=True,names=[("L","P0"),("L","P-10"),("L","P+10")])
    assert len(bs)==1 and bs[0]["gain"]==pytest.approx(2.,abs=.1)
    assert bs[0]["q"]==pytest.approx(1.5,abs=.1)
    assert info["final_cost"]["value"]<.001
    no_evidence,_,_=fit(f,ys,s,boost_allowed=False)
    assert not any(b["gain"]>0 for b in no_evidence)
    no_preamp,_,_=fit(f,ys,{**s,"supports_preamp":False},boost_allowed=True)
    assert not any(b["gain"]>0 for b in no_preamp)


def test_deep_and_inconsistent_dips_do_not_become_boost_demands():
    f=a._log_grid(30.,300.)
    s=settings(objective_mode="shape",f_max=300.,allow_extended=True,allow_boost=True)
    for ys in (np.full((3,len(f)),65.),np.array([np.full(len(f),73.),np.full(len(f),74.),np.full(len(f),76.)])):
        bs,_,_=fit(f,ys,s,boost_allowed=True)
        assert not any(b["gain"]>0 for b in bs)


def test_dense_total_gain_after_coarse_device_rounding():
    f=a._log_grid(30.,300.)
    y=73.+sum(9*np.exp(-.5*(np.log2(f/fc)/.055)**2) for fc in [82.,94.,109.])
    s=settings(f_max=300.,allow_extended=True,bands=10,max_q=20.,freq_step=3.,q_step=.5,gain_step=.5,max_total_cut=5.,low_cut_limit_db=2.)
    bs,_,_=fit(f,y,s)
    full=np.unique(np.r_[np.geomspace(1.,23999.,40000),[b["frequency"] for b in bs]])
    eq=a.filter_response(full,bs)
    assert eq.min()>=-5.-1e-6
    for b in bs:
        for key,step in (("frequency",3.),("q",.5),("gain",.5)):
            assert b[key]/step==pytest.approx(round(b[key]/step))


def test_more_than_twenty_slots_and_frozen_rows_are_supported():
    f=a._log_grid(30.,200.)
    frozen=[band(35.+i*5,0.,3.,False) for i in range(24)]
    y=75.+a.filter_response(f,[band(90.,2.,3.)])
    bs,_,info=fit(f,y,settings(bands=32),frozen=frozen)
    assert len(bs)>=1
    assert len(info["final_filters"])>24
    assert info["final_filters"][:24]==frozen


def test_more_than_twenty_active_filters_can_use_small_per_band_gain_cap():
    # An intentionally small per-band hardware limit requires stacking more
    # than twenty useful bands. This catches reintroduced fixed-20 loops.
    f=a._log_grid(30.,200.)
    ys=np.full((1,len(f)),78.)
    s=settings(bands=24,max_cut=.1,gain_step=.1,min_q=.4,max_q=.4,mode="standard")
    bs,_,info=fit(f,ys,s)
    assert len(bs)==24
    assert all(b["gain"]==-.1 for b in bs)
    assert info["final_cost"]["peak_mse"]<2.


def test_cancellation_is_checked_within_search_and_never_returns_partial_result():
    f=a._log_grid(30.,200.)
    y=73.+7*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    calls=[0]
    def cancel():
        calls[0]+=1
        return calls[0]>15
    with pytest.raises(InterruptedError):
        fit(f,y,cancel=cancel)


def test_round_costs_reconstruct_actual_policy_on_saved_enterable_filters():
    f=a._log_grid(30.,200.)
    y=73.+7*np.exp(-.5*(np.log2(f/90.)/.12)**2)
    s=settings(bands=3)
    bs,_,info=fit(f,y,s)
    for row in info["rounds"]:
        before=costs(f,y,s,row["before_filters"])
        assert row["before_cost"]==pytest.approx(before)
        if row["candidate_filters"] is not None:
            after=costs(f,y,s,row["candidate_filters"])
            assert row["candidate_cost"]==pytest.approx(after)
            if row["accepted"]:
                assert after["value"]<=before["value"]+1e-9
    assert info["final_cost"]==pytest.approx(costs(f,y,s,bs))
