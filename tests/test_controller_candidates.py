"""Durable/temporary boundaries for the user-visible candidate workflow."""
import copy
import json

import pytest

from test_controller import app, bridge, load_baseline, await_work


SETTINGS = dict(bands=2, independent=True, target_level=75, mode="standard")


def generate(b, app, limit=1.):
    b.generatePeqCandidates(json.dumps(SETTINGS), limit)
    await_work(b, app)
    assert b.state["candidate_comparison"]["candidates"]
    return b.state["candidate_comparison"]


def test_preview_is_transient_save_is_idempotent_and_comparison_roundtrips(bridge, app, tmp_path):
    b=bridge
    load_baseline(b)
    before=b.store.load(b.project["id"])
    comparison=generate(b,app)
    assert b.project["peqs"]==[]
    assert b.store.load(b.project["id"])==before
    key=comparison["selected_key"]
    b.selectPeqCandidate(key)
    assert b.state["candidate_preview"]["is_draft"]
    assert b.state["candidate_preview"]["id"].startswith("draft:")
    assert not b.state["selected_peq"]
    b.savePeqCandidate(key)
    assert len(b.project["peqs"])==1
    q=copy.deepcopy(b._peq())
    assert q["candidate_selection"]["selected_key"]==key
    assert q["explanation"]["positions"]
    assert not b.state["candidate_preview"]
    b.savePeqCandidate(key)
    assert len(b.project["peqs"])==1 and b._peq()["id"]==q["id"]
    path=tmp_path/'candidates.roompilot'
    b.store.export_bundle(b.project,path)
    copied=b.store.import_bundle(path)
    assert copied["peqs"][0]["candidate_selection"]==q["candidate_selection"]
    b.selectProject(copied["id"])
    assert not b.state["candidate_comparison"].get("candidates")
    assert b.state["selected_peq"]["candidate_selection"]["historical"]


def test_cancel_regenerate_discards_previous_candidates_without_saving(bridge, app):
    load_baseline(bridge)
    generate(bridge,app)
    bridge.generatePeqCandidates(json.dumps(SETTINGS),1.)
    bridge.cancelWork()
    await_work(bridge,app)
    assert not bridge.state["candidate_comparison"].get("candidates")
    assert not bridge.state["candidate_preview"]
    assert bridge.project["peqs"]==[]


def test_draft_cannot_cross_project_or_baseline_or_mutated_snapshot(bridge, app):
    b=bridge
    load_baseline(b)
    first_id=b.project["id"]
    key=generate(b,app)["selected_key"]
    b.createProject("another","")
    b.savePeqCandidate(key)
    assert not b.project["peqs"] and b.state["message_kind"]=="error"
    b.selectProject(first_id)
    key=generate(b,app)["selected_key"]
    ids=json.dumps([m["id"] for m in b.project["measurements"]])
    b.setBaseline(ids,True)
    assert not b.state["candidate_preview"]
    b.savePeqCandidate(key)
    assert not b.project["peqs"]
    key=generate(b,app)["selected_key"]
    b._baseline()["measurements"][0]["spl"][100]+=1
    b.savePeqCandidate(key)
    assert not b.project["peqs"] and "Baseline" in b.state["message"]


def test_candidate_does_not_allow_editing_hidden_saved_version_and_manual_has_fresh_explanation(bridge, app):
    b=bridge
    load_baseline(b)
    first=generate(b,app)
    b.savePeqCandidate(first["selected_key"])
    original=copy.deepcopy(b._peq())
    band=original["filters"]["L"][0]
    b.selectPeqCandidate(first["selected_key"])
    b.editFilter("L",0,band["frequency"],band["gain"]+.3,band["q"],True)
    assert len(b.project["peqs"])==1 and "預覽" in b.state["message"]
    b.selectPeq(original["id"])
    b.editFilter("L",0,band["frequency"],band["gain"]+.3,band["q"],True)
    edited=b._peq()
    assert len(b.project["peqs"])==2
    assert "candidate_selection" not in edited
    assert edited["explanation"]!=original["explanation"]
    assert not any(row["frozen"] for row in edited["explanation"]["bands"])
    assert b.project["peqs"][0]==original


def test_legacy_posthoc_explanation_does_not_rewrite_revision(bridge, app):
    b=bridge
    load_baseline(b)
    b.generatePeq(json.dumps(SETTINGS))
    await_work(b,app)
    q=b._peq()
    q.pop("explanation",None)
    q["algorithm_version"]="roompilot-peq-2.0"
    q["settings"].pop("overshoot_scale",None)
    b.store.save(b.project)
    before=copy.deepcopy(b.store.load(b.project["id"]))
    b._explanation_cache={}
    b._refresh()
    assert b.state["selected_peq"]["explanation"]["post_hoc"]
    assert "舊版補充說明" in b.state["selected_peq"]["explanation"]["notes"][0]
    assert b.project==before and b.store.load(b.project["id"])==before


def test_noncompliant_candidate_cannot_be_saved(bridge,app):
    b=bridge
    load_baseline(b)
    # The main synthetic peak now rises out of a lower surrounding response.
    for m in b._baseline()["measurements"]:
        m["spl"]=[v-3 for v in m["spl"]]
    comparison=generate(b,app,limit=0.)
    active=[c for c in comparison["candidates"] if any(c["result"]["filters"].values())]
    assert active
    assert all(not c["within_limit"] for c in active)
    b.savePeqCandidate(active[0]["key"])
    assert b.project["peqs"]==[] and "容許量" in b.state["message"]


def test_save_later_candidate_preserves_auto_target_and_replaces_current_applied(bridge,app):
    b=bridge
    load_baseline(b)
    b.generatePeqCandidates(json.dumps({k:v for k,v in SETTINGS.items() if k!='target_level'}),1.)
    await_work(b,app)
    candidates=b.state["candidate_comparison"]["candidates"]
    assert len(candidates)>1
    b.savePeqCandidate(candidates[0]["key"])
    first=b._peq()["id"]
    b.markApplied(first)
    b.selectPeqCandidate(candidates[1]["key"])
    b.savePeqCandidate(candidates[1]["key"])
    q=b._peq()
    assert q["replaces_peq_id"]==first
    assert q["settings"]["target_level"] is None
    assert b.project["settings"]["peq_settings"]["target_level"] is None
    assert q["target_reference"]["mode"]=="fixed_reference"
