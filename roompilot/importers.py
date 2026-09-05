"""Read-only REW measurement imports.

MDAT V2 is a Java *data* stream. javaobj-py3 parses it passively: no JVM is
started, no Java classes are loaded or executed, and no pickle is used. Only
the documented-in-code MeasData schema below is interpreted. Unknown schemas
fail with a REW text-export route instead of guessing a frequency response.

Array semantics were checked by static inspection of REW 5.31.3's own
getFrequencyResponse, indexToFreq and hasMeterCalFile methods. The five Cal
records were also confirmed in REW's GUI; text-export numeric cross-validation
remains a separate acceptance check and has not yet been completed.
splValues already contain the saved SPL/calibration adjustments: applying the
embedded mic curve or splCalOffset again would double-correct the measurement.
The saved smoothing is preserved and disclosed, not silently changed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import struct
from typing import Any
import uuid

import numpy as np


class MeasurementImportError(ValueError):
    """An actionable import error suitable for the UI."""


_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_POINTS = 4_194_304
_TEXT_FALLBACK = "請在 REW 開啟原檔，選擇 File → Export → Measurement as text，匯出 SPL 與 Phase（保留標頭），再匯入文字檔。"


def _fields(obj: Any) -> dict[str, Any]:
    """Extract only inert javaobj field dictionaries (never call Java code)."""
    return {field.name: value for fields in getattr(obj, "field_data", {}).values()
            for field, value in fields.items()}


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # javaobj's JavaString and boxed primitive beans are not Python primitives.
    if value.__class__.__name__ == "JavaString":
        return str(value)
    if value.__class__.__name__ == "JavaEnum":
        return str(value.value)
    fields = _fields(value)
    if "value" in fields:
        return _scalar(fields["value"])
    return None


def _number(value: Any) -> float | None:
    value = _scalar(value)
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _string(value: Any) -> str:
    result = _scalar(value)
    return result if isinstance(result, str) else ""


def _vector(value: Any, field: str) -> np.ndarray:
    if value is None or not hasattr(value, "__len__") or not 2 <= len(value) <= _MAX_POINTS:
        raise MeasurementImportError(f"{field} 的資料長度無效。{_TEXT_FALLBACK}")
    try:
        arr = np.asarray(value, dtype=np.float64)
    except (ValueError, TypeError, OverflowError) as exc:
        raise MeasurementImportError(f"{field} 不是有效的數值陣列。{_TEXT_FALLBACK}") from exc
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise MeasurementImportError(f"{field} 包含缺失或非有限數值。{_TEXT_FALLBACK}")
    return arr


def infer_channel(name: str, output_route: str = "") -> str:
    """A suggested annotation only; the user must confirm physical routing."""
    # REW Java route normally ends ', L, volume: ...' or ', L+R, ...'.
    route = re.search(r",\s*(L\+R|L|R)\s*(?:,|$)", output_route, re.I)
    if route:
        return route.group(1).upper().replace("L+R", "LR")
    normalized = name.upper().strip()
    if re.match(r"^(?:L\+R|LR|STEREO)(?=[\s_\-]|$)", normalized):
        return "LR"
    if re.match(r"^(?:LEFT|L)(?=[\s_\-]|$)", normalized):
        return "L"
    if re.match(r"^(?:RIGHT|R)(?=[\s_\-]|$)", normalized):
        return "R"
    return "Unknown"


def infer_position(name: str) -> str:
    match = re.search(r"(?:^|[\s_])P([+\-]?10|0)(?=[\s_]|$)", name.upper())
    if match:
        return {"10": "P+10", "+10": "P+10", "-10": "P-10", "0": "P0"}[match.group(1)]
    return "P0"


def _new_measurement(name: str, frequency: np.ndarray, spl: np.ndarray,
                     phase: np.ndarray | None, metadata: dict) -> dict:
    if len(frequency) != len(spl) or (phase is not None and len(phase) != len(spl)):
        raise MeasurementImportError(f"「{name}」的頻率、SPL 或相位長度不一致。{_TEXT_FALLBACK}")
    if not np.all(np.isfinite(frequency)) or np.any(frequency <= 0) or np.any(np.diff(frequency) <= 0):
        raise MeasurementImportError(f"「{name}」的頻率軸必須是遞增的正值。{_TEXT_FALLBACK}")
    result = {
        "id": str(uuid.uuid4()), "name": name, "channel": infer_channel(name, metadata.get("output_route", "")),
        "position": infer_position(name), "role": "baseline", "applied_peq_id": "",
        "frequency": frequency.tolist(), "spl": spl.tolist(), "metadata": metadata,
    }
    if phase is not None:
        result["phase"] = phase.tolist()
    return result


def _calibration(measurement_fields: dict) -> dict:
    """Per-measurement Mic/Meter Cal only; soundcard Cal is a separate field."""
    result = {"cal_status": "unknown", "cal_name": "", "cal_evidence": "未提供 Mic/Meter Cal 欄位"}
    if "meterCal" not in measurement_fields:
        return result
    obj = measurement_fields["meterCal"]
    if obj is None:
        return {**result, "cal_status": "missing", "cal_evidence": "這筆量測的 Mic/Meter Cal 欄位為空"}
    cal = _fields(obj)
    if not cal:
        return result
    serial = _scalar(cal.get("serialNum"))
    result.update(cal_name=_string(cal.get("sourceName")), cal_path=_string(cal.get("sourcePath")),
                  cal_serial=str(serial) if serial is not None else "")
    freq, gain = cal.get("freqArray"), cal.get("gainArray")
    # Some older schemas use frequency/gain lists. Same validation applies.
    if freq is None:
        freq = cal.get("freqList")
    if gain is None:
        gain = cal.get("gainList")
    if freq is None or gain is None or not hasattr(freq, "__len__") or not hasattr(gain, "__len__") or len(freq) == 0 or len(gain) == 0:
        has_name = bool(result["cal_name"] or result["cal_path"])
        result.update(cal_status="unknown" if has_name else "missing",
                      cal_evidence="有校正檔名稱，但未取得有效校正資料" if has_name else "這筆量測沒有麥克風校正曲線")
        if bool(cal.get("inverseCSelected", False)):
            result["cal_evidence"] += "（只有反向 C 加權，並非麥克風 Cal 檔）"
        return result
    try:
        f, g = _vector(freq, "Cal 頻率"), _vector(gain, "Cal 增益")
        valid = len(f) == len(g) and np.all(f > 0) and np.all(np.diff(f) > 0)
    except MeasurementImportError:
        valid = False
    if not valid:
        return {**result, "cal_evidence": "Mic/Meter Cal 曲線不完整或包含無效數值"}
    result.update(cal_status="loaded", cal_evidence="這筆量測包含有效的 Mic/Meter Cal 曲線及檔案紀錄",
                  cal_points=len(f), cal_min_hz=float(f[0]), cal_max_hz=float(f[-1]),
                  cal_fingerprint=hashlib.sha256(np.column_stack((f, g)).astype("<f8").tobytes()).hexdigest())
    sensitivity = _number(cal.get("sensitivitydB"))
    if sensitivity is not None:
        result["mic_sensitivity_db"] = sensitivity
    return result


def _native_metadata(fields: dict, path: Path) -> dict:
    ir = _fields(fields.get("irData"))
    sweep = _fields(fields.get("sweep"))
    metadata = {
        **_calibration(fields), "source_format": "REW MDAT V2", "source_file": str(path),
        "reader": "passive MeasData V2 / saved SPL", "amplitude_unit": "dB SPL",
        "input_device": _string(fields.get("sourceFileName")),
        "output_device": _string(fields.get("outputName")),
        "output_route": _string(fields.get("outputName")),
        "recording_description": _string(fields.get("sourceFileFormat")),
        "notes": _string(fields.get("measNotes")),
        "clipping": None, "headroom_db": None,
        "timing_reference": _string(fields.get("timingReferenceMode")),
        "rew_version": ".".join(str(int(v)) for v in (_number(fields.get("rewVersion")), _number(fields.get("rewSubVersion"))) if v is not None) + _string(fields.get("versionSt")),
        "cal_record_scope": "量測目前保存的 Cal 狀態；Cal 可在 REW 錄製後替換",
        "channel_inferred": True, "position_inferred": True,
        "source_type": int(fields.get("sourceType", 0)),
        "uses_embedded_cal": _scalar(fields.get("usesEmbeddedCalData")),
        "analysis": {
            "smoothing": "None" if not fields.get("octaveFrac") else f"1/{float(fields['octaveFrac']):g} octave (saved)",
            "smoothing_octave_fraction": _number(fields.get("octaveFrac")),
            "saved_response": True, "fdw_enabled": _scalar(ir.get("fdwEnabled")),
            "frequency_step_hz": _number(fields.get("freqStep")),
            "points_per_octave": _number(fields.get("ppo")),
        },
    }
    mappings = {
        "sample_rate": fields.get("sampleRate"), "sweep_length": sweep.get("totalSamples"),
        "sweep_level_dbfs": fields.get("sweepLevel"), "num_sweeps": fields.get("numSweeps"),
        "spl_cal_offset_db": fields.get("splCalOffset"),
        "input_volume": fields.get("inputVolume"), "output_volume": fields.get("outputVolume"),
        "valid_start_hz": fields.get("validStartFreq"), "valid_end_hz": fields.get("validEndFreq"),
        "snr_db": ir.get("signalToNoisedB"), "signal_to_distortion_db": ir.get("signalToDistdB"),
        "alignment_offset_db": fields.get("alignSPLOffsetCumulative"),
    }
    for key, value in mappings.items():
        val = _number(value)
        if val is not None:
            metadata[key] = val
    if "sweep_length" not in metadata:
        match = re.search(r"\b(\d+)k\b", metadata["recording_description"], re.I)
        if match:
            metadata["sweep_length"] = int(match.group(1)) * 1024
    timestamp = _number(fields.get("sourceFileDate"))
    if timestamp and 0 < timestamp < 253402300799000:
        metadata["recorded_at"] = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
    windows = {}
    for key, value in _fields(ir.get("windows")).items():
        simple = _scalar(value)
        if simple is not None and (not isinstance(simple, float) or math.isfinite(simple)):
            windows[key] = simple
    metadata["analysis"]["windows"] = windows
    soundcard = _fields(fields.get("scCal"))
    metadata["soundcard_cal_name"] = _string(soundcard.get("sourceName"))
    if not metadata["timing_reference"]:
        match = re.search(r"with\s+([^\r\n]+timing reference)", metadata["recording_description"], re.I)
        if match:
            metadata["timing_reference"] = match.group(1)
    return metadata


def _read_mdat(data: bytes, path: Path, progress=None, cancel=None) -> list[dict]:
    if not data.startswith(b"\xac\xed\x00\x05t\x00\x1cREW Measurement Data File V2"):
        raise MeasurementImportError(f"不支援此 MDAT 版本，或檔案不是完整的 REW MDAT V2。{_TEXT_FALLBACK}")
    try:
        import javaobj.v2 as javaobj
    except ImportError as exc:
        raise MeasurementImportError("缺少讀檔元件 javaobj-py3；請重新執行安裝程式。" + _TEXT_FALLBACK) from exc
    try:
        contents = javaobj.loads(data)
    except Exception as exc:
        raise MeasurementImportError(f"無法解析 MDAT；可能未完整下載、損壞或使用尚未支援的資料結構。{_TEXT_FALLBACK}") from exc
    if not isinstance(contents, list) or not contents or str(contents[0]) != "REW Measurement Data File V2":
        raise MeasurementImportError(f"MDAT 容器格式不支援。{_TEXT_FALLBACK}")
    objects = [obj for obj in contents if getattr(getattr(obj, "classdesc", None), "name", "") == "roomeqwizard.MeasData"]
    if not objects:
        raise MeasurementImportError(f"檔案未包含可讀取的 MeasData 量測。{_TEXT_FALLBACK}")
    # Container V2 writes version (two ints), notes, then measurement count.
    # Reject missing/extra records rather than importing a truncated baseline.
    count_block = getattr(contents[3], "data", b"") if len(contents) > 3 else b""
    if len(count_block) != 4 or struct.unpack(">i", count_block)[0] != len(objects):
        raise MeasurementImportError(f"MDAT 的量測數量與容器紀錄不符，或使用尚未支援的群組格式。{_TEXT_FALLBACK}")
    result = []
    for index, obj in enumerate(objects):
        if cancel and cancel():
            raise MeasurementImportError("已取消匯入。")
        fields = _fields(obj)
        required = {"splValues", "startFreq", "endFreq", "dataLength", "isLogSpaced", "sourceType"}
        if not required.issubset(fields):
            raise MeasurementImportError(f"第 {index + 1} 筆量測使用未支援的 MeasData 結構。{_TEXT_FALLBACK}")
        if int(fields["sourceType"]) & 512:
            raise MeasurementImportError(f"第 {index + 1} 筆是阻抗量測，不能作為空間 PEQ 的 SPL 曲線。請只匯出聲壓量測。")
        name = _string(fields.get("shortDesc")) or f"{path.stem} {index + 1}"
        spl = _vector(fields["splValues"], f"{name} SPL")
        if len(spl) != fields["dataLength"]:
            raise MeasurementImportError(f"「{name}」儲存的樣本數不一致。{_TEXT_FALLBACK}")
        start, end = _number(fields["startFreq"]), _number(fields["endFreq"])
        if start is None or end is None or start <= 0 or end <= start:
            raise MeasurementImportError(f"「{name}」的起迄頻率無效。{_TEXT_FALLBACK}")
        indices = np.arange(len(spl), dtype=np.float64)
        if fields["isLogSpaced"]:
            log_step = _number(fields.get("logStepLog"))
            if log_step is None or log_step <= 0:
                raise MeasurementImportError(f"「{name}」缺少有效的對數頻率間隔。{_TEXT_FALLBACK}")
            with np.errstate(over="ignore"):
                frequency = start * np.exp(indices * log_step)
            tolerance = max(0.05, end * 1e-5)
        else:
            step = _number(fields.get("freqStep"))
            if step is None or step <= 0:
                raise MeasurementImportError(f"「{name}」缺少有效的頻率間隔。{_TEXT_FALLBACK}")
            frequency = start + indices * step
            tolerance = max(0.05, step * 0.1, end * 1e-6)
        if not math.isclose(float(frequency[-1]), end, rel_tol=0, abs_tol=tolerance):
            raise MeasurementImportError(f"「{name}」的頻率軸與檔案結束頻率不符。{_TEXT_FALLBACK}")
        phase = _vector(fields["phaseValues"], f"{name} Phase") if fields.get("phaseValues") is not None else None
        measurement = _new_measurement(name, frequency, spl, phase, _native_metadata(fields, path))
        result.append(measurement)
        if progress:
            progress((index + 1) / len(objects))
    return result


def _decode_text(data: bytes) -> tuple[str, str]:
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp950", "cp1252")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                raise UnicodeError("binary data")
            return text, encoding
        except UnicodeError:
            continue
    raise MeasurementImportError("無法辨識文字檔編碼；請從 REW 重新匯出 UTF-8 的 Measurement as text。")


def _read_text(data: bytes, path: Path, progress=None, cancel=None) -> list[dict]:
    text, encoding = _decode_text(data)
    headers: list[str] = []
    rows: list[list[float]] = []
    discarded_dc = False
    phase_present = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        if cancel and line_number % 1024 == 0 and cancel():
            raise MeasurementImportError("已取消匯入。")
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("*", "#", "//", ";")):
            headers.append(line.lstrip("*#;/ "))
            continue
        # REW supports tab/space/comma separators. Semicolon/tab additionally
        # allow locale decimal commas without confusing them with CSV columns.
        if ";" in line or "\t" in line:
            parts = re.split(r"[;\t]+", line)
            parts = [part.strip().replace(",", ".") for part in parts]
        elif "," in line:
            parts = [part.strip() for part in line.split(",")]
        else:
            parts = line.split()
        try:
            values = [float(part) for part in parts]
        except ValueError:
            if not rows and not re.match(r"^[+\-]?(?:\d|\.\d)", line):
                headers.append(line)
                continue
            raise MeasurementImportError(f"第 {line_number} 行包含無法辨識的數值；請匯出單筆量測的 Frequency、SPL、Phase。")
        if len(values) not in (2, 3):
            raise MeasurementImportError(f"第 {line_number} 行有 {len(values)} 欄；請匯出單筆量測的 Frequency、SPL 與選填 Phase（2 或 3 欄）。")
        if not all(math.isfinite(value) for value in values):
            raise MeasurementImportError(f"第 {line_number} 行含 NaN 或 Infinity；請檢查 REW 原始量測。")
        has_phase = len(values) == 3
        if phase_present is not None and has_phase != phase_present:
            raise MeasurementImportError(f"第 {line_number} 行的欄數與其他資料不一致。")
        phase_present = has_phase
        if values[0] == 0:
            discarded_dc = True
            continue
        rows.append(values)
        if len(rows) > _MAX_POINTS:
            raise MeasurementImportError("文字資料點數過多；請使用 REW 匯出較小的分析頻段。")
    if len(rows) < 2:
        raise MeasurementImportError("找不到至少兩筆有效的 Frequency / SPL 資料。" + _TEXT_FALLBACK)
    header = "\n".join(headers)
    if re.search(r"(?:Freq(?:uency)?\s*\([^)]*\)|SPL|Magnitude).*?(?:dBFS|Ohm|Volt|impedance)", header, re.I):
        raise MeasurementImportError("此文字檔的單位不是 SPL；請在 REW 選擇 dB SPL 後重新匯出。")
    metadata = {"cal_status": "unknown", "cal_name": "", "clipping": None, "headroom_db": None,
                "source_format": "REW text", "source_file": str(path), "encoding": encoding,
                "amplitude_unit": "dB SPL", "header_text": header,
                "notes": "文字檔僅包含匯出的曲線與標頭，未記錄的量測條件無法驗證。",
                "channel_inferred": True, "position_inferred": True,
                "analysis": {"smoothing": "unknown", "saved_response": True}}
    if discarded_dc:
        metadata["notes"] += " 已略過 0 Hz 的 DC 資料點。"
    name = path.stem
    for line in headers:
        match = re.match(r"([^:]+):\s*(.*)", line)
        if not match:
            continue
        key, value = match.group(1).strip().lower(), match.group(2).strip()
        if key in {"measurement", "measurement name", "measurement title", "title"} and value:
            name = value
        elif key in {"dated", "date", "recorded at"}:
            metadata["recorded_at"] = value
        elif key in {"input", "input device", "source"}:
            metadata["input_device"] = value
        elif key in {"output", "output device"}:
            metadata["output_device"] = value
            metadata["output_route"] = value
        elif key in {"mic/meter cal file", "mic cal file", "microphone calibration file", "mic/meter cal"}:
            missing = not value or value.lower() in {"none", "no cal file", "not loaded", "n/a", "(none)"}
            metadata.update(cal_status="missing" if missing else "loaded", cal_name="" if missing else value,
                            cal_evidence="文字標頭的 Mic/Meter Cal 記錄（無內嵌曲線可驗證）")
        elif key == "smoothing":
            metadata["analysis"]["smoothing"] = value
        elif key == "format":
            metadata["recording_description"] = value
            length = re.search(r"\b(\d+)k\b", value, re.I)
            level = re.search(r"at\s+([+\-]?[\d.]+)\s*dBFS", value, re.I)
            sweeps = re.search(r"\b(\d+)\s+sweeps?\b", value, re.I)
            if length:
                metadata["sweep_length"] = int(length.group(1)) * 1024
            if level:
                metadata["sweep_level_dbfs"] = float(level.group(1))
            if sweeps:
                metadata["num_sweeps"] = int(sweeps.group(1))
        elif key == "note":
            metadata["measurement_notes"] = value
        elif key in {"sample rate", "sample rate (hz)", "samplerate"}:
            rate = re.search(r"([\d.]+)\s*(k?hz)?", value, re.I)
            if rate:
                metadata["sample_rate"] = float(rate.group(1)) * (1000 if (rate.group(2) or "").lower() == "khz" else 1)
    arr = np.asarray(rows, dtype=float)
    measurement = _new_measurement(name, arr[:, 0], arr[:, 1], arr[:, 2] if phase_present else None, metadata)
    if progress:
        progress(1.0)
    return [measurement]


def import_measurements(path: str, **options) -> list[dict]:
    """Import .mdat, .txt, .csv or .tsv. Source bytes are never changed.

    progress: callable(float 0..1). cancel: callable() -> bool.
    role, applied_peq_id and position may supply the project import context.
    Native V2 import requires javaobj-py3, not a running REW installation.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MeasurementImportError(f"找不到量測檔：{source.name}")
    if source.suffix.lower() not in {".mdat", ".txt", ".csv", ".tsv"}:
        raise MeasurementImportError("請選擇 REW .mdat，或匯出的 .txt / .csv / .tsv 頻響檔。")
    try:
        size = source.stat().st_size
        if size == 0 or size > _MAX_FILE_BYTES:
            raise MeasurementImportError("檔案為空或超過 256 MB；請在 REW 分批保存量測後再匯入。")
        data = source.read_bytes()
    except OSError as exc:
        raise MeasurementImportError(f"無法讀取 {source.name}；請確認檔案已下載且有讀取權限。") from exc
    if options.get("cancel") and options["cancel"]():
        raise MeasurementImportError("已取消匯入。")
    reader = _read_mdat if source.suffix.lower() == ".mdat" else _read_text
    try:
        measurements = reader(data, source, options.get("progress"), options.get("cancel"))
    except MeasurementImportError:
        raise
    except (TypeError, ValueError, KeyError, IndexError, OverflowError, AttributeError) as exc:
        raise MeasurementImportError(f"量測資料結構不完整或尚未支援。{_TEXT_FALLBACK}") from exc
    source_hash = hashlib.sha256(data).hexdigest()
    for measurement in measurements:
        for key in ("role", "applied_peq_id", "position"):
            if key in options:
                measurement[key] = options[key]
        measurement["metadata"]["source_sha256"] = source_hash
    return measurements
