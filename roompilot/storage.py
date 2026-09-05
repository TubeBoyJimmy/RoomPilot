"""Atomic local projects and portable archives. No pickle or executable content."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
import uuid
import zipfile
import zlib

SCHEMA_VERSION = 1
MAX_BUNDLE_SIZE = 1_000_000_000
MAX_JSON_SIZE = 150_000_000


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_data_dir() -> Path:
    override = os.environ.get("ROOMPILOT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "RoomPilot"


def add_history(project: dict, title: str, detail: str = "") -> None:
    project.setdefault("history", []).insert(0, {"time": timestamp(), "title": title, "detail": detail})


class ProjectStore:
    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory or default_data_dir()).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.directory / "projects"
        self.assets_dir.mkdir(exist_ok=True)
        self.connection = sqlite3.connect(self.directory / "projects.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL, data BLOB NOT NULL)")
        self.connection.commit()

    def list_projects(self) -> list[dict]:
        return [dict(id=row[0], name=row[1], updated_at=row[2]) for row in self.connection.execute("SELECT id,name,updated_at FROM projects ORDER BY updated_at DESC")]

    def create(self, name: str, notes: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("請輸入專案名稱。")
        project = {"schema_version": SCHEMA_VERSION, "id": str(uuid.uuid4()), "name": name[:160], "notes": notes[:10000], "created_at": timestamp(), "updated_at": timestamp(), "settings": {"checklist": {}, "mic_orientation": "尚未確認", "input_device": "", "output_device": "", "peq_destination": "", "volume_note": "", "route_note": ""}, "measurements": [], "baselines": [], "baseline_ids": [], "baseline_version": 0, "peqs": [], "history": [], "sources": [], "current_applied_peq_id": ""}
        add_history(project, "建立空間專案", project["name"])
        self.save(project)
        return project

    def save(self, project: dict) -> None:
        project["updated_at"] = timestamp()
        encoded = json.dumps(project, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.connection.execute("INSERT INTO projects(id,name,updated_at,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at,data=excluded.data", (project["id"], project["name"], project["updated_at"], zlib.compress(encoded)))
        self.connection.commit()

    def load(self, project_id: str) -> dict:
        row = self.connection.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise ValueError("找不到這個專案。")
        return json.loads(zlib.decompress(row[0]))

    def preserve_source(self, project: dict, source: str | Path, expected_sha256: str | None = None) -> dict:
        path = Path(source).resolve()
        # Hash and preserve exactly the same bytes even if REW saves concurrently.
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise ValueError("原始檔在讀取期間已變更。請先完成 REW 存檔，再重新匯入。")
        existing = next((item for item in project["sources"] if item["sha256"] == digest), None)
        if existing:
            return existing
        folder = self.assets_dir / project["id"] / "sources"
        folder.mkdir(parents=True, exist_ok=True)
        relative = "sources/" + digest[:16] + path.suffix.lower()
        target = self.assets_dir / project["id"] / relative
        if not target.exists():
            handle, temporary = tempfile.mkstemp(prefix="source-", dir=folder)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                os.replace(temporary, target)
            finally:
                if Path(temporary).exists():
                    Path(temporary).unlink()
        record = {"name": path.name, "relative_path": relative, "sha256": digest, "bytes": len(payload)}
        project["sources"].append(record)
        return record

    def snapshot_baseline(self, project: dict, ids: list[str], quality: list, acknowledged: bool) -> dict:
        selected = [copy.deepcopy(m) for m in project["measurements"] if m["id"] in ids]
        if len(selected) != len(set(ids)):
            raise ValueError("量測選取已改變，請重新選擇 Baseline。")
        version = len(project["baselines"]) + 1
        result = {"version": version, "created_at": timestamp(), "measurement_ids": list(ids), "measurements": selected, "settings": copy.deepcopy(project["settings"]), "quality": copy.deepcopy(quality), "warnings_acknowledged": acknowledged}
        project["baselines"].append(result)
        project["baseline_ids"] = list(ids)
        project["baseline_version"] = version
        add_history(project, f"建立 Baseline v{version}", f"保存 {len(ids)} 筆量測與設定快照。")
        self.save(project)
        return result

    def export_bundle(self, project: dict, destination: str | Path) -> None:
        destination = Path(destination)
        if destination.suffix.lower() != ".roompilot":
            destination = destination.with_suffix(".roompilot")
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix="roompilot-", suffix=".tmp", dir=destination.parent)
        os.close(handle)
        try:
            with zipfile.ZipFile(temp_name, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(project, ensure_ascii=False, allow_nan=False))
                archive.writestr("manifest.json", json.dumps({"format": "RoomPilot", "schema_version": SCHEMA_VERSION, "exported_at": timestamp()}))
                for source in project.get("sources", []):
                    relative = PurePosixPath(source["relative_path"])
                    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "sources":
                        raise ValueError("原始檔路徑無效。")
                    original = self.assets_dir / project["id"] / Path(relative)
                    if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
                        raise ValueError(f"原始檔遺失或已變更：{source['name']}")
                    archive.write(original, str(relative))
            os.replace(temp_name, destination)
        finally:
            if Path(temp_name).exists():
                Path(temp_name).unlink()

    def import_bundle(self, filename: str | Path) -> dict:
        with zipfile.ZipFile(filename) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(item.file_size for item in infos) > MAX_BUNDLE_SIZE:
                raise ValueError("專案封存檔過大。")
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise ValueError("專案封存檔含重複項目。")
            for name in names:
                parts = PurePosixPath(name.replace("\\", "/"))
                if parts.is_absolute() or ".." in parts.parts or ":" in name:
                    raise ValueError("專案封存檔含無效路徑。")
            if archive.getinfo("project.json").file_size > MAX_JSON_SIZE:
                raise ValueError("專案資料過大。")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "RoomPilot" or manifest.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("不支援此專案版本。")
            project = json.loads(archive.read("project.json"))
            self._validate_project(project)
            # Import as an independent copy so existing local history cannot be overwritten.
            project["id"] = str(uuid.uuid4())
            project["name"] = project["name"] + "（匯入）"
            source_bytes = []
            for source in project.get("sources", []):
                relative = PurePosixPath(source["relative_path"])
                if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "sources" or ":" in str(relative) or "\\" in str(relative):
                    raise ValueError("無效的原始檔路徑。")
                data = archive.read(str(relative))
                if hashlib.sha256(data).hexdigest() != source["sha256"]:
                    raise ValueError("原始檔校驗失敗。")
                source_bytes.append((relative, data))
            for relative, data in source_bytes:
                target = self.assets_dir / project["id"] / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            add_history(project, "匯入專案副本", "原始量測、Baseline 與 PEQ 版本已保留。")
            self.save(project)
            return project

    @staticmethod
    def _validate_project(project: dict) -> None:
        """Validate a whole archive graph before copying assets or saving it.

        Baseline snapshots are intentionally checked independently of mutable
        measurement annotations. Their IDs must still exist, but changing a
        live position/channel must not rewrite or invalidate historic snapshots.
        """
        if not isinstance(project, dict) or project.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("無效的專案資料。")
        if not isinstance(project.get("name"), str) or not project["name"].strip():
            raise ValueError("專案缺少名稱。")
        if not isinstance(project.get("id"), str) or not project["id"]:
            raise ValueError("專案識別碼無效。")
        for key in ("measurements", "baselines", "baseline_ids", "peqs", "history", "sources"):
            if not isinstance(project.get(key), list):
                raise ValueError(f"專案資料不完整：{key}")
        if not isinstance(project.get("settings"), dict):
            raise ValueError("專案設定無效。")

        def finite(value):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            try:
                return math.isfinite(value)
            except OverflowError:
                return False

        def unique_ids(items, label, *, dictionaries=True):
            ids = []
            for item in items:
                value = item.get("id") if dictionaries and isinstance(item, dict) else item if not dictionaries else None
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{label}識別碼無效。")
                ids.append(value)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label}識別碼重複。")
            return set(ids)

        def arrays(item, label):
            if not isinstance(item, dict):
                raise ValueError(f"{label}資料格式無效。")
            f, y, phase = item.get("frequency"), item.get("spl"), item.get("phase")
            if not isinstance(f, list) or not isinstance(y, list) or len(f) != len(y) or len(f) < 2:
                raise ValueError(f"{label}頻率與聲壓長度不一致或不足。")
            if not all(finite(v) for v in f + y) or any(v <= 0 for v in f) or any(b <= a for a, b in zip(f, f[1:])):
                raise ValueError(f"{label}必須包含有限聲壓與遞增的正頻率。")
            if phase is not None and (not isinstance(phase, list) or len(phase) != len(f) or not all(finite(v) for v in phase)):
                raise ValueError(f"{label}相位資料格式無效。")

        def measurement(m, label):
            arrays(m, label)
            if not isinstance(m.get("name"), str) or m.get("channel") not in ("L", "R", "LR", "Unknown") or m.get("position") not in ("P0", "P-10", "P+10") or m.get("role") not in ("baseline", "verification"):
                raise ValueError(f"{label}的名称、聲道、位置或用途無效。")
            if not isinstance(m.get("metadata"), dict):
                raise ValueError(f"{label}的量測資訊無效。")

        measurement_ids = unique_ids(project["measurements"], "量測")
        for m in project["measurements"]:
            measurement(m, "量測")

        versions = {}
        snapshot_measurements = []
        for baseline in project["baselines"]:
            version = baseline.get("version") if isinstance(baseline, dict) else None
            if type(version) is not int or version < 1 or version in versions:
                raise ValueError("Baseline 版本無效或重複。")
            ids, records = baseline.get("measurement_ids"), baseline.get("measurements")
            if not isinstance(ids, list) or not isinstance(records, list) or not records:
                raise ValueError("Baseline 缺少量測快照。")
            selected_ids = unique_ids(ids, "Baseline 量測", dictionaries=False)
            saved_ids = unique_ids(records, "Baseline 快照")
            if selected_ids != saved_ids or not saved_ids.issubset(measurement_ids):
                raise ValueError("Baseline 的量測識別碼與保存快照不符。")
            for m in records:
                measurement(m, "Baseline 快照")
                if m.get("role") != "baseline" or m.get("applied_peq_id"):
                    raise ValueError("Baseline 快照含 PEQ 後補錄，不能作為未校正基準。")
            if not isinstance(baseline.get("settings"), dict) or not isinstance(baseline.get("quality"), list):
                raise ValueError("Baseline 的設定或品質紀錄無效。")
            versions[version] = selected_ids
            snapshot_measurements.extend(records)
        # New versions are allocated as len(baselines)+1, so gaps must be rejected.
        if set(versions) != set(range(1, len(versions) + 1)):
            raise ValueError("Baseline 版本序列不完整。")
        active = project.get("baseline_version")
        selected_ids = unique_ids(project["baseline_ids"], "目前 Baseline", dictionaries=False)
        if type(active) is not int or active < 0 or (active == 0 and selected_ids) or (active > 0 and (active not in versions or selected_ids != versions[active])):
            raise ValueError("目前 Baseline 版本或量測參照不存在。")

        peq_ids = unique_ids(project["peqs"], "PEQ")
        peqs = {p["id"]: p for p in project["peqs"]}
        for peq in project["peqs"]:
            if type(peq.get("baseline_version")) is not int or peq["baseline_version"] not in versions:
                raise ValueError("PEQ 參照的 Baseline 版本不存在。")
            if peq.get("status") not in ("draft", "applied") or not isinstance(peq.get("name"), str) or not isinstance(peq.get("settings"), dict):
                raise ValueError("PEQ 的名稱、套用狀態或設定無效。")
            previous = peq.get("replaces_peq_id", "")
            if not isinstance(previous, str) or (previous and (previous not in peq_ids or previous == peq["id"])):
                raise ValueError("PEQ 的前一版本參照無效。")
            extension = peq.get("extension_source_id")
            if extension is not None and (not isinstance(extension, str) or extension not in peq_ids or extension == peq["id"]):
                raise ValueError("PEQ 的保留擴充來源無效。")
            if peq.get("deleted_at") is not None and not isinstance(peq["deleted_at"], str):
                raise ValueError("PEQ 的回收區紀錄無效。")
            filters = peq.get("filters")
            if not isinstance(filters, dict) or not filters or not set(filters).issubset({"Shared", "L", "R"}) or ("Shared" in filters and len(filters) != 1):
                raise ValueError("PEQ 的聲道濾波器設定無效。")
            for bands in filters.values():
                if not isinstance(bands, list) or len(bands) > 20:
                    raise ValueError("PEQ Band 資料無效。")
                for band in bands:
                    if not isinstance(band, dict) or not all(finite(band.get(key)) for key in ("frequency", "gain", "q")) or band["frequency"] <= 0 or band["q"] <= 0 or type(band.get("enabled", True)) is not bool or band.get("type", "PK") != "PK":
                        raise ValueError("PEQ 濾波器參數無效。")
            if not finite(peq.get("target_level")) or not finite(peq.get("preamp_db", 0)):
                raise ValueError("PEQ 目標或前級數值無效。")
            curves = peq.get("curves", [])
            if not isinstance(curves, list):
                raise ValueError("PEQ 曲線資料無效。")
            for curve in curves:
                arrays(curve, "PEQ 曲線")
            checks = peq.get("verification_history", [])
            if not isinstance(checks, list) or not isinstance(peq.get("verification", {}), dict):
                raise ValueError("PEQ 驗證紀錄格式無效。")
            for check in checks + [peq.get("verification", {})]:
                if not isinstance(check, dict):
                    raise ValueError("PEQ 驗證紀錄格式無效。")
                refs = check.get("measurement_ids", [])
                if not isinstance(refs, list) or not unique_ids(refs, "驗證量測", dictionaries=False).issubset(measurement_ids):
                    raise ValueError("PEQ 驗證紀錄參照的量測不存在。")
                check_curves = check.get("curves", [])
                if not isinstance(check_curves, list):
                    raise ValueError("PEQ 驗證曲線格式無效。")
                for curve in check_curves:
                    arrays(curve, "PEQ 驗證曲線")
        current = project.get("current_applied_peq_id", "")
        if not isinstance(current, str) or (current and (current not in peqs or peqs[current]["status"] != "applied")):
            raise ValueError("目前套用的 PEQ 參照不存在或尚未套用。")
        for m in project["measurements"]:
            ref = m.get("applied_peq_id", "")
            if not isinstance(ref, str) or (m["role"] == "baseline" and ref) or (m["role"] == "verification" and (ref not in peqs or peqs[ref]["status"] != "applied")):
                raise ValueError("補錄量測未對應有效且已套用的 PEQ 版本。")

        paths, source_hashes = set(), set()
        for source in project["sources"]:
            if not isinstance(source, dict) or not isinstance(source.get("relative_path"), str) or not isinstance(source.get("name"), str):
                raise ValueError("原始檔紀錄無效。")
            name = source["relative_path"]
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2 or relative.parts[0] != "sources" or ":" in name or "\\" in name or name in paths or str(relative) != name:
                raise ValueError("無效或重複的原始檔路徑。")
            digest = source.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or digest in source_hashes:
                raise ValueError("原始檔校驗碼無效或重複。")
            if type(source.get("bytes")) is not int or source["bytes"] < 0:
                raise ValueError("原始檔大小紀錄無效。")
            paths.add(name)
            source_hashes.add(digest)
        for m in project["measurements"] + snapshot_measurements:
            ref = m.get("source_sha256")
            if ref is not None and (not isinstance(ref, str) or ref not in source_hashes):
                raise ValueError("量測参照的原始檔紀錄不存在。")
        for event in project["history"]:
            if not isinstance(event, dict) or not all(isinstance(event.get(key), str) for key in ("time", "title", "detail")):
                raise ValueError("專案歷史紀錄格式無效。")
        json.dumps(project, allow_nan=False)

    def close(self):
        self.connection.close()
