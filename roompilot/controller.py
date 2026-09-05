"""Qt application controller. All durable mutations occur on the GUI thread."""
from __future__ import annotations

import copy
import csv
from functools import wraps
import json
import math
from pathlib import Path
import threading
import traceback
import uuid

import numpy as np
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Signal, Slot, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QFileDialog

from .storage import ProjectStore, add_history, timestamp


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def guarded(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:
            traceback.print_exc()
            self._message = str(exc) or type(exc).__name__
            self._message_kind = "error"
            self._refresh()
    return wrapper


class JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.signals = JobSignals()
        self.function = function
        self.cancelled = threading.Event()

    def run(self):
        try:
            result = self.function(self._progress, self.cancelled.is_set)
            self.signals.finished.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or type(exc).__name__)

    def _progress(self, *args):
        fraction = 0.0
        message = "計算中…"
        for arg in args:
            if isinstance(arg, (float, int)):
                fraction = max(0.0, min(1.0, float(arg)))
            elif isinstance(arg, str):
                message = arg
        self.signals.progress.emit(fraction, message)


class Bridge(QObject):
    stateChanged = Signal()

    def __init__(self, store: ProjectStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.project = None
        self._selected_measurement_id = ""
        self._selected_peq_id = ""
        self._message = ""
        self._message_kind = "info"
        self._page = 0
        self._progress = 0.0
        self._state = {}
        self._job = None
        self._completion = None
        self._devices = {"inputs": [], "outputs": []}
        self._quality_cache = []
        self._closing = False
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioInputsChanged.connect(self.refreshDevices)
        self._media_devices.audioOutputsChanged.connect(self.refreshDevices)
        self.refreshDevices()
        self._refresh()

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    def _need_project(self):
        if self.project is None:
            raise ValueError("請先建立或開啟一個空間專案。")
        return self.project

    def _not_busy(self):
        if self._job:
            raise ValueError("請等待目前工作完成，或先取消工作。")

    def _save(self, title=None, detail=""):
        if title:
            add_history(self.project, title, detail)
        self.store.save(self.project)
        self._refresh()

    def _baseline(self, version=None):
        p = self._need_project()
        version = version or p.get("baseline_version")
        return next((b for b in p["baselines"] if b["version"] == version), None)

    def _peq(self, peq_id=None):
        p = self._need_project()
        peq_id = peq_id or self._selected_peq_id
        result = next((item for item in p["peqs"] if item["id"] == peq_id), None)
        if result is None:
            raise ValueError("請先選擇一版 PEQ。")
        return result

    def _quality(self, measurements):
        from .analysis import quality_report
        return clean(quality_report(measurements, self.project.get("settings", {}) if self.project else {}))

    def _summary(self, m):
        meta = m.get("metadata", {})
        result = {key: value for key, value in m.items() if key not in ("frequency", "spl", "phase", "impulse", "metadata")}
        result.update({k: meta.get(k) for k in ("cal_status", "cal_name", "sample_rate", "sweep_level_dbfs", "source_file", "source_format", "input_device", "output_device", "headroom_db")})
        result["metadata"] = copy.deepcopy(meta)
        result["cal_status"] = meta.get("cal_status") or "unknown"
        result["cal_name"] = meta.get("cal_name") or ""
        result["quality"] = [q for q in self._quality_cache if not q.get("measurement_ids") or m["id"] in q.get("measurement_ids", [])]
        return result

    def _refresh(self):
        if self._closing:
            return
        project = self.project
        selected = {}
        peq = {}
        measurements = []
        curves = []
        project_summary = {}
        if project:
            measurements = [self._summary(m) for m in project["measurements"]]
            raw = next((m for m in project["measurements"] if m["id"] == self._selected_measurement_id), None)
            if raw:
                selected = self._summary(raw)
                curves = [{"name": raw["name"], "channel": raw["channel"], "kind": "measurement", "frequency": raw["frequency"], "spl": raw["spl"]}]
            peq = next((v for v in project["peqs"] if v["id"] == self._selected_peq_id), {})
            if self._page == 2 and peq:
                curves = (peq.get("verification") or {}).get("curves") or peq.get("curves", [])
            elif self._page == 0 and project.get("baseline_version"):
                b = self._baseline()
                curves = [{"name": m["name"], "channel": m["channel"], "kind": "baseline", "frequency": m["frequency"], "spl": m["spl"]} for m in b["measurements"] if m.get("position", "P0") == "P0"]
            project_summary = {key: copy.deepcopy(project.get(key)) for key in ("id", "name", "notes", "settings", "baseline_ids", "baseline_version", "current_applied_peq_id", "created_at")}
        # Graph data is a display copy; exact arrays remain in the stored project.
        display_curves = []
        for curve in curves:
            c = dict(curve)
            freq = c.get("frequency", [])
            values = c.get("spl", [])
            if len(freq) > 2500:
                f = np.asarray(freq, dtype=float)
                valid = (f >= 10) & np.isfinite(f)
                indices = np.flatnonzero(valid)
                if len(indices):
                    wanted = np.geomspace(max(10, f[indices[0]]), min(40000, f[indices[-1]]), 2400)
                    indices = np.unique(np.clip(np.searchsorted(f, wanted), 0, len(f) - 1))
                    c["frequency"] = [freq[int(i)] for i in indices]
                    c["spl"] = [values[int(i)] for i in indices]
            display_curves.append(c)
        f_max = 20000
        if self._page == 2 and peq:
            f_max = max(500, min(20000, peq.get("settings", {}).get("f_max", 200) * 2.5))
        baseline_summaries = []
        for snapshot in (project or {}).get("baselines", []):
            summary = {key: copy.deepcopy(snapshot.get(key)) for key in ("version", "created_at", "settings", "quality", "warnings_acknowledged")}
            summary["measurements"] = [self._summary(m) for m in snapshot["measurements"]]
            baseline_summaries.append(summary)
        self._state = clean({"projects": self.store.list_projects(), "project": project_summary, "measurements": measurements, "selected_measurement": selected, "baselines": baseline_summaries, "peqs": [{k: q.get(k) for k in ("id", "name", "created_at", "status", "baseline_version")} for q in (project or {}).get("peqs", [])], "selected_peq": copy.deepcopy(peq), "quality": self._quality_cache, "history": (project or {}).get("history", []), "devices": self._devices, "busy": self._job is not None, "progress": self._progress, "message": self._message, "message_kind": self._message_kind, "page": self._page, "chart": {"curves": display_curves, "f_min": 20, "f_max": f_max}, "baseline_ready": bool(project and project.get("baseline_version"))})
        self.stateChanged.emit()

    @Slot(str, str)
    @guarded
    def createProject(self, name, notes):
        self._not_busy()
        self.project = self.store.create(name, notes)
        self._selected_measurement_id = self._selected_peq_id = ""
        self._quality_cache = []
        self._page = 0
        self._message = "專案已建立，先完成環境與 REW 量測準備。"
        self._message_kind = "success"
        self._refresh()

    @Slot(str)
    @guarded
    def selectProject(self, project_id):
        self._not_busy()
        self.project = self.store.load(project_id)
        self._selected_measurement_id = self.project["measurements"][0]["id"] if self.project["measurements"] else ""
        self._selected_peq_id = self.project["peqs"][-1]["id"] if self.project["peqs"] else ""
        self._quality_cache = self._quality(self.project["measurements"])
        self._message = ""
        self._page = 0
        self._refresh()

    @Slot(str)
    @guarded
    def updateSettings(self, payload):
        self._not_busy()
        self._need_project()
        updates = json.loads(payload)
        if not isinstance(updates, dict):
            raise ValueError("設定格式錯誤。")
        allowed = {"input_device", "output_device", "peq_destination", "mic_orientation", "mic_serial", "volume_note", "sweep_note", "route_note", "room_note", "room_dimensions", "theme", "peq_settings", "mic_height", "speaker_note"}
        updates = {k: v for k, v in updates.items() if k in allowed}
        self.project["settings"].update(updates)
        self._save("更新量測條件", "、".join(updates.keys()))

    @Slot()
    def refreshDevices(self):
        self._devices = {"inputs": list(dict.fromkeys(d.description() for d in QMediaDevices.audioInputs())), "outputs": list(dict.fromkeys(d.description() for d in QMediaDevices.audioOutputs()))}
        self._refresh()

    @Slot(str, bool)
    @guarded
    def updateChecklist(self, key, checked):
        self._not_busy()
        self._need_project()
        self.project["settings"].setdefault("checklist", {})[key] = bool(checked)
        self._save()

    @Slot(int)
    def setPage(self, page):
        self._page = max(0, min(3, page))
        self._refresh()

    @Slot()
    def clearMessage(self):
        self._message = ""
        self._refresh()

    def _start(self, function, completion, message):
        self._not_busy()
        self._job = Job(function)
        self._completion = completion
        self._job.signals.finished.connect(self._job_finished, Qt.ConnectionType.QueuedConnection)
        self._job.signals.failed.connect(self._job_failed, Qt.ConnectionType.QueuedConnection)
        self._job.signals.progress.connect(self._job_progress, Qt.ConnectionType.QueuedConnection)
        self._message = message
        self._message_kind = "info"
        self._progress = 0
        self._refresh()
        QThreadPool.globalInstance().start(self._job)

    @Slot(float, str)
    def _job_progress(self, value, message):
        if self._closing:
            return
        self._progress = value
        self._message = "正在取消，請稍候…" if self._job and self._job.cancelled.is_set() else message
        self._refresh()

    @Slot(object)
    @guarded
    def _job_finished(self, result):
        if self._closing:
            return
        cancelled = self._job and self._job.cancelled.is_set()
        callback = self._completion
        self._job = self._completion = None
        self._progress = 1
        if cancelled:
            self._message = "工作已取消，專案資料未變更。"
            self._message_kind = "info"
            self._refresh()
        else:
            callback(clean(result))

    @Slot(str)
    def _job_failed(self, message):
        if self._closing:
            return
        cancelled = self._job and self._job.cancelled.is_set()
        self._job = self._completion = None
        self._message = "工作已取消。" if cancelled else message
        self._message_kind = "info" if cancelled else "error"
        self._refresh()

    @Slot()
    def cancelWork(self):
        if self._job:
            self._job.cancelled.set()
            self._message = "正在取消，請稍候…"
            self._refresh()

    @Slot(str, str)
    @guarded
    def importFiles(self, purpose="baseline", peq_id=""):
        self._not_busy()
        self._need_project()
        paths, _ = QFileDialog.getOpenFileNames(None, "匯入 REW 量測", "", "REW 量測 (*.mdat *.txt *.csv *.tsv);;所有檔案 (*)")
        if paths:
            self.import_paths(paths, purpose, peq_id)

    @Slot(str, str, str)
    @guarded
    def importPath(self, path, purpose="baseline", peq_id=""):
        self.import_paths([path], purpose, peq_id)

    def import_paths(self, paths, purpose="baseline", peq_id=""):
        self._not_busy()
        project = self._need_project()
        if purpose not in ("baseline", "verification"):
            raise ValueError("請選擇 Baseline 或補錄量測用途。")
        if purpose == "verification":
            peq = self._peq(peq_id)
            if peq.get("status") != "applied":
                raise ValueError("請先確認這版完整 PEQ 已套用，再匯入它的補錄。")
        conditions = copy.deepcopy(project["settings"])

        def run(progress, cancel):
            from .importers import import_measurements
            imported = []
            for index, path in enumerate(paths):
                if cancel():
                    return []
                progress(index / len(paths), f"讀取 {Path(path).name}…")
                data = import_measurements(str(Path(path).resolve()))
                for m in data:
                    m["id"] = str(uuid.uuid4())
                    m["role"] = purpose
                    m["applied_peq_id"] = peq_id if purpose == "verification" else ""
                    m.setdefault("channel", "Unknown")
                    m.setdefault("position", "P0")
                    meta = m.setdefault("metadata", {})
                    meta["session_conditions"] = conditions
                    meta["conditions_source"] = "project_record"
                    meta["source_file"] = Path(path).name
                    m["_import_path"] = str(Path(path).resolve())
                    imported.append(m)
            return imported

        def complete(measurements):
            if not measurements:
                raise ValueError("檔案中沒有可匯入的量測。")
            issues = self._quality(measurements)
            if any(q.get("level") == "error" and q.get("code") in ("invalid_data", "invalid_arrays", "nonfinite_data") for q in issues):
                raise ValueError("量測包含無效的頻率或聲壓數值，請在 REW 確認後重新匯出。")
            staged = dict(self.project, sources=copy.deepcopy(self.project["sources"]))
            for m in measurements:
                imported_hash = m.get("metadata", {}).get("source_sha256")
                source = self.store.preserve_source(staged, m.pop("_import_path"), expected_sha256=imported_hash)
                m["source_sha256"] = source["sha256"]
            self.project["sources"] = staged["sources"]
            self.project["measurements"].extend(measurements)
            self._selected_measurement_id = measurements[0]["id"]
            self._quality_cache = self._quality(self.project["measurements"])
            self._page = 1
            self._message = f"已匯入 {len(measurements)} 筆量測。請確認聲道、位置與 Mic Cal。"
            self._message_kind = "success"
            self._save("匯入補錄" if purpose == "verification" else "匯入量測", f"{len(measurements)} 筆；" + "、".join(Path(p).name for p in paths))

        self._start(run, complete, "正在讀取量測…")

    @Slot(str)
    def selectMeasurement(self, measurement_id):
        self._selected_measurement_id = measurement_id
        self._refresh()

    @Slot(str, str, str, str, str)
    @guarded
    def updateMeasurement(self, measurement_id, channel, position, role, peq_id=""):
        self._not_busy()
        p = self._need_project()
        m = next((m for m in p["measurements"] if m["id"] == measurement_id), None)
        if not m or channel not in ("L", "R", "LR", "Unknown") or position not in ("P0", "P-10", "P+10") or role not in ("baseline", "verification"):
            raise ValueError("聲道、位置或用途無效。")
        if role == "verification":
            q = self._peq(peq_id)
            if q["status"] != "applied":
                raise ValueError("補錄必須對應一版已確認套用的 PEQ。")
        elif m.get("applied_peq_id"):
            raise ValueError("這筆量測是在 PEQ 後匯入，不能直接改成未校正基準。若用途標錯，請以正確用途重新匯入原始檔。")
        m.update(channel=channel, position=position, role=role, applied_peq_id=peq_id if role == "verification" else "")
        self._quality_cache = self._quality(p["measurements"])
        self._save("更新量測分類", f"{m['name']} → {channel} / {position} / {role}")

    @Slot(str, bool)
    @guarded
    def setBaseline(self, ids_payload, acknowledged=False):
        self._not_busy()
        p = self._need_project()
        ids = json.loads(ids_payload)
        selected = [m for m in p["measurements"] if m["id"] in ids]
        if not selected or len(selected) != len(set(ids)):
            raise ValueError("請選擇要作為 Baseline 的量測。")
        if any(m.get("applied_peq_id") or m.get("role") != "baseline" for m in selected):
            raise ValueError("Baseline 必須是未套用本專案校正的基準量測，不能使用 PEQ 後補錄。")
        channels = {m["channel"] for m in selected if m["position"] == "P0"}
        if not {"L", "R"}.issubset(channels):
            raise ValueError("Baseline 至少需要 P0 的左、右獨立量測。L+R 可附加，不能取代 L 與 R。")
        if any(m["channel"] == "Unknown" for m in selected):
            raise ValueError("請先確認每筆量測的播放聲道。")
        report = self._quality(selected)
        errors = [q for q in report if q.get("level") == "error"]
        if errors:
            raise ValueError("建立 Baseline 前請先修正：" + "；".join(q["title"] for q in errors))
        warnings = [q for q in report if q.get("level") in ("warning", "unknown")]
        if warnings and not acknowledged:
            self._quality_cache = report
            raise ValueError("請先查看品質提醒，並勾選已確認提醒。缺少或無法讀取 Mic Cal 時，建議先在 REW 確認後另存匯入。")
        self.store.snapshot_baseline(p, ids, report, bool(acknowledged))
        self._message = f"Baseline v{p['baseline_version']} 已保存。接著設定可用的 PEQ 功能。"
        self._message_kind = "success"
        self._page = 2
        self._refresh()

    @Slot(str)
    @guarded
    def generatePeq(self, payload):
        self._not_busy()
        p = self._need_project()
        baseline = self._baseline()
        if not baseline:
            raise ValueError("請先設定 Baseline。")
        settings = json.loads(payload)
        measurements = copy.deepcopy(baseline["measurements"])
        version = baseline["version"]
        prior = p.get("current_applied_peq_id", "")
        settings["sample_rate"] = settings.get("sample_rate") or next((m["metadata"].get("sample_rate") for m in measurements if m["metadata"].get("sample_rate")), 48000)

        def run(progress, cancel):
            from .analysis import generate_peq
            return generate_peq(measurements, settings, progress=progress, cancel=cancel)

        def complete(result):
            result.update(id=str(uuid.uuid4()), name=f"PEQ v{len(p['peqs'])+1}", created_at=timestamp(), status="draft", baseline_version=version, replaces_peq_id=prior, verification={}, verification_history=[])
            p["peqs"].append(result)
            p["settings"]["peq_settings"] = copy.deepcopy(result.get("settings", settings))
            self._selected_peq_id = result["id"]
            self._page = 2
            self._message = "已產生完整 PEQ 設定。請查看建議，再套用到你的硬體或軟體 DSP。"
            self._message_kind = "success"
            self._save("產生 " + result["name"], f"使用 Baseline v{version}；完整取代式設定。")

        self._start(run, complete, "正在比較低頻校正方案…")

    @Slot(str)
    def selectPeq(self, peq_id):
        self._selected_peq_id = peq_id
        self._refresh()

    @Slot(str, int, float, float, float, bool)
    @guarded
    def editFilter(self, channel, index, frequency, gain, q, enabled):
        self._not_busy()
        p = self._need_project()
        original = self._peq()
        result = copy.deepcopy(original)
        if channel not in result["filters"] or not 0 <= index < len(result["filters"][channel]):
            raise ValueError("找不到這個濾波器。")
        settings = result["settings"]
        if not all(math.isfinite(v) for v in (frequency, gain, q)):
            raise ValueError("請輸入有效參數。")
        low, high = settings.get("f_min", 30), settings.get("f_max", 200)
        boost = settings.get("max_boost", 3) if settings.get("allow_boost") else 0
        if not low <= frequency <= high or not -settings.get("max_cut", 6) <= gain <= boost or not settings.get("min_q", .4) <= q <= settings.get("max_q", 6):
            raise ValueError("參數超出本版校正範圍或 Gain／Q 限制。請修改能力設定後產生新方案。")
        rounded = lambda value, step: round(round(value / step) * step, 8) if step > 0 else value
        item = result["filters"][channel][index]
        item.update(frequency=rounded(frequency, settings.get("freq_step", 1)), gain=rounded(gain, settings.get("gain_step", .1)), q=rounded(q, settings.get("q_step", .01)), enabled=bool(enabled))
        if not low <= item["frequency"] <= high or not -settings.get("max_cut", 6) <= item["gain"] <= boost or not settings.get("min_q", .4) <= item["q"] <= settings.get("max_q", 6):
            raise ValueError("依輸入步進取整後超出範圍，請選擇範圍內可輸入的數值。")
        if item["gain"] > 0 and item["q"] > 2:
            raise ValueError("進階增益僅支援 Q ≤ 2 的寬頻修正。")
        from .analysis import evaluate_peq
        baseline = self._baseline(original["baseline_version"])
        result = evaluate_peq(baseline["measurements"], settings, result["filters"], original["target_level"])
        result["baseline_version"] = original["baseline_version"]
        result.update(id=str(uuid.uuid4()), name=f"PEQ v{len(p['peqs'])+1}", created_at=timestamp(), status="draft", replaces_peq_id=original["id"], verification={}, verification_history=[])
        result["rationale"].insert(0, "由 " + original["name"] + " 手動調整；參數表是完整替換設定。")
        p["peqs"].append(clean(result))
        self._selected_peq_id = result["id"]
        self._message = "手動調整已另存新版本，請重新套用並補錄驗證。"
        self._message_kind = "success"
        self._save("手動調整 " + result["name"], f"來源 {original['name']}，保留原版。")

    @Slot(str)
    @guarded
    def markApplied(self, peq_id):
        self._not_busy()
        p = self._need_project()
        peq = self._peq(peq_id)
        peq["status"] = "applied"
        peq["applied_at"] = timestamp()
        peq["applied_conditions"] = copy.deepcopy(p["settings"])
        p["current_applied_peq_id"] = peq["id"]
        self._message = "已記錄你確認套用此版完整設定。請沿用相同播放路徑、音量及 P0 位置補錄 L／R。"
        self._message_kind = "success"
        self._save("確認已套用 " + peq["name"], "此狀態為使用者確認；尚需補錄验证。")

    @Slot(str)
    @guarded
    def compareVerification(self, peq_id):
        self._not_busy()
        p = self._need_project()
        peq = self._peq(peq_id)
        if peq["status"] != "applied":
            raise ValueError("請先確認 PEQ 已套用並匯入補錄。")
        baseline = self._baseline(peq["baseline_version"])
        measurements = [m for m in p["measurements"] if m.get("role") == "verification" and m.get("applied_peq_id") == peq_id]
        if not measurements:
            raise ValueError("這版 PEQ 還沒有補錄。請選擇「匯入補錄」。")
        from .analysis import compare_verification
        result = clean(compare_verification(baseline["measurements"], measurements, peq))
        result["created_at"] = timestamp()
        result["measurement_ids"] = [m["id"] for m in measurements]
        peq["verification"] = result
        peq.setdefault("verification_history", []).append(copy.deepcopy(result))
        self._page = 2
        self._selected_peq_id = peq_id
        self._message = result.get("title", "補錄比較已完成。")
        self._message_kind = "success" if result.get("status") == "improved" else "warning"
        self._save("驗證 " + peq["name"], result.get("title", ""))

    @Slot(str, str)
    @guarded
    def exportPeq(self, peq_id, format):
        self._not_busy()
        peq = self._peq(peq_id)
        extension = "csv" if format == "csv" else "txt"
        path, _ = QFileDialog.getSaveFileName(None, "匯出完整 PEQ 設定", peq["name"] + "." + extension, f"{extension.upper()} (*.{extension})")
        if path:
            self.export_peq_to(peq_id, path, format)
            self._message = "已匯出完整參數表。匯出不代表設備已套用。"
            self._message_kind = "success"
            self._save("匯出 " + peq["name"], Path(path).name)

    def export_peq_to(self, peq_id, path, format="text"):
        peq = self._peq(peq_id)
        if format == "csv":
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["方案", "Baseline", "聲道", "Band", "類型", "頻率_Hz", "Gain_dB", "Q", "啟用", "前級_dB"])
                for channel, filters in peq["filters"].items():
                    for i, f in enumerate(filters):
                        writer.writerow([peq["name"], peq["baseline_version"], channel, i+1, "PK", f["frequency"], f["gain"], f["q"], f.get("enabled", True), peq.get("preamp_db", 0)])
        else:
            lines = ["RoomPilot — 完整 PEQ 參數表", f"專案：{self.project['name']}", f"方案：{peq['name']} / Baseline v{peq['baseline_version']}", "請以這份完整設定取代上一版；請勿再疊加上一版濾波器。", "未使用的 band 請停用。PK = Peak / Bell / Peak-Dip。", "這是通用參數表，依你的 DSP 輸入欄位手動套用。", f"Preamp: {peq.get('preamp_db',0):.2f} dB", ""]
            for channel, filters in peq["filters"].items():
                lines.append("Channel: " + ("L + R（同一組套用兩側）" if channel == "Shared" else channel))
                for i, f in enumerate(filters):
                    lines.append(f"Filter {i+1}: {'ON' if f.get('enabled',True) else 'OFF'} PK Fc {f['frequency']:.2f} Hz Gain {f['gain']:.2f} dB Q {f['q']:.3f}")
                if not filters:
                    lines.append("本聲道不需要濾波器。")
                lines.append("")
            lines.extend(["套用後請保持原播放路徑與音量，在 P0 重新量測 L / R。", "播放器內 DSP：確認掃頻實際經過該 DSP；必要時用 REW 產生測試檔交播放器播放。"])
            Path(path).write_text("\n".join(lines), encoding="utf-8-sig")

    @Slot()
    @guarded
    def exportProject(self):
        self._not_busy()
        p = self._need_project()
        path, _ = QFileDialog.getSaveFileName(None, "匯出可攜專案", p["name"] + ".roompilot", "RoomPilot 專案 (*.roompilot)")
        if path:
            self.store.export_bundle(p, path)
            self._message = "專案已匯出，包含原始檔與所有版本，可在另一台電腦匯入。"
            self._message_kind = "success"
            self._save("匯出專案", Path(path).name)

    @Slot()
    @guarded
    def importProject(self):
        self._not_busy()
        path, _ = QFileDialog.getOpenFileName(None, "匯入可攜專案", "", "RoomPilot 專案 (*.roompilot)")
        if path:
            self.project = self.store.import_bundle(path)
            self._selected_measurement_id = self.project["measurements"][0]["id"] if self.project["measurements"] else ""
            self._selected_peq_id = self.project["peqs"][-1]["id"] if self.project["peqs"] else ""
            self._quality_cache = self._quality(self.project["measurements"])
            self._page = 0
            self._message = "已匯入獨立專案副本，所有版本已保留。"
            self._message_kind = "success"
            self._refresh()

    @Slot(str)
    @guarded
    def openLink(self, key):
        links = {"rew": "https://www.roomeqwizard.com/#downloads", "rew_setup": "https://www.roomeqwizard.com/help/help/html/makingmeasurements.html", "rew_cal": "https://www.roomeqwizard.com/help/help/html/calfiles.html", "rew_fileplayback": "https://www.roomeqwizard.com/help/help/html/makingmeasurements.html#fileplayback"}
        if key in links:
            QDesktopServices.openUrl(QUrl(links[key]))

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        if self._job:
            self._job.cancelled.set()
        QThreadPool.globalInstance().waitForDone(15000)
        self.store.close()
