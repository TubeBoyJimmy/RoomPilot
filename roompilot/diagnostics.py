"""Optional packaging check; runs only when explicitly requested on the CLI."""
from pathlib import Path
import tempfile
import time


def check_sample(path):
    from .analysis import generate_peq, quality_report
    from .importers import import_measurements
    from .storage import ProjectStore

    started = time.perf_counter()
    measurements = import_measurements(path)
    selected = [m for m in measurements if m["channel"] in ("L", "R") and m["position"] == "P0"]
    issues = quality_report(selected)
    peq = generate_peq(selected, {"bands": 5, "f_min": 30, "f_max": 200, "independent": True})
    with tempfile.TemporaryDirectory(prefix="roompilot-check-") as temporary:
        store = ProjectStore(Path(temporary) / "data")
        try:
            project = store.create("Packaging verification")
            source = store.preserve_source(project, path)
            for measurement in measurements:
                measurement["source_sha256"] = source["sha256"]
            project["measurements"] = measurements
            store.snapshot_baseline(project, [m["id"] for m in selected], issues, True)
            bundle = Path(temporary) / "check.roompilot"
            store.export_bundle(project, bundle)
            restored = store.import_bundle(bundle)
            assert len(restored["measurements"]) == len(measurements)
        finally:
            store.close()
    return {"passed": True, "native_measurements": len(measurements),
            "cal_states": [m["metadata"]["cal_status"] for m in measurements],
            "bands": {channel: len(filters) for channel, filters in peq["filters"].items()},
            "metrics": peq["metrics"], "archive_roundtrip": True,
            "elapsed_seconds": round(time.perf_counter() - started, 3)}
