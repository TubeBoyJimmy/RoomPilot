import copy
import json
import zipfile
from pathlib import Path

import pytest

from roompilot.storage import ProjectStore


def sample_measurement():
    return {"id": "one", "name": "L P0", "channel": "L", "position": "P0", "role": "baseline", "frequency": [20., 30., 40.], "spl": [70., 72., 70.], "metadata": {"cal_status": "loaded", "cal_name": "mic.txt"}}


def test_snapshot_and_round_trip(tmp_path):
    s = ProjectStore(tmp_path / "local")
    p = s.create("書房", "固定耳高")
    p["measurements"].append(sample_measurement())
    s.snapshot_baseline(p, ["one"], [], False)
    p["measurements"][0]["position"] = "P+10"
    p["settings"]["volume_note"] = "-30 dB"
    s.save(p)
    assert s.load(p["id"])["baselines"][0]["measurements"][0]["position"] == "P0"
    source = tmp_path / "source.mdat"
    source.write_bytes(b"original-source")
    original = source.read_bytes()
    s.preserve_source(p, source)
    s.save(p)
    bundle = tmp_path / "test.roompilot"
    s.export_bundle(p, bundle)
    other = ProjectStore(tmp_path / "other")
    restored = other.import_bundle(bundle)
    assert restored["id"] != p["id"]
    assert restored["baselines"] == p["baselines"]
    assert source.read_bytes() == original
    assert (other.assets_dir / restored["id"] / restored["sources"][0]["relative_path"]).read_bytes() == original
    assert len(s.list_projects()) == 1
    s.close()
    other.close()


def test_bundle_rejects_traversal_before_writes(tmp_path):
    s = ProjectStore(tmp_path / "local")
    p = s.create("room")
    bundle = tmp_path / "unsafe.roompilot"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("project.json", json.dumps(p))
        z.writestr("manifest.json", json.dumps({"format": "RoomPilot", "schema_version": 1}))
        z.writestr("../escape.txt", "unsafe")
    with pytest.raises(ValueError, match="路徑"):
        s.import_bundle(bundle)
    assert not (tmp_path / "escape.txt").exists()
    assert len(s.list_projects()) == 1
    s.close()


def test_missing_source_refuses_incomplete_export(tmp_path):
    s = ProjectStore(tmp_path / "local")
    p = s.create("room")
    source = tmp_path / "test.txt"
    source.write_text("10 70\n20 71")
    record = s.preserve_source(p, source)
    (s.assets_dir / p["id"] / record["relative_path"]).unlink()
    with pytest.raises(ValueError, match="遺失"):
        s.export_bundle(p, tmp_path / "out.roompilot")
    assert not (tmp_path / "out.roompilot").exists()
    s.close()


def complete_project(store):
    project = store.create("完整專案")
    project["measurements"].append(sample_measurement())
    store.snapshot_baseline(project, ["one"], [], True)
    peq = {"id": "peq-one", "name": "PEQ v1", "status": "applied", "baseline_version": 1,
           "settings": {}, "filters": {"Shared": [{"frequency": 30., "gain": -3., "q": 2., "enabled": True, "type": "PK"}]},
           "target_level": 70., "preamp_db": 0., "replaces_peq_id": "", "curves": [],
           "verification": {}, "verification_history": []}
    project["peqs"].append(peq)
    project["current_applied_peq_id"] = "peq-one"
    verification = {**sample_measurement(), "id": "verified-one", "role": "verification", "applied_peq_id": "peq-one"}
    project["measurements"].append(verification)
    return project


def write_bundle(path, project):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.json", json.dumps(project))
        archive.writestr("manifest.json", json.dumps({"format": "RoomPilot", "schema_version": 1}))


def test_empty_and_complete_project_graphs_are_accepted(tmp_path):
    store = ProjectStore(tmp_path / "db")
    empty = store.create("尚未量測")
    ProjectStore._validate_project(empty)
    full = complete_project(store)
    ProjectStore._validate_project(full)
    # Live annotations can change while immutable baseline history remains valid.
    full["measurements"][0]["position"] = "P+10"
    ProjectStore._validate_project(full)
    bundle = tmp_path / "full.roompilot"
    write_bundle(bundle, full)
    restored = store.import_bundle(bundle)
    assert restored["baseline_version"] == 1
    assert restored["current_applied_peq_id"] == "peq-one"
    assert restored["baselines"][0]["measurements"][0]["position"] == "P0"
    store.close()


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(baseline_version=2),
    lambda p: p.update(baselines=[]),
    lambda p: p.update(baseline_ids=["not-here"]),
    lambda p: p["measurements"].append(copy.deepcopy(p["measurements"][0])),
    lambda p: p["measurements"][0].update(frequency=[20., 20., 40.]),
    lambda p: p["measurements"][0].update(frequency=[0., 30., 40.]),
    lambda p: p["measurements"][0].update(spl=[70., float("nan"), 70.]),
    lambda p: p["measurements"][0].update(spl=["70", 72., 70.]),
    lambda p: p["measurements"][0].update(phase=[0.]),
    lambda p: p["baselines"][0]["measurements"][0].update(frequency=[20., 40., 30.]),
    lambda p: p["baselines"][0].update(measurement_ids=["verified-one"]),
    lambda p: p["baselines"].append(copy.deepcopy(p["baselines"][0])),
    lambda p: p["baselines"][0].update(version=3),
    lambda p: p["baselines"][0]["measurements"][0].update(role="verification", applied_peq_id="peq-one"),
    lambda p: p["peqs"][0].update(baseline_version=123),
    lambda p: p["peqs"][0].update(baseline_version=[]),
    lambda p: p["peqs"].append(copy.deepcopy(p["peqs"][0])),
    lambda p: p["peqs"][0].update(replaces_peq_id="not-here"),
    lambda p: p["peqs"][0].update(replaces_peq_id="peq-one"),
    lambda p: p["peqs"][0]["filters"]["Shared"][0].update(q=0),
    lambda p: p["peqs"][0].update(curves=[{"frequency": [20, 30], "spl": [float("inf"), 70]}]),
    lambda p: p["peqs"][0].update(verification={"measurement_ids": ["not-here"]}),
    lambda p: p["measurements"][1].update(applied_peq_id="not-here"),
    lambda p: p.update(current_applied_peq_id="not-here"),
    lambda p: p["peqs"][0].update(status="draft"),
    lambda p: p["measurements"][0].update(source_sha256="a" * 64),
])
def test_invalid_archive_graph_is_rejected_before_any_project_write(tmp_path, mutation):
    store = ProjectStore(tmp_path / "db")
    project = complete_project(store)
    before = store.list_projects()
    before_assets = list(store.assets_dir.rglob("*"))
    mutation(project)
    bundle = tmp_path / "invalid.roompilot"
    write_bundle(bundle, project)
    with pytest.raises(ValueError):
        store.import_bundle(bundle)
    assert store.list_projects() == before
    assert list(store.assets_dir.rglob("*")) == before_assets
    store.close()


@pytest.mark.parametrize("source_path", ["../elsewhere", "sources/../elsewhere", "sources\\elsewhere", "sources//same.txt", "C:/elsewhere", "sources"])
def test_invalid_source_records_fail_validation_before_copy(tmp_path, source_path):
    store = ProjectStore(tmp_path / "db")
    project = store.create("source paths")
    project["sources"] = [{"name": "file.txt", "relative_path": source_path, "sha256": "a" * 64, "bytes": 0}]
    with pytest.raises(ValueError, match="路徑"):
        ProjectStore._validate_project(project)
    store.close()


def test_source_changed_since_parsing_is_not_registered(tmp_path):
    store = ProjectStore(tmp_path / "data")
    project = store.create("source snapshot")
    path = tmp_path / "measurement.txt"
    path.write_text("changed contents", encoding="utf-8")
    with pytest.raises(ValueError, match="已變更"):
        store.preserve_source(project, path, expected_sha256="0" * 64)
    assert project["sources"] == []
    assert list(store.assets_dir.rglob("*")) == []
    store.close()
