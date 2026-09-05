"""Optional packaging check; runs only when explicitly requested on the CLI."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import uuid


def check_sample(path):
    from . import __version__
    from .analysis import quality_report
    from .comparison import generate_peq_candidates
    from .importers import import_measurements
    from .storage import ProjectStore, timestamp

    started = time.perf_counter()
    measurements = import_measurements(path)
    selected = [m for m in measurements if m["channel"] in ("L", "R") and m["position"] == "P0"]
    issues = quality_report(selected)
    settings = {"bands": 5, "f_min": 30, "f_max": 200, "independent": True, "objective_mode": "peak", "guard_policy": "v5"}
    comparison = generate_peq_candidates(selected, settings, low_cut_limit_db=1.0)
    assert comparison["schema_version"] == 2 and len(comparison["candidates"]) == 3
    assert len({c["key"] for c in comparison["candidates"]}) == 3
    assert all(c["within_limit"] for c in comparison["candidates"])
    assert all(c["result"]["explanation"]["protection"]["enforced"]
               and all(p["feasible"] for p in c["result"]["explanation"]["protection"]["channels"].values())
               for c in comparison["candidates"])
    assert all(c["result"]["target_level"] == comparison["target_level"]
               for c in comparison["candidates"])
    assert all(c["result"]["explanation"]["schema_version"] == 1
               for c in comparison["candidates"])
    peq = comparison["candidates"][0]["result"]
    with tempfile.TemporaryDirectory(prefix="roompilot-check-") as temporary:
        store = ProjectStore(Path(temporary) / "data")
        try:
            project = store.create("Packaging verification")
            source = store.preserve_source(project, path)
            for measurement in measurements:
                measurement["source_sha256"] = source["sha256"]
            project["measurements"] = measurements
            store.snapshot_baseline(project, [m["id"] for m in selected], issues, True)
            # Use the production archive graph: three immutable PEQ identities
            # share one visible version, each retaining its full parameters and
            # comparison evidence. No GUI or real user project is opened here.
            group_id, created_at = str(uuid.uuid4()), timestamp()
            encoded = json.dumps(comparison, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
            group_fingerprint = hashlib.sha256(encoded).hexdigest()
            selection = {k: copy.deepcopy(v) for k, v in comparison.items() if k != "candidates"}
            selection.update(historical=True, selected_at=created_at, baseline_version=1)
            selection["candidates"] = [{k: copy.deepcopy(v) for k, v in c.items() if k != "result"}
                                       for c in comparison["candidates"]]
            for order, candidate in enumerate(comparison["candidates"]):
                result = copy.deepcopy(candidate["result"])
                result.update(id=str(uuid.uuid4()), name="PEQ v1", group_id=group_id, group_name="PEQ v1",
                              group_fingerprint=group_fingerprint, group_default_variant_key=comparison["selected_key"],
                              variant_key=candidate["key"], variant_title=candidate["title"], variant_order=order,
                              created_at=created_at, status="draft", baseline_version=1, replaces_peq_id="",
                              extension_source_id=None, verification={}, verification_history=[])
                result["candidate_selection"] = {**copy.deepcopy(selection), "selected_key": candidate["key"],
                                                  "selected_title": candidate["title"]}
                project["peqs"].append(result)
            store._validate_project(project)
            store.save(project)
            bundle = Path(temporary) / "check.roompilot"
            store.export_bundle(project, bundle)
            restored = store.import_bundle(bundle)
            assert restored["measurements"] == measurements
            assert restored["baselines"] == project["baselines"]
            assert restored["peqs"] == project["peqs"]
            assert len({q["id"] for q in restored["peqs"]}) == 3
            assert {q["group_id"] for q in restored["peqs"]} == {group_id}
            assert {q["candidate_selection"]["selected_key"] for q in restored["peqs"]} == {c["key"] for c in comparison["candidates"]}
            assert restored["sources"] == project["sources"]
            restored_source = store.assets_dir / restored["id"] / source["relative_path"]
            assert hashlib.sha256(restored_source.read_bytes()).hexdigest() == source["sha256"]
        finally:
            store.close()
    return {"passed": True, "version": __version__, "native_measurements": len(measurements),
            "cal_states": [m["metadata"]["cal_status"] for m in measurements],
            "bands": {channel: len(filters) for channel, filters in peq["filters"].items()},
            "metrics": peq["metrics"], "archive_roundtrip": True,
            "candidate_count": len(comparison["candidates"]),
            "candidate_schema_version": comparison["schema_version"], "bundle_strategy_count": len(restored["peqs"]),
            "group_variant_ids_preserved": True, "group_strategy_parameters_preserved": True,
            "original_source_preserved": True,
            "candidate_targets_match": True, "candidate_explanations_present": True,
            "elapsed_seconds": round(time.perf_counter() - started, 3)}
