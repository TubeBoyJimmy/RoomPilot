"""Qt application controller. All durable mutations occur on the GUI thread."""
from __future__ import annotations

import copy
import csv
import hashlib
from functools import wraps
import json
import math
from pathlib import Path
import re
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
        self._gain_chart_cache = {}
        self._explanation_cache = {}
        self._candidate_comparison = {}
        self._candidate_context = {}
        self._candidate_key = ""
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

    def _active_peqs(self):
        return [q for q in (self.project or {}).get("peqs", []) if not q.get("deleted_at")]

    def _incumbent_for(self, baseline, settings):
        """Only the selected saved variant on this Baseline can seed a refit."""
        if settings.get("guard_policy") != "v5" or settings.get("strategy") == "extend_existing":
            return None
        selected = next((q for q in self._active_peqs() if q["id"] == self._selected_peq_id), None)
        if selected and selected.get("baseline_version") == baseline["version"]:
            return copy.deepcopy(selected)
        return None

    def _next_peq_name(self):
        # A version can contain several strategy records. Deleted versions also
        # reserve their number so restoring a group cannot create a collision.
        numbers = []
        for peq in self._need_project()["peqs"]:
            match = re.fullmatch(r"PEQ v(\d+)", peq.get("group_name") or peq.get("name", ""))
            if match:
                numbers.append(int(match.group(1)))
        return f"PEQ v{max(numbers, default=0) + 1}"

    @staticmethod
    def _peq_label(peq):
        return peq["name"] + (" · " + peq["variant_title"] if peq.get("variant_title") else "")

    def _group_members(self, peq):
        if not peq.get("group_id"):
            return [peq]
        return sorted([q for q in self._need_project()["peqs"] if q.get("group_id") == peq["group_id"]],
                      key=lambda q: q["variant_order"])

    def _default_group_member(self, peq):
        members = self._group_members(peq)
        applied_id = self._need_project().get("current_applied_peq_id")
        return next((q for q in members if q["id"] == applied_id),
                    next((q for q in members if q.get("variant_key") == peq.get("group_default_variant_key")), members[0]))

    def _latest_peq_id(self):
        active = self._active_peqs()
        return self._default_group_member(active[-1])["id"] if active else ""

    def _peq_summary(self, peq):
        summary = {k: peq.get(k) for k in ("id", "name", "created_at", "status", "baseline_version", "deleted_at",
                                          "group_id", "group_name", "variant_key", "variant_title", "variant_order")}
        summary.update(display_name=self._peq_label(peq), key=peq.get("variant_key", ""), title=peq.get("variant_title", ""),
                       is_selected=peq["id"] == self._selected_peq_id)
        selection = peq.get("candidate_selection") or {}
        candidate = next((c for c in selection.get("candidates", []) if c.get("key") == peq.get("variant_key")), {})
        summary.update(metrics=candidate.get("metrics", {}), low_cut_limit_db=candidate.get("low_cut_limit_db"),
                       within_limit=candidate.get("within_limit", True))
        return summary

    def _group_summaries(self, records):
        groups = {}
        for peq in records:
            groups.setdefault(peq.get("group_id") or peq["id"], []).append(peq)
        summaries = []
        for members in groups.values():
            representative = next((q for q in members if q["id"] == self._selected_peq_id), self._default_group_member(members[0]))
            summary = self._peq_summary(representative)
            summary.update(variant_count=len(members), variant_ids=[q["id"] for q in members],
                           has_applied_variant=any(q.get("status") == "applied" for q in members))
            summaries.append(summary)
        return summaries

    def _clear_candidates(self):
        self._candidate_comparison = {}
        self._candidate_context = {}
        self._candidate_key = ""

    def _candidate(self, key):
        p = self._need_project()
        context = self._candidate_context
        if context.get("project_id") != p["id"] or context.get("baseline_version") != p.get("baseline_version"):
            raise ValueError("候選所用的專案或 Baseline 已改變，請重新產生候選。")
        item = next((c for c in self._candidate_comparison.get("candidates", []) if c["key"] == key), None)
        if item is None:
            raise ValueError("請先產生並選取一個候選方案。")
        return item

    def _no_candidate_preview(self):
        if self._candidate_key:
            raise ValueError("目前正在預覽未儲存候選；請先儲存此方案，或切回已儲存 PEQ。")

    @staticmethod
    def _fingerprint(value):
        return hashlib.sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _display_peq(self, peq):
        """Add post-hoc explanations to old revisions without changing history."""
        if not peq or peq.get("explanation") or self._page != 2:
            return peq
        if peq.get("id") in self._explanation_cache:
            return {**peq, **self._explanation_cache[peq["id"]]}
        extra = {}
        try:
            from .analysis import explain_peq, ALGORITHM_VERSION
            baseline = self._baseline(peq.get("baseline_version"))
            if baseline is None:
                raise ValueError("找不到此方案的 Baseline 快照。")
            explanation = clean(explain_peq(baseline["measurements"], peq.get("settings", {}), peq["filters"], peq["target_level"]))
            explanation["post_hoc"] = True
            explanation["evaluated_with_algorithm"] = ALGORITHM_VERSION
            explanation["source_algorithm"] = peq.get("algorithm_version", "未記錄")
            explanation.setdefault("notes", []).insert(0, "舊版補充說明：保留原參數與目標，以目前模型重新評估收益與代價；成本不是當初搜尋的歷史紀錄。")
            extra = {"explanation": explanation}
        except (ValueError, TypeError, KeyError, ImportError) as exc:
            extra = {"explanation_error": "無法補充此舊版的模型說明：" + str(exc)}
        if len(self._explanation_cache) >= 8:
            self._explanation_cache.pop(next(iter(self._explanation_cache)))
        self._explanation_cache[peq["id"]] = extra
        return {**peq, **extra}

    def _peq(self, peq_id=None, include_deleted=False):
        p = self._need_project()
        peq_id = peq_id or self._selected_peq_id
        result = next((item for item in p["peqs"] if item["id"] == peq_id), None)
        if result is None or (result.get("deleted_at") and not include_deleted):
            raise ValueError("請先選擇一版 PEQ。")
        return result

    def _gain_chart(self, peq):
        if not peq:
            return {"curves": [], "f_min": 20, "f_max": 500}
        settings = peq.get("settings", {})
        fs = settings.get("sample_rate", 48000)
        key = json.dumps([peq.get("filters", {}), fs, settings.get("f_max", 200)], sort_keys=True)
        if key in self._gain_chart_cache:
            return self._gain_chart_cache[key]
        from .analysis import filter_response
        hi = min(fs * .499, max(500, settings.get("f_max", 200) * 2.5))
        grid = np.geomspace(20, hi, 1000)
        curves = [{"name": "0 dB", "channel": "Shared", "kind": "zero", "frequency": grid.tolist(), "spl": [0.] * len(grid)}]
        for channel, bands in peq.get("filters", {}).items():
            curves.append(dict(name=f"{channel} 合成 PEQ", channel=channel, kind="peq_total", frequency=grid.tolist(), spl=filter_response(grid, bands, fs).tolist()))
            for index, band in enumerate(bands):
                if band.get("enabled", True):
                    curves.append(dict(name=f"{channel} Band {index + 1} · {band['frequency']:g} Hz", channel=channel, kind="per_band", frequency=grid.tolist(), spl=filter_response(grid, [band], fs).tolist()))
        result = {"curves": curves, "f_min": 20, "f_max": hi}
        # Revisions are immutable. Bound memory when browsing many versions.
        if len(self._gain_chart_cache) >= 8:
            self._gain_chart_cache.pop(next(iter(self._gain_chart_cache)))
        self._gain_chart_cache[key] = result
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
        candidate_preview = {}
        if project:
            if self._candidate_context and (self._candidate_context.get("project_id") != project["id"] or self._candidate_context.get("baseline_version") != project.get("baseline_version")):
                self._clear_candidates()
            measurements = [self._summary(m) for m in project["measurements"]]
            raw = next((m for m in project["measurements"] if m["id"] == self._selected_measurement_id), None)
            if raw:
                selected = self._summary(raw)
                curves = [{"name": raw["name"], "channel": raw["channel"], "kind": "measurement", "frequency": raw["frequency"], "spl": raw["spl"]}]
            peq = next((v for v in self._active_peqs() if v["id"] == self._selected_peq_id), {})
            peq = self._display_peq(peq)
            if self._candidate_key:
                choice = self._candidate(self._candidate_key)
                candidate_preview = {**choice["result"], "id": "draft:" + choice["key"], "name": choice["title"], "is_draft": True,
                                     "status": "candidate", "baseline_version": self._candidate_context["baseline_version"], "verification": {}}
            displayed = candidate_preview or peq
            if self._page == 2 and displayed:
                curves = (displayed.get("verification") or {}).get("curves") or displayed.get("curves", [])
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
        if self._page == 2 and (candidate_preview or peq):
            f_max = max(500, min(20000, (candidate_preview or peq).get("settings", {}).get("f_max", 200) * 2.5))
        baseline_summaries = []
        for snapshot in (project or {}).get("baselines", []):
            summary = {key: copy.deepcopy(snapshot.get(key)) for key in ("version", "created_at", "settings", "quality", "warnings_acknowledged")}
            summary["measurements"] = [self._summary(m) for m in snapshot["measurements"]]
            baseline_summaries.append(summary)
        self._state = clean({"projects": self.store.list_projects(), "project": project_summary, "measurements": measurements, "selected_measurement": selected, "baselines": baseline_summaries, "peqs": self._group_summaries(self._active_peqs()), "deleted_peqs": self._group_summaries([q for q in (project or {}).get("peqs", []) if q.get("deleted_at")]), "selected_peq": copy.deepcopy(peq), "quality": self._quality_cache, "history": (project or {}).get("history", []), "devices": self._devices, "busy": self._job is not None, "progress": self._progress, "message": self._message, "message_kind": self._message_kind, "page": self._page, "chart": {"curves": display_curves, "f_min": 20, "f_max": f_max}, "peq_chart": self._gain_chart(peq), "baseline_ready": bool(project and project.get("baseline_version"))})
        self._state["peq_variants"] = clean([self._peq_summary(q) for q in self._active_peqs()])
        self._state["selected_peq_variants"] = clean([self._peq_summary(q) for q in self._group_members(peq)]) if peq.get("group_id") else []
        self._state["candidate_comparison"] = clean(copy.deepcopy(self._candidate_comparison))
        self._state["candidate_comparison"]["selected_key"] = self._candidate_key
        self._state["candidate_preview"] = clean(candidate_preview)
        self._state["peq_chart"] = clean(self._gain_chart(candidate_preview or peq))
        self.stateChanged.emit()

    @Slot(str, str)
    @guarded
    def createProject(self, name, notes):
        self._not_busy()
        self.project = self.store.create(name, notes)
        self._clear_candidates()
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
        self._clear_candidates()
        self._explanation_cache = {}
        self._selected_measurement_id = self.project["measurements"][0]["id"] if self.project["measurements"] else ""
        self._selected_peq_id = self._latest_peq_id()
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
                    meta["project_notes_at_import"] = conditions
                    # Analysis-host choices are notes, not evidence of recording
                    # conditions. Preserve acquisition metadata from the source.
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
        self._clear_candidates()
        self._message = f"Baseline v{p['baseline_version']} 已保存。接著設定可用的 PEQ 功能。"
        self._message_kind = "success"
        self._page = 2
        self._refresh()

    @Slot(str)
    @guarded
    def generatePeq(self, payload):
        self._not_busy()
        self._no_candidate_preview()
        p = self._need_project()
        baseline = self._baseline()
        if not baseline:
            raise ValueError("請先設定 Baseline。")
        settings = json.loads(payload)
        if not isinstance(settings, dict):
            raise ValueError("PEQ 設定格式錯誤。")
        measurements = copy.deepcopy(baseline["measurements"])
        version = baseline["version"]
        prior = p.get("current_applied_peq_id", "")
        settings["sample_rate"] = settings.get("sample_rate") or next((m["metadata"].get("sample_rate") for m in measurements if m["metadata"].get("sample_rate")), 48000)
        base_peq = None
        if settings.get("strategy") == "extend_existing":
            base_peq = copy.deepcopy(self._peq())
            if base_peq["baseline_version"] != version:
                raise ValueError("保留擴充需要目前 Baseline 的 PEQ；請選擇相同 Baseline 版本的方案。")

        incumbent_peq = self._incumbent_for(baseline, settings)

        self._clear_candidates()

        def run(progress, cancel):
            from .analysis import generate_peq
            extra = {"incumbent_peq": incumbent_peq} if incumbent_peq else {}
            return generate_peq(measurements, settings, progress=progress, cancel=cancel, base_peq=base_peq, **extra)

        def complete(result):
            # Exact repeated computation does not need another durable revision.
            def same_result(q):
                return (q.get("baseline_version") == version and q.get("algorithm_version") == result.get("algorithm_version")
                        and q.get("settings") == result.get("settings") and q.get("filters") == result.get("filters")
                        and q.get("target_level") == result.get("target_level"))
            # Repeatedly extending the selected result without changing anything
            # is also a no-op: the selected source ID naturally changes once.
            existing = base_peq if base_peq and same_result(base_peq) else next((q for q in reversed(self._active_peqs()) if same_result(q) and q.get("extension_source_id") == (base_peq or {}).get("id")), None)
            if existing:
                self._selected_peq_id = existing["id"]
                self._message = f"結果與 {existing['name']} 完全相同，已選取原方案。"
                self._message_kind = "info"
                self._page = 2
                self._refresh()
                return
            result.update(id=str(uuid.uuid4()), name=self._next_peq_name(), created_at=timestamp(), status="draft", baseline_version=version, replaces_peq_id=prior, verification={}, verification_history=[])
            result["extension_source_id"] = (base_peq or {}).get("id")
            p["peqs"].append(result)
            p["settings"]["peq_settings"] = copy.deepcopy(result.get("settings", settings))
            self._selected_peq_id = result["id"]
            self._page = 2
            self._message = "已產生完整 PEQ 設定。請查看建議，再套用到你的硬體或軟體 DSP。"
            self._message_kind = "success"
            self._save("產生 " + result["name"], f"使用 Baseline v{version}；完整取代式設定。")

        self._start(run, complete, "正在比較低頻校正方案…")

    @Slot(str, float)
    @guarded
    def generatePeqCandidates(self, payload, low_cut_limit_db=1.0):
        self._not_busy()
        p = self._need_project()
        baseline = self._baseline()
        if not baseline:
            raise ValueError("請先設定 Baseline。")
        settings = json.loads(payload)
        if not isinstance(settings, dict):
            raise ValueError("PEQ 設定格式錯誤。")
        if not math.isfinite(low_cut_limit_db) or not 0 <= low_cut_limit_db <= 6:
            raise ValueError("低處額外減益容許量需為 0–6 dB RMS。")
        measurements = copy.deepcopy(baseline["measurements"])
        version = baseline["version"]
        settings["sample_rate"] = settings.get("sample_rate") or next((m.get("metadata", {}).get("sample_rate") for m in measurements if m.get("metadata", {}).get("sample_rate")), 48000)
        base_peq = None
        if settings.get("strategy") == "extend_existing":
            self._no_candidate_preview()
            base_peq = copy.deepcopy(self._peq())
            if base_peq["baseline_version"] != version:
                raise ValueError("保留擴充需要目前 Baseline 的已儲存方案。")
        incumbent_peq = self._incumbent_for(baseline, settings)
        source_values = lambda q: {k: q.get(k) for k in ("filters", "settings", "target_level", "baseline_version")} if q else None
        context = dict(project_id=p["id"], baseline_version=version, baseline_fingerprint=self._fingerprint(measurements),
                       generation_settings=copy.deepcopy(settings), settings_fingerprint=self._fingerprint(settings),
                       source_peq_id=(base_peq or {}).get("id"), source_fingerprint=self._fingerprint(source_values(base_peq)),
                       replaces_peq_id=p.get("current_applied_peq_id", ""))
        self._clear_candidates()
        self._page = 2

        def run(progress, cancel):
            from .comparison import generate_peq_candidates
            extra = {"incumbent_peq": incumbent_peq} if incumbent_peq else {}
            return generate_peq_candidates(measurements, settings, progress=progress, cancel=cancel,
                                           base_peq=base_peq, low_cut_limit_db=low_cut_limit_db, **extra)

        def complete(result):
            if not self.project or self.project["id"] != context["project_id"] or self.project.get("baseline_version") != version:
                raise ValueError("候選計算期間專案已改變，結果未保存。")
            self._candidate_comparison = result
            self._candidate_context = context
            self._candidate_key = result.get("selected_key", "")
            self._page = 2
            if result.get("schema_version") == 2:
                try:
                    self._save_candidate_bundle(self._candidate_key)
                finally:
                    # New generations are a single durable group, never an
                    # additional preview/save workflow or a partially saved set.
                    self._clear_candidates()
                    self._refresh()
                return
            self._message = "候選已完成，尚未新增 PEQ 版本。請比較收益與代價，選定後儲存。" if result.get("has_feasible_candidate") else "本輪候選均未符合額外減益容許量；可查看原因，或調整設定後重新比較。"
            self._message_kind = "info" if result.get("has_feasible_candidate") else "warning"
            self._refresh()

        self._start(run, complete, "正在計算相同目標下的不同取捨…")

    @Slot(str)
    @guarded
    def selectPeqCandidate(self, key):
        self._not_busy()
        candidate = self._candidate(key)
        self._candidate_key = key
        self._page = 2
        self._message = "預覽 " + candidate["title"] + "；尚未保存為 PEQ。"
        self._message_kind = "info" if candidate.get("within_limit") else "warning"
        self._refresh()

    @Slot()
    @guarded
    def discardPeqCandidates(self):
        self._not_busy()
        self._clear_candidates()
        self._message = "候選預覽已關閉。"
        self._message_kind = "info"
        self._refresh()

    @Slot(str)
    @guarded
    def savePeqCandidate(self, key):
        self._not_busy()
        if self._candidate_comparison.get("schema_version") == 2:
            self._save_candidate_bundle(key)
            return
        candidate = self._candidate(key)
        if not candidate.get("within_limit"):
            raise ValueError("這個候選未符合設定的低處額外減益容許量；請調整設定後重新比較。")
        p, context = self.project, self._candidate_context
        baseline = self._baseline(context["baseline_version"])
        if self._fingerprint(baseline["measurements"]) != context["baseline_fingerprint"]:
            self._clear_candidates()
            raise ValueError("Baseline 資料已改變，請重新產生候選。")
        if context.get("source_peq_id"):
            source = self._peq(context["source_peq_id"], include_deleted=True)
            values = {k: source.get(k) for k in ("filters", "settings", "target_level", "baseline_version")}
            if self._fingerprint(values) != context["source_fingerprint"]:
                self._clear_candidates()
                raise ValueError("擴充起點的參數已改變，請重新產生候選。")
        result = copy.deepcopy(candidate["result"])
        result["candidate_selection"] = {k: copy.deepcopy(v) for k, v in self._candidate_comparison.items() if k != "candidates"}
        selection = result["candidate_selection"]
        selection.update(selected_key=key, selected_title=candidate["title"], selected_at=timestamp(), baseline_version=context["baseline_version"],
                         source_peq_id=context.get("source_peq_id"), baseline_fingerprint=context["baseline_fingerprint"],
                         settings_fingerprint=context["settings_fingerprint"], historical=True)
        selection["candidates"] = []
        for item in self._candidate_comparison.get("candidates", []):
            summary = {k: copy.deepcopy(v) for k, v in item.items() if k != "result"}
            summary["result"] = {k: copy.deepcopy(item["result"].get(k)) for k in ("filters", "settings", "target_level", "algorithm_version")}
            selection["candidates"].append(summary)
        # Candidate-local keys never become durable PEQ references.
        existing = next((q for q in reversed(self._active_peqs()) if q.get("baseline_version") == context["baseline_version"]
                         and q.get("filters") == result["filters"] and q.get("settings") == result["settings"]
                         and q.get("target_level") == result["target_level"] and q.get("algorithm_version") == result.get("algorithm_version")
                         and (q.get("candidate_selection") or {}).get("low_cut_limit_db") == selection.get("low_cut_limit_db")
                         and q.get("extension_source_id") == context.get("source_peq_id")), None)
        if existing:
            self._selected_peq_id = existing["id"]
            self._candidate_key = ""
            self._message = "這個候選已保存為 " + existing["name"] + "，已選取原版本。"
            self._message_kind = "info"
            self._refresh()
            return
        result.update(id=str(uuid.uuid4()), name=self._next_peq_name(), created_at=timestamp(), status="draft",
                      baseline_version=context["baseline_version"], replaces_peq_id=p.get("current_applied_peq_id", ""),
                      extension_source_id=context.get("source_peq_id"), verification={}, verification_history=[])
        p["peqs"].append(clean(result))
        p["settings"]["peq_settings"] = copy.deepcopy(result["settings"])
        p["settings"]["peq_low_cut_limit_db"] = selection["low_cut_limit_db"]
        self._selected_peq_id = result["id"]
        self._candidate_key = ""
        self._page = 2
        self._message = result["name"] + " 已保存，含候選比較與各段計算依據。這是完整替換設定。"
        self._message_kind = "success"
        self._save("保存 " + result["name"], candidate["title"] + f"；低處額外減益容許量 {selection['low_cut_limit_db']:g} dB RMS，尚需補錄驗證。")

    def _save_candidate_bundle(self, key):
        """Persist every strategy in one transaction, retaining exact identities.

        Build and validate a detached project first: cancelled/failed generation
        and a failed database write cannot leave half a version in memory or disk.
        """
        self._candidate(key)
        p, context = self.project, self._candidate_context
        baseline = self._baseline(context["baseline_version"])
        if self._fingerprint(baseline["measurements"]) != context["baseline_fingerprint"]:
            raise ValueError("Baseline 資料已改變，請重新產生 PEQ。")
        if context.get("source_peq_id"):
            source = self._peq(context["source_peq_id"], include_deleted=True)
            values = {k: source.get(k) for k in ("filters", "settings", "target_level", "baseline_version")}
            if self._fingerprint(values) != context["source_fingerprint"]:
                raise ValueError("擴充起點的參數已改變，請重新產生 PEQ。")
        comparison = self._candidate_comparison
        candidates = comparison.get("candidates", [])
        keys = [c.get("key") for c in candidates]
        if len(candidates) != 3 or len(set(keys)) != 3 or not all(isinstance(k, str) and k for k in keys):
            raise ValueError("三種策略尚未全部完成，未新增 PEQ 版本。請重新產生。")
        for item in candidates:
            if not item.get("within_limit") or not isinstance(item.get("result"), dict):
                raise ValueError(f"{item.get('title', '策略')} 未完成或未符合設定的容許量；整組未保存。請調整設定後重新產生。")
        result_identity_fields = ("filters", "settings", "target_level", "preamp_db", "algorithm_version")
        identity = {"baseline_version": context["baseline_version"], "baseline_fingerprint": context["baseline_fingerprint"],
                    "source_peq_id": context.get("source_peq_id"), "generation_settings": context["generation_settings"],
                    "low_cut_limit_db": comparison.get("low_cut_limit_db"),
                    "variants": [{"key": c["key"], "title": c["title"], "low_cut_limit_db": c.get("low_cut_limit_db"),
                                  "result": {k: c["result"].get(k) for k in result_identity_fields}}
                                 for c in candidates]}
        fingerprint = self._fingerprint(identity)
        existing = [q for q in self._active_peqs() if q.get("group_fingerprint") == fingerprint]
        def same_variant(q):
            candidate = next((c for c in candidates if c["key"] == q.get("variant_key")), None)
            return (candidate is not None and q.get("variant_title") == candidate["title"]
                    and q.get("baseline_version") == context["baseline_version"]
                    and q.get("extension_source_id") == context.get("source_peq_id")
                    and all(q.get(k) == candidate["result"].get(k) for k in result_identity_fields))
        if (len(existing) == 3 and {q.get("variant_key") for q in existing} == set(keys)
                and len({q.get("group_id") for q in existing}) == 1 and all(same_variant(q) for q in existing)):
            selected = next(q for q in existing if q["variant_key"] == key)
            self._selected_peq_id = selected["id"]
            self._candidate_key = ""
            self._message = selected["name"] + " 已含相同三種策略，已選取原版本。"
            self._message_kind = "info"
            self._refresh()
            return
        group_id, group_name, created_at = str(uuid.uuid4()), self._next_peq_name(), timestamp()
        selection = {k: copy.deepcopy(v) for k, v in comparison.items() if k != "candidates"}
        selection.update(selected_at=created_at, baseline_version=context["baseline_version"],
                         source_peq_id=context.get("source_peq_id"), baseline_fingerprint=context["baseline_fingerprint"],
                         settings_fingerprint=context["settings_fingerprint"], historical=True)
        selection["candidates"] = []
        for item in candidates:
            # All exact results are sibling PEQs. Avoid embedding them again in
            # every member; these summaries are the shared comparison evidence.
            selection["candidates"].append({k: copy.deepcopy(v) for k, v in item.items() if k != "result"})
        results = []
        for order, item in enumerate(candidates):
            result = copy.deepcopy(item["result"])
            result.update(id=str(uuid.uuid4()), name=group_name, group_id=group_id, group_name=group_name,
                          group_fingerprint=fingerprint, group_default_variant_key=key,
                          variant_key=item["key"], variant_title=item["title"], variant_order=order,
                          created_at=created_at, status="draft", baseline_version=context["baseline_version"],
                          replaces_peq_id=p.get("current_applied_peq_id", ""), extension_source_id=context.get("source_peq_id"),
                          verification={}, verification_history=[])
            result["candidate_selection"] = {**copy.deepcopy(selection), "selected_key": item["key"], "selected_title": item["title"]}
            results.append(clean(result))
        selected = next(q for q in results if q["variant_key"] == key)
        updated = copy.deepcopy(p)
        updated["peqs"].extend(results)
        updated["settings"]["peq_settings"] = copy.deepcopy(context["generation_settings"])
        updated["settings"]["peq_low_cut_limit_db"] = comparison.get("low_cut_limit_db", 1.)
        add_history(updated, "產生 " + group_name, "保存三種策略；每種策略保留獨立套用與補錄紀錄。尚需實際補錄驗證。")
        self.store._validate_project(updated)
        self.store.save(updated)
        self.project = updated
        self._selected_peq_id = selected["id"]
        self._candidate_key = ""
        self._page = 2
        self._message = group_name + " 已保存三種策略。點選標籤切換；套用與補錄只對應所選策略。"
        self._message_kind = "success"
        self._refresh()

    @Slot(str)
    @guarded
    def selectPeq(self, peq_id):
        self._peq(peq_id)
        self._candidate_key = ""
        self._selected_peq_id = peq_id
        self._refresh()

    @Slot(str)
    @guarded
    def deletePeq(self, peq_id):
        self._not_busy()
        peq = self._peq(peq_id)
        members = self._group_members(peq)
        deleted_at = timestamp()
        for member in members:
            member["deleted_at"] = deleted_at
        ids = {q["id"] for q in members}
        if self._selected_peq_id in ids:
            self._selected_peq_id = self._latest_peq_id()
        self._message = f"{peq['name']} 已移至回收區，可在歷程還原。"
        if self.project.get("current_applied_peq_id") in ids:
            self._message += " 設備上的設定未改變，目前套用紀錄仍保留。"
        self._message_kind = "info"
        self._save("刪除 " + peq["name"], "移至可還原回收區，保留補錄及歷史關聯。")

    @Slot(str)
    @guarded
    def restorePeq(self, peq_id):
        self._not_busy()
        peq = self._peq(peq_id, include_deleted=True)
        if not peq.get("deleted_at"):
            raise ValueError("此方案不在回收區。")
        for member in self._group_members(peq):
            member.pop("deleted_at", None)
        self._candidate_key = ""
        self._selected_peq_id = peq_id
        self._message = f"{peq['name']} 已還原。"
        self._message_kind = "success"
        self._save("還原 " + peq["name"])

    @Slot(str, int, float, float, float, bool)
    @guarded
    def editFilter(self, channel, index, frequency, gain, q, enabled):
        self._not_busy()
        self._no_candidate_preview()
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
        result["target_reference"] = copy.deepcopy(original.get("target_reference") or {"mode": "inherited", "level_db": original["target_level"], "statistic": "沿用舊版目標；原始估算參考未保存。"})
        result["allocation"] = {"strategy": "manual", "skipped": ["手動調整後重新驗算，不宣稱原先的凍結 Band 或裙帶容差仍成立。"]}
        result["baseline_version"] = original["baseline_version"]
        result.update(id=str(uuid.uuid4()), name=self._next_peq_name(), created_at=timestamp(), status="draft", replaces_peq_id=original["id"], verification={}, verification_history=[])
        result["rationale"].insert(0, "由 " + self._peq_label(original) + " 手動調整；參數表是完整替換設定。")
        p["peqs"].append(clean(result))
        self._selected_peq_id = result["id"]
        self._message = "手動調整已另存新版本，請重新套用並補錄驗證。"
        self._message_kind = "success"
        self._save("手動調整 " + result["name"], f"來源 {self._peq_label(original)}，保留原版。")

    @Slot(str)
    @guarded
    def markApplied(self, peq_id):
        self._not_busy()
        self._no_candidate_preview()
        p = self._need_project()
        peq = self._peq(peq_id)
        peq["status"] = "applied"
        peq["applied_at"] = timestamp()
        peq["applied_conditions"] = copy.deepcopy(p["settings"])
        p["current_applied_peq_id"] = peq["id"]
        self._message = "已記錄你確認套用此版完整設定。請沿用相同播放路徑、音量及 P0 位置補錄 L／R。"
        self._message_kind = "success"
        self._save("確認已套用 " + self._peq_label(peq), "此狀態為使用者確認；尚需補錄验证。")

    @Slot(str)
    @guarded
    def compareVerification(self, peq_id):
        self._compare_verification(peq_id, False)

    @Slot(str)
    @guarded
    def compareVerificationWithOverride(self, peq_id):
        self._compare_verification(peq_id, True)

    def _compare_verification(self, peq_id, allow_mismatch):
        self._not_busy()
        self._no_candidate_preview()
        p = self._need_project()
        peq = self._peq(peq_id)
        if peq["status"] != "applied":
            raise ValueError("請先確認 PEQ 已套用並匯入補錄。")
        baseline = self._baseline(peq["baseline_version"])
        measurements = [m for m in p["measurements"] if m.get("role") == "verification" and m.get("applied_peq_id") == peq_id]
        if not measurements:
            raise ValueError("這版 PEQ 還沒有補錄。請選擇「匯入補錄」。")
        from .analysis import compare_verification
        result = clean(compare_verification(baseline["measurements"], measurements, peq, allow_mismatch=allow_mismatch))
        result["mismatch_acknowledged"] = bool(allow_mismatch)
        result["created_at"] = timestamp()
        result["measurement_ids"] = [m["id"] for m in measurements]
        peq["verification"] = result
        peq.setdefault("verification_history", []).append(copy.deepcopy(result))
        self._page = 2
        self._selected_peq_id = peq_id
        self._message = result.get("title", "補錄比較已完成。")
        self._message_kind = "success" if result.get("status") == "improved" else "warning"
        self._save("驗證 " + self._peq_label(peq), result.get("title", ""))

    @Slot(str, str)
    @guarded
    def exportPeq(self, peq_id, format):
        self._not_busy()
        peq = self._peq(peq_id)
        extension = "csv" if format == "csv" else "txt"
        path, _ = QFileDialog.getSaveFileName(None, "匯出完整 PEQ 設定", self._peq_label(peq) + "." + extension, f"{extension.upper()} (*.{extension})")
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
                        writer.writerow([self._peq_label(peq), peq["baseline_version"], channel, i+1, "PK", f["frequency"], f["gain"], f["q"], f.get("enabled", True), peq.get("preamp_db", 0)])
        else:
            lines = ["RoomPilot — 完整 PEQ 參數表", f"專案：{self.project['name']}", f"方案：{self._peq_label(peq)} / Baseline v{peq['baseline_version']}", "請以這份完整設定取代上一版；請勿再疊加上一版濾波器。", "未使用的 band 請停用。PK = Peak / Bell / Peak-Dip。", "這是通用參數表，依你的 DSP 輸入欄位手動套用。", f"Preamp: {peq.get('preamp_db',0):.2f} dB", ""]
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
            self._clear_candidates()
            self._explanation_cache = {}
            self._selected_measurement_id = self.project["measurements"][0]["id"] if self.project["measurements"] else ""
            self._selected_peq_id = self._latest_peq_id()
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
