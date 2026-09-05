import copy
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from roompilot.controller import Bridge
from roompilot.analysis import filter_response
from roompilot.storage import ProjectStore


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(app, tmp_path):
    store = ProjectStore(tmp_path / "data")
    b = Bridge(store)
    b.createProject("測試空間", "")
    yield b
    b.shutdown()


def measurements():
    f = np.geomspace(20, 20000, 1800)
    data = []
    for channel in ("L", "R"):
        for repeat in ("A", "B"):
            data.append(dict(id=channel+repeat, name=channel+"_P0_"+repeat, channel=channel, position="P0", role="baseline", applied_peq_id="", frequency=f.tolist(), spl=(75+filter_response(f, [{"frequency": 90, "gain": 5, "q": 3}])).tolist(), metadata={"cal_status": "loaded", "cal_name": "mic.txt", "sample_rate": 48000, "sweep_length": 131072, "sweep_level_dbfs": -12, "clipping": False, "headroom_db": 15, "input_device": "mic", "output_device": "dac", "timing_reference": "none"}))
    return data


def load_baseline(b):
    b.project["measurements"] = measurements()
    b.store.save(b.project)
    b.setBaseline(json.dumps([m["id"] for m in b.project["measurements"]]), True)
    assert b.state["baseline_ready"], b.state["message"]


def await_work(b, app):
    deadline = time.monotonic() + 20
    while b.state["busy"] and time.monotonic() < deadline:
        app.processEvents()
        # PySide's qWait can hold the GIL and starve the Python worker.
        time.sleep(.01)
    assert not b.state["busy"], "background work failed to complete"
    assert b.state["message_kind"] != "error", b.state["message"]


def test_cal_missing_requires_ack_and_no_verification_as_baseline(bridge):
    b = bridge
    b.project["measurements"] = measurements()
    b.project["measurements"][0]["metadata"]["cal_status"] = "missing"
    ids = json.dumps([m["id"] for m in b.project["measurements"]])
    b.setBaseline(ids, False)
    assert not b.state["baseline_ready"]
    assert "提醒" in b.state["message"]
    b.setBaseline(ids, True)
    assert b.state["baseline_ready"]
    b.project["measurements"][0]["applied_peq_id"] = "already-equalized"
    b.setBaseline(ids, True)
    assert b.project["baseline_version"] == 1
    assert b.state["message_kind"] == "error"


def test_complete_replacement_revision_and_roundtrip(bridge, app, tmp_path):
    b = bridge
    load_baseline(b)
    b.generatePeq(json.dumps({"bands": 3, "independent": True, "target_level": 75}))
    await_work(b, app)
    first = copy.deepcopy(b.state["selected_peq"])
    assert first["filters"]["L"]
    assert first["status"] == "draft"
    b.export_peq_to(first["id"], tmp_path / "peq.txt")
    assert "取代上一版" in (tmp_path / "peq.txt").read_text(encoding="utf-8-sig")
    assert b._peq()["status"] == "draft"
    b.markApplied(first["id"])
    for m in measurements():
        m["id"] += "-after"
        m["role"] = "verification"
        m["applied_peq_id"] = first["id"]
        m["spl"] = (np.array(m["spl"]) + filter_response(m["frequency"], first["filters"][m["channel"]])).tolist()
        b.project["measurements"].append(m)
    b.compareVerification(first["id"])
    assert b._peq()["verification"]["metrics"]["shape_assessment"] == "improved"
    f = first["filters"]["L"][0]
    b.editFilter("L", 0, f["frequency"], f["gain"]+.3, f["q"], True)
    second = b._peq()
    assert second["id"] != first["id"]
    assert second["status"] == "draft"
    assert second["verification"] == {}
    assert len(second["filters"]["L"]) == len(first["filters"]["L"])
    assert b.project["peqs"][0]["filters"] == first["filters"]
    assert len(b.project["peqs"][0]["verification_history"]) == 1
    bundle = tmp_path / "test.roompilot"
    b.store.export_bundle(b.project, bundle)
    imported = b.store.import_bundle(bundle)
    assert len(imported["peqs"]) == 2
    assert imported["peqs"][0]["verification_history"]


def test_rounded_manual_parameter_rechecked(bridge, app):
    b = bridge
    load_baseline(b)
    b.generatePeq(json.dumps({"bands": 2, "target_level": 75, "f_max": 199, "freq_step": 10}))
    await_work(b, app)
    before = len(b.project["peqs"])
    b.editFilter("L", 0, 199, -3, 3, True)
    assert len(b.project["peqs"]) == before
    assert "取整" in b.state["message"]


def test_baseline_snapshot_not_changed_by_later_annotation(bridge):
    b = bridge
    load_baseline(b)
    b.updateMeasurement("LA", "L", "P+10", "baseline", "")
    assert b.project["measurements"][0]["position"] == "P+10"
    assert b._baseline()["measurements"][0]["position"] == "P0"


def test_cancelled_generation_does_not_create_version(bridge, app):
    load_baseline(bridge)
    bridge.generatePeq(json.dumps({"bands": 10, "mode": "deep"}))
    bridge.cancelWork()
    await_work(bridge, app)
    assert bridge.project["peqs"] == []
    assert "取消" in bridge.state["message"]
