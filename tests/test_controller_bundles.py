"""A version groups strategies while all physical/acoustic references stay exact."""
import copy
import json
import time

import pytest

from roompilot.analysis import evaluate_peq
from roompilot.storage import ProjectStore
from test_controller import app, bridge, load_baseline, measurements, await_work


SETTINGS = {"bands": 2, "independent": True, "target_level": 75, "mode": "standard", "objective_mode": "peak"}


def comparison_for(b):
    candidates = []
    for i, gain in enumerate((-3., -4., -5.)):
        filters = {ch: [{"frequency": 90., "gain": gain, "q": 3., "enabled": True, "type": "PK"}] for ch in ("L", "R")}
        # Controller tests deliberately inject deterministic solver results.
        # Numeric optimizer correctness is tested independently.
        result = evaluate_peq(b._baseline()["measurements"], SETTINGS, filters, 75.)
        candidates.append({"key": "strategy_" + str(i), "title": ("保護低處", "均衡修正", "充分削峰")[i],
                           "result": result, "within_limit": True, "low_cut_limit_db": (i+1)/3,
                           "metrics": {"residual_peak_rms_db": 3-i, "worst_below_target_cut_rms_db": i/4},
                           "active_band_count": 2, "notes": []})
    return {"schema_version": 2, "candidates": candidates, "selected_key": "strategy_1", "recommended_key": "strategy_0",
            "low_cut_limit_db": 1., "target_level": 75., "has_feasible_candidate": True, "notes": []}


def inject_pool(b, monkeypatch, *, transform=None):
    pool = comparison_for(b)
    if transform:
        transform(pool)
    def generate(*args, **kwargs):
        return copy.deepcopy(pool)
    monkeypatch.setattr("roompilot.comparison.generate_peq_candidates", generate)
    return pool


def generate_group(b, app, monkeypatch):
    inject_pool(b, monkeypatch)
    b.generatePeqCandidates(json.dumps(SETTINGS), 1.)
    await_work(b, app)
    return b._group_members(b._peq())


def wait_finished(b, app):
    deadline = time.monotonic() + 10
    while b.state["busy"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not b.state["busy"]


def test_generation_auto_saves_one_version_with_three_exact_variants_and_dedupes(bridge, app, monkeypatch, tmp_path):
    b = bridge
    load_baseline(b)
    members = generate_group(b, app, monkeypatch)
    assert len(members) == 3 and len(b.project["peqs"]) == 3
    assert len(b.state["peqs"]) == 1 and b.state["peqs"][0]["name"] == "PEQ v1"
    assert len(b.state["peq_variants"]) == len(b.state["selected_peq_variants"]) == 3
    assert b._peq()["variant_key"] == "strategy_1"
    assert not b.state["candidate_preview"] and not b.state["candidate_comparison"].get("candidates")
    before = copy.deepcopy(b.project)
    ids = [m["id"] for m in members]
    for member in members:
        b.selectPeq(member["id"])
        assert b.state["peqs"][0]["id"] == member["id"]
        assert b.state["selected_peq"]["filters"] == member["filters"]
    assert b.project == before and b.store.load(b.project["id"]) == before
    generate_group(b, app, monkeypatch)
    assert [q["id"] for q in b.project["peqs"]] == ids
    bundle = tmp_path / "strategies.roompilot"
    b.store.export_bundle(b.project, bundle)
    imported = b.store.import_bundle(bundle)
    b.selectProject(imported["id"])
    assert len(b.state["peqs"]) == 1 and len(b.state["selected_peq_variants"]) == 3
    assert b._peq()["variant_key"] == "strategy_1"
    assert [q["id"] for q in imported["peqs"]] == ids


def test_applied_export_verification_and_extension_keep_exact_variant_identity(bridge, app, monkeypatch, tmp_path):
    b = bridge
    load_baseline(b)
    members = generate_group(b, app, monkeypatch)
    first, second = members[:2]
    b.markApplied(first["id"])
    b.selectPeq(second["id"])
    assert b.project["current_applied_peq_id"] == first["id"]
    assert b._peq()["status"] == "draft"
    b.export_peq_to(first["id"], tmp_path / "first.txt")
    b.export_peq_to(second["id"], tmp_path / "second.csv", "csv")
    assert "PEQ v1 · 保護低處" in (tmp_path / "first.txt").read_text(encoding="utf-8-sig")
    assert "Gain -3.00" in (tmp_path / "first.txt").read_text(encoding="utf-8-sig")
    assert "PEQ v1 · 均衡修正" in (tmp_path / "second.csv").read_text(encoding="utf-8-sig")
    for measurement in measurements():
        measurement.update(id=measurement["id"]+"-verification", role="verification", applied_peq_id=first["id"])
        b.project["measurements"].append(measurement)
    b.compareVerification(first["id"])
    assert b._peq(first["id"])["verification_history"]
    assert b._peq(second["id"])["verification_history"] == []
    b.selectPeq(second["id"])
    seen = {}
    pool = comparison_for(b)
    def extend(*args, **kwargs):
        seen["source"] = kwargs["base_peq"]["id"]
        return pool
    monkeypatch.setattr("roompilot.comparison.generate_peq_candidates", extend)
    b.generatePeqCandidates(json.dumps({**SETTINGS, "strategy": "extend_existing", "f_max": 500}), 1.)
    await_work(b, app)
    assert seen["source"] == second["id"]
    assert all(q["extension_source_id"] == second["id"] and q["replaces_peq_id"] == first["id"] for q in b._group_members(b._peq()))
    assert len(b.state["peqs"]) == 2 and b._peq()["name"] == "PEQ v2"


def test_delete_restore_whole_group_preserves_references_and_edit_creates_standalone(bridge, app, monkeypatch):
    b = bridge
    load_baseline(b)
    members = generate_group(b, app, monkeypatch)
    ids = [q["id"] for q in members]
    b.markApplied(ids[0])
    b.selectPeq(ids[1])
    original = copy.deepcopy(b.project["peqs"])
    b.deletePeq(ids[2])
    assert not b.state["peqs"] and len(b.state["deleted_peqs"]) == 1
    assert all(q.get("deleted_at") for q in b.project["peqs"])
    assert b.project["current_applied_peq_id"] == ids[0]
    ProjectStore._validate_project(b.project)
    b.restorePeq(ids[2])
    assert b.project["peqs"] == original
    assert len(b.state["selected_peq_variants"]) == 3
    band = b._peq()["filters"]["L"][0]
    b.editFilter("L", 0, band["frequency"], band["gain"]+.2, band["q"], True)
    edited = b._peq()
    assert edited["name"] == "PEQ v2" and edited["replaces_peq_id"] == ids[2]
    assert "group_id" not in edited and "candidate_selection" not in edited
    assert b.project["peqs"][:3] == original
    assert len(b.state["peqs"]) == 2 and not b.state["selected_peq_variants"]


def test_version_number_reserves_deleted_group_and_legacy_maximum(bridge, app, monkeypatch):
    b = bridge
    load_baseline(b)
    generate_group(b, app, monkeypatch)
    b.deletePeq(b._peq()["id"])
    legacy = copy.deepcopy(b.project["peqs"][0])
    for field in ("group_id", "group_name", "group_fingerprint", "group_default_variant_key", "variant_key", "variant_title", "variant_order", "candidate_selection", "deleted_at"):
        legacy.pop(field, None)
    legacy.update(id="legacy-max-version", name="PEQ v7")
    b.project["peqs"].append(legacy)
    b.store.save(b.project)
    generate_group(b, app, monkeypatch)
    assert b._peq()["name"] == "PEQ v8"


def test_reopen_prefers_current_applied_variant_without_persisting_browsing(bridge, app, monkeypatch):
    b = bridge
    load_baseline(b)
    members = generate_group(b, app, monkeypatch)
    b.markApplied(members[0]["id"])
    b.selectPeq(members[2]["id"])
    b.selectProject(b.project["id"])
    assert b._peq()["id"] == members[0]["id"]
    assert b.project["current_applied_peq_id"] == members[0]["id"]
    assert members[0]["group_default_variant_key"] == "strategy_1"


def test_real_schema2_solver_auto_save_contract(bridge, app):
    b = bridge
    load_baseline(b)
    b.generatePeqCandidates(json.dumps(SETTINGS), 1.)
    await_work(b, app)
    assert len(b.state["peqs"]) == 1 and len(b.project["peqs"]) == 3
    members = b._group_members(b._peq())
    assert [q["settings"]["low_cut_limit_db"] for q in members] == [.35, .65, 1.]
    assert all(q["settings"]["objective_mode"] == "peak" for q in members)
    assert b._peq()["variant_order"] == 0
    ProjectStore._validate_project(b.project)


def test_new_budget_or_changed_saved_parameters_do_not_incorrectly_dedupe(bridge, app, monkeypatch):
    b = bridge
    load_baseline(b)
    generate_group(b, app, monkeypatch)
    def change_budget(pool):
        pool["low_cut_limit_db"] = 2.
        for candidate in pool["candidates"]:
            candidate["low_cut_limit_db"] *= 2
    inject_pool(b, monkeypatch, transform=change_budget)
    b.generatePeqCandidates(json.dumps(SETTINGS), 2.)
    await_work(b, app)
    assert len(b.state["peqs"]) == 2
    # A copied/imported historical hash must not override actual filter values.
    b.project["peqs"][0]["filters"]["L"][0]["gain"] = -1.
    generate_group(b, app, monkeypatch)
    assert len(b.state["peqs"]) == 3


@pytest.mark.parametrize("failure", ["missing", "infeasible", "duplicate", "database", "cancel", "stale"])
def test_failed_cancelled_stale_or_partial_generation_saves_nothing(bridge, app, monkeypatch, failure):
    b = bridge
    load_baseline(b)
    before = copy.deepcopy(b.project)
    pool = comparison_for(b)
    if failure == "missing":
        pool["candidates"].pop()
    if failure == "infeasible":
        pool["candidates"][0]["within_limit"] = False
    if failure == "duplicate":
        pool["candidates"][2]["key"] = pool["candidates"][0]["key"]
    def generation(*args, **kwargs):
        time.sleep(.025)
        return copy.deepcopy(pool)
    monkeypatch.setattr("roompilot.comparison.generate_peq_candidates", generation)
    if failure == "database":
        b.store.connection.execute("CREATE TRIGGER refuse_save BEFORE UPDATE ON projects BEGIN SELECT RAISE(ABORT, 'disk simulated'); END")
    b.generatePeqCandidates(json.dumps(SETTINGS), 1.)
    if failure == "cancel":
        b.cancelWork()
    if failure == "stale":
        b._baseline()["measurements"][0]["spl"][0] += .1
    wait_finished(b, app)
    assert b.project["peqs"] == []
    assert b.store.load(b.project["id"]) == before
    if failure != "stale":
        assert b.project == before
    assert not b.state["candidate_preview"] and not b.state["candidate_comparison"].get("candidates")
    assert b.state["message_kind"] == ("info" if failure == "cancel" else "error")
    assert not b.store.connection.in_transaction


@pytest.mark.parametrize("broken", ["missing", "duplicate_key", "order", "baseline", "target", "name", "partial_deleted", "missing_metadata", "comparison"])
def test_archive_rejects_inconsistent_group_before_import(bridge, app, monkeypatch, tmp_path, broken):
    b = bridge
    load_baseline(b)
    generate_group(b, app, monkeypatch)
    project = copy.deepcopy(b.project)
    members = project["peqs"]
    if broken == "missing": members.pop()
    elif broken == "duplicate_key": members[1]["variant_key"] = members[0]["variant_key"]
    elif broken == "order": members[1]["variant_order"] = 0
    elif broken == "baseline": members[1]["baseline_version"] = 2
    elif broken == "target": members[1]["target_level"] += 1
    elif broken == "name": members[1]["name"] = "PEQ v9"
    elif broken == "partial_deleted": members[1]["deleted_at"] = "2026-09-06"
    elif broken == "missing_metadata": members[1].pop("variant_title")
    elif broken == "comparison": members[1]["candidate_selection"]["candidates"].pop()
    with pytest.raises(ValueError):
        ProjectStore._validate_project(project)
    archive = tmp_path / "malformed.roompilot"
    b.store.export_bundle(project, archive)
    before = b.store.list_projects()
    with pytest.raises(ValueError):
        b.store.import_bundle(archive)
    assert b.store.list_projects() == before


def test_archive_accepts_128_bands_and_rejects_129(bridge, app, monkeypatch):
    b = bridge
    load_baseline(b)
    generate_group(b, app, monkeypatch)
    project = copy.deepcopy(b.project)
    member = project["peqs"][0]
    band = member["filters"]["L"][0]
    member["filters"]["L"] = [copy.deepcopy(band) for _ in range(128)]
    ProjectStore._validate_project(project)
    member["filters"]["L"].append(copy.deepcopy(band))
    with pytest.raises(ValueError, match="Band"):
        ProjectStore._validate_project(project)
