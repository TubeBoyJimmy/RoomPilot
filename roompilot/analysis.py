"""Conservative, reproducible PEQ analysis of immutable REW measurements.

This module predicts magnitude responses; it does not capture/play audio and does
not claim an EQ prediction is a verified acoustic result. RBJ peaking coefficients:
https://www.w3.org/TR/audio-eq-cookbook/ . Every reported score uses the rounded,
device-enterable filters, evaluated on a logarithmic frequency grid in float64.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
import time
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks

ALGORITHM_VERSION = "roompilot-peq-4.0"
DEFAULTS = dict(bands=5, independent=True, f_min=30.0, f_max=200.0,
                max_cut=6.0, max_total_cut=9.0, min_q=0.4, max_q=6.0,
                gain_step=0.1, freq_step=1.0, q_step=0.01,
                target_level=None, mode="deep", allow_boost=False,
                max_boost=3.0, allow_extended=False, sample_rate=48000.0,
                target_ref_min=80.0, target_ref_max=200.0,
                strategy="bass_first", bass_drift_limit_db=0.5, overshoot_scale=1.0,
                objective_mode="legacy", low_cut_limit_db=1.0,
                supports_preamp=True, preamp_margin_db=0.5, max_total_boost=3.0)
GRID_PPO = 192


def _number(value: Any) -> bool:
    return isinstance(value, (int, float, np.number)) and not isinstance(value, bool) and math.isfinite(float(value))


def _arrays(m: dict) -> tuple[np.ndarray, np.ndarray]:
    try:
        f = np.asarray(m.get("frequency", []), dtype=np.float64)
        y = np.asarray(m.get("spl", []), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("頻響包含無法讀取的數值。") from exc
    if f.ndim != 1 or y.ndim != 1 or len(f) != len(y) or len(f) < 8:
        raise ValueError("頻率與 SPL 陣列長度不一致，或有效資料少於 8 點。")
    if not np.all(np.isfinite(f)) or not np.all(np.isfinite(y)):
        raise ValueError("頻響包含 NaN 或無限值，請重新匯出量測。")
    if np.any(f <= 0) or np.any(np.diff(f) <= 0):
        raise ValueError("頻率必須為正值並嚴格遞增，不可包含重複頻率。")
    return f, y


def _item(code, level, title, detail, measurements):
    return dict(code=code, level=level, title=title, detail=detail,
                blocking=level == "error" and code in ("empty", "invalid_arrays", "coverage"),
                measurement_ids=[str(m.get("id", "")) for m in measurements])


def _covers(f, lo, hi):
    # REW FFT bins seldom land exactly on the requested sweep boundary. Accept
    # at most one adjacent bin, capped to 1%, then *clamp* the analysis domain.
    tolerance_lo = min(float(f[1] - f[0]), lo * .01) + 1e-9
    tolerance_hi = min(float(f[-1] - f[-2]), hi * .01) + 1e-9
    return f[0] <= lo + tolerance_lo and f[-1] >= hi - tolerance_hi


def _pairwise_rms(ys):
    """RMS of all unique same-position measurement differences, without centering."""
    n = len(ys)
    return float(np.sqrt(2 * n / (n - 1) * np.mean(np.var(ys, axis=0)))) if n > 1 else None


def quality_report(measurements, settings=None) -> list[dict]:
    """Evidence-based checks. Absence of metadata is never a pass."""
    measurements = list(measurements)
    report = []
    valid = []
    if not measurements:
        return [_item("empty", "error", "尚無量測", "請匯入左右聲道的獨立量測。", [])]
    wanted = settings or {}
    for m in measurements:
        try:
            f, _ = _arrays(m)
            valid.append(m)
            report.append(_item("frequency_data", "pass", "頻響資料可讀取", f"共 {len(f):,} 點，涵蓋 {f[0]:.1f}–{f[-1]:.0f} Hz。", [m]))
            lo, hi = float(wanted.get("f_min", 30)), float(wanted.get("f_max", 200))
            if not _covers(f, lo, hi):
                report.append(_item("coverage", "error" if "f_min" in wanted or "f_max" in wanted else "warning", "未涵蓋校正頻段", f"需求為 {lo:g}–{hi:g} Hz；請補錄或縮小校正範圍。", [m]))
        except (ValueError, TypeError) as exc:
            report.append(_item("invalid_arrays", "error", "頻響資料無效", str(exc), [m]))
        md = m.get("metadata") or {}
        cal = md.get("cal_status", "unknown")
        if cal == "loaded":
            report.append(_item("mic_cal", "pass", "已記錄麥克風 Cal", str(md.get("cal_name") or "量測記錄包含麥克風校正資訊") + "。請確認檔案序號與麥克風朝向。", [m]))
            cal_lo, cal_hi = md.get("cal_min_hz"), md.get("cal_max_hz")
            if _number(cal_lo) and _number(cal_hi) and (cal_lo > float(wanted.get("f_min", 30)) or cal_hi < float(wanted.get("f_max", 200))):
                report.append(_item("cal_coverage", "warning", "Cal 未涵蓋校正頻段", f"校正資料範圍為 {cal_lo:g}–{cal_hi:g} Hz；請確認適用性。", [m]))
        elif cal == "missing":
            report.append(_item("mic_cal", "warning", "量測未載入麥克風 Cal", "請在 REW 確認並補上適當校正資料，或確認限制後再指定 Baseline。", [m]))
        else:
            report.append(_item("mic_cal", "unknown", "無法確認麥克風 Cal", "來源未提供可確認的逐筆 Cal 資訊；請查看 REW 的這筆量測。", [m]))
        clipping = md.get("clipping")
        if clipping is True:
            report.append(_item("clipping", "warning", "記錄顯示 Clipping", "強烈建議降低錄製或播放電平並重新量測；可確認限制後繼續分析，結果不代表錄製品質合格。", [m]))
        elif clipping is False:
            report.append(_item("clipping", "pass", "未記錄到 Clipping", "來源的過載旗標為否；仍需確認播放鏈路沒有其他失真。", [m]))
        else:
            report.append(_item("clipping", "unknown", "無法驗證 Clipping", "缺少原始輸入過載資訊，不能由頻響或正規化 IR 峰值推斷。", [m]))
        headroom = md.get("headroom_db")
        if _number(headroom):
            report.append(_item("headroom", "warning" if float(headroom) < 6 else "pass", "輸入餘裕", f"記錄的 headroom 為 {float(headroom):.1f} dB。" + ("低於 6 dB，建議降低電平再量測。" if float(headroom) < 6 else ""), [m]))
        else:
            report.append(_item("headroom", "unknown", "輸入餘裕未提供", "此資料不能確認錄製時的輸入峰值。", [m]))
        sweep_keys = ("sample_rate", "sweep_length", "sweep_level_dbfs")
        missing = [k for k in sweep_keys if md.get(k) is None]
        problems = []
        if md.get("sample_rate") is not None and (not _number(md["sample_rate"]) or not 8000 <= md["sample_rate"] <= 768000):
            problems.append("取樣率不合理")
        if md.get("sweep_length") is not None and (not _number(md["sweep_length"]) or md["sweep_length"] < 1024):
            problems.append("掃頻長度需確認")
        if md.get("sweep_level_dbfs") is not None and (not _number(md["sweep_level_dbfs"]) or not -100 <= md["sweep_level_dbfs"] <= 0):
            problems.append("掃頻輸出 dBFS 不合理")
        if problems:
            report.append(_item("sweep_settings", "warning", "量測參數需要確認", "、".join(problems) + "。", [m]))
        elif missing:
            report.append(_item("sweep_settings", "unknown", "量測參數不完整", "未提供：" + "、".join(missing) + "；不代表量測錯誤。", [m]))
        else:
            report.append(_item("sweep_settings", "pass", "量測參數可讀取", f"{md['sample_rate']:g} Hz，{md['sweep_length']:g} samples，{md['sweep_level_dbfs']:g} dBFS；合理數值不代表實際聲壓已校正。", [m]))
        if md.get("spl_calibrated") is True:
            report.append(_item("spl_calibration", "pass", "有 SPL 校正紀錄", "仍需使用者確認校正程序與音量設定。", [m]))
        else:
            report.append(_item("spl_calibration", "unknown", "絕對 SPL 校正未確認", "麥克風頻響 Cal 與絕對聲壓校正不同；此處無法確認現場 dB SPL。", [m]))
        if md.get("noise_floor_db") is None and md.get("snr_db") is None:
            report.append(_item("noise", "unknown", "環境噪音資料未提供", "A/B 一致性只能協助發現差異，不能排除兩次都含有相同噪音。", [m]))
        elif _number(md.get("snr_db")):
            snr = float(md["snr_db"])
            report.append(_item("noise", "warning" if snr < 30 else "pass", "訊噪比記錄", f"來源記錄 SNR {snr:.1f} dB；30 dB 提醒門檻為本程式起始設定。", [m]))
        else:
            report.append(_item("noise", "unknown", "只有噪音底資料", "缺少可比較的訊噪比，請確認量測環境。", [m]))
    p0 = [m for m in valid if m.get("position", "P0") == "P0" and m.get("channel") in ("L", "R")]
    channels = {m.get("channel") for m in p0}
    if channels != {"L", "R"}:
        report.append(_item("channel_pair", "warning", "左右獨立量測尚未齊全", "建議 P0 各錄左右聲道；L+R 不能代替左右獨立量測。", valid))
    for key, label in (("sample_rate", "取樣率"), ("sweep_length", "掃頻長度"), ("sweep_level_dbfs", "掃頻輸出"), ("input_device", "輸入裝置"), ("output_device", "播放裝置"), ("cal_name", "麥克風 Cal"), ("cal_fingerprint", "麥克風 Cal 內容"), ("output_volume", "REW 輸出音量")):
        values = {str((m.get("metadata") or {}).get(key)) for m in p0 if (m.get("metadata") or {}).get(key) not in (None, "")}
        if len(values) > 1:
            report.append(_item("inconsistent_" + key, "warning", label + "不一致", "同組量測包含不同設定，請確認是否切換設定後未還原。", p0))
    groups = defaultdict(list)
    for m in valid:
        groups[(m.get("channel"), m.get("position", "P0"))].append(m)
    for (ch, pos), ms in groups.items():
        if ch not in ("L", "R"):
            continue
        if len(ms) < 2:
            report.append(_item("repeatability", "unknown", f"{ch} · {pos} 缺少重錄", "同位置兩筆独立量測可檢查重現性。", ms))
        else:
            low = max(_arrays(m)[0][0] for m in ms)
            high = min(_arrays(m)[0][-1] for m in ms)
            if high > low:
                grid = np.geomspace(max(low, 20), min(high, 20000), 512)
                ys = np.array([np.interp(np.log(grid), np.log(_arrays(m)[0]), _arrays(m)[1]) for m in ms])
                spread = _pairwise_rms(ys)
                report.append(_item("repeatability", "warning" if spread > 1 else "pass", f"{ch} · {pos} 重錄一致性", f"重錄差異約 {spread:.2f} dB RMS（{grid[0]:g}–{grid[-1]:g} Hz，未移除音量差）；1 dB 為可調整的產品提醒門檻。", ms))
    if channels == {"L", "R"}:
        low = max(200.0, max(_arrays(m)[0][0] for m in p0))
        high = min(2000.0, min(_arrays(m)[0][-1] for m in p0))
        if high <= low:
            low = max(_arrays(m)[0][0] for m in p0)
            high = min(_arrays(m)[0][-1] for m in p0)
        if high > low:
            grid = np.geomspace(low, high, 256)
            means = {ch: np.mean([np.interp(np.log(grid), np.log(_arrays(m)[0]), _arrays(m)[1]) for m in p0 if m["channel"] == ch], axis=0) for ch in ("L", "R")}
            diff = float(np.median(means["L"] - means["R"]))
            report.append(_item("imbalance", "warning" if abs(diff) > 2 else "pass", "左右寬頻電平比較", f"L − R 約 {diff:+.2f} dB（{low:g}–{high:g} Hz）；差異可能來自擺位或設定，不直接判定錄製失敗。", p0))
    return report


def _settings(settings):
    s = {**DEFAULTS, **(settings or {})}
    if not isinstance(s["bands"], int) or isinstance(s["bands"], bool) or not 1 <= s["bands"] <= 128:
        raise ValueError("本次搜尋 Band 上限必須為 1–128 的整數；128 為運算資源上限，不是設備限制。")
    for key in ("independent", "allow_boost", "allow_extended", "supports_preamp"):
        if not isinstance(s[key], bool):
            raise ValueError(key + " 必須為布林值。")
    for key in ("f_min", "f_max", "max_cut", "max_total_cut", "min_q", "max_q", "gain_step", "freq_step", "q_step", "max_boost", "sample_rate", "target_ref_min", "target_ref_max"):
        if not _number(s[key]) or s[key] <= 0:
            raise ValueError(key + " 必須為有限正數。")
        s[key] = float(s[key])
    if s["mode"] not in ("standard", "deep"):
        raise ValueError("搜尋模式必須為 standard 或 deep。")
    if s["strategy"] not in ("bass_first", "extend_existing", "joint"):
        raise ValueError("配置策略必須為 bass_first、extend_existing 或 joint。")
    if not _number(s["bass_drift_limit_db"]) or not 0 <= s["bass_drift_limit_db"] <= 3:
        raise ValueError("保留頻段曲線變化上限必須為 0–3 dB。")
    s["bass_drift_limit_db"] = float(s["bass_drift_limit_db"])
    if not _number(s["overshoot_scale"]) or not 0 <= s["overshoot_scale"] <= 1:
        raise ValueError("額外削減成本倍率必須為 0–1 的有限數值。")
    s["overshoot_scale"] = float(s["overshoot_scale"])
    if s["objective_mode"] not in ("legacy", "peak", "shape"):
        raise ValueError("計算目標必須為 legacy、peak 或 shape。")
    for key in ("low_cut_limit_db", "preamp_margin_db"):
        if not _number(s[key]) or not 0 <= s[key] <= 6:
            raise ValueError(key + " 必須為 0–6 dB 的有限數值。")
        s[key] = float(s[key])
    if not _number(s["max_total_boost"]) or not 0 < s["max_total_boost"] <= 12:
        raise ValueError("合成增益上限必須大於 0 且不超過 12 dB。")
    s["max_total_boost"] = float(s["max_total_boost"])
    if s["allow_boost"] and not s["supports_preamp"]:
        raise ValueError("允許增益需要可設定前級衰減；請確認 DSP 能力或關閉增益。")
    if not 10 <= s["target_ref_min"] < s["target_ref_max"] < s["sample_rate"] / 2:
        raise ValueError("目標參考頻段需為遞增的有效範圍，且低於模擬取樣率的一半。")
    if not 8000 <= s["sample_rate"] <= 768000 or not 10 <= s["f_min"] < s["f_max"] < s["sample_rate"] / 2:
        raise ValueError("頻段必須在 10 Hz 以上、低於取樣率的一半，且上限大於下限。")
    if s["f_max"] > 200 and not s["allow_extended"]:
        raise ValueError("超過 200 Hz 的校正需先啟用進階頻段。")
    if not 0.1 <= s["min_q"] <= s["max_q"] <= 20:
        raise ValueError("Q 範圍必須落在 0.1–20 且下限不大於上限。")
    if s["max_cut"] > 20 or s["max_total_cut"] > 30 or s["max_boost"] > 6:
        raise ValueError("單段減益上限 20 dB、總減益上限 30 dB、增益上限 6 dB。")
    for lo, hi, step in ((s["f_min"], s["f_max"], s["freq_step"]), (s["min_q"], s["max_q"], s["q_step"])):
        if math.ceil(lo / step - 1e-9) > math.floor(hi / step + 1e-9):
            raise ValueError("調整步進在指定範圍內沒有可用值。")
    if s["gain_step"] > s["max_cut"]:
        raise ValueError("Gain 步進不可大於單段減益上限。")
    if s["target_level"] is not None and not _number(s["target_level"]):
        raise ValueError("目標水平必須為有限數值。")
    if s["target_level"] is not None:
        s["target_level"] = float(s["target_level"])
    # Persist only documented computation settings, not arbitrary UI objects.
    return {k: s[k] for k in DEFAULTS}


def filter_response(frequency, filters, sample_rate=48000) -> np.ndarray:
    """Sum of RBJ peaking biquad magnitude responses, in dB (float64)."""
    f = np.asarray(frequency, dtype=np.float64)
    fs = float(sample_rate)
    if not np.isfinite(fs) or fs <= 0 or not np.all(np.isfinite(f)) or np.any(f < 0) or np.any(f > fs / 2):
        raise ValueError("濾波器評估頻率必須在 DC 至 Nyquist 範圍。")
    z = np.exp(-2j * np.pi * f / fs)
    z2 = z * z
    answer = np.zeros(f.shape, dtype=np.float64)
    for band in filters:
        if not band.get("enabled", True):
            continue
        if band.get("type", "PK") not in ("PK", "Peak", "Bell", "Peaking"):
            raise ValueError("此版本僅支援 Peak / Bell 濾波器。")
        fc, gain, q = (float(band[k]) for k in ("frequency", "gain", "q"))
        if not all(map(math.isfinite, (fc, gain, q))) or not 0 < fc < fs / 2 or q <= 0 or abs(gain) > 100:
            raise ValueError("濾波器參數無效。")
        if abs(gain) < 1e-12:
            continue
        a = 10 ** (gain / 40)
        w = 2 * np.pi * fc / fs
        alpha = np.sin(w) / (2 * q)
        common = -2 * np.cos(w) * z
        num = 1 + alpha * a + common + (1 - alpha * a) * z2
        den = 1 + alpha / a + common + (1 - alpha / a) * z2
        answer += 20 * np.log10(np.maximum(np.abs(num / den), 1e-30))
    return answer


def _cancelled(cancel):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise InterruptedError("已取消 PEQ 計算。")


def _group_curves(measurements, grid):
    groups = defaultdict(list)
    for m in measurements:
        ch = m.get("channel", "Unknown")
        if ch not in ("L", "R"):
            continue
        f, y = _arrays(m)
        if f[0] > grid[0] or f[-1] < grid[-1]:
            raise ValueError(f"{m.get('name', ch)} 未涵蓋設定頻段，請縮小範圍或重新錄製。")
        groups[(ch, m.get("position", "P0"))].append(np.interp(np.log(grid), np.log(f), y))
    curves, names, repeats = [], [], []
    for key in sorted(groups):
        yy = np.asarray(groups[key])
        curves.append(np.mean(yy, axis=0))
        names.append(key)
        if len(yy) >= 2:
            repeats.append(_pairwise_rms(yy))
    if not curves:
        raise ValueError("請先將量測指定為 L 或 R；L+R 不能代替獨立聲道。")
    return np.array(curves), names, max(repeats) if repeats else None


def _round_value(value, step, lo, hi):
    bottom = math.ceil(lo / step - 1e-9)
    top = math.floor(hi / step + 1e-9)
    return round(min(top, max(bottom, round(value / step))) * step, 10)


def _rounded(bands, s):
    result = []
    for b in bands:
        gain = _round_value(b["gain"], s["gain_step"], -s["max_cut"], s["max_boost"] if s["allow_boost"] else 0)
        if abs(gain) < max(s["gain_step"] * .5, .05):
            continue
        result.append(dict(frequency=_round_value(b["frequency"], s["freq_step"], s["f_min"], s["f_max"]), gain=gain,
                           q=_round_value(b["q"], s["q_step"], s["min_q"], min(2., s["max_q"]) if gain > 0 else s["max_q"]), enabled=True, type="PK"))
    return sorted(result, key=lambda b: b["frequency"])


def _log_grid(lo, hi):
    """Globally anchored octave grid: extending a range does not move its bins."""
    a = math.ceil(np.log2(lo) * GRID_PPO)
    b = math.floor(np.log2(hi) * GRID_PPO)
    return np.unique(np.r_[lo, np.exp2(np.arange(a, b + 1) / GRID_PPO), hi])


def _target_reference(measurements, s):
    if s["target_level"] is not None:
        return float(s["target_level"]), dict(mode="manual", level_db=float(s["target_level"]),
                                            coverage_complete=None, measurement_ids=[], statistic="使用者指定")
    ms = [m for m in measurements if m.get("channel") in ("L", "R") and m.get("position", "P0") == "P0"]
    if not ms:
        raise ValueError("自動目標需要 P0 的左右獨立量測；也可手動指定目標水平。")
    lo, hi = s["target_ref_min"], s["target_ref_max"]
    if not all(_covers(_arrays(m)[0], lo, hi) for m in ms):
        raise ValueError(f"P0 量測未完整涵蓋固定目標參考 {lo:g}–{hi:g} Hz；請調整參考範圍或手動指定目標，不會自動換用其他頻段。")
    actual_lo = max(lo, max(_arrays(m)[0][0] for m in ms))
    actual_hi = min(hi, min(_arrays(m)[0][-1] for m in ms))
    grid = np.geomspace(actual_lo, actual_hi, math.ceil(np.log2(actual_hi / actual_lo) * GRID_PPO) + 1)
    ys, names, _ = _group_curves(ms, grid)
    target = float(np.percentile(np.median(ys, axis=0), 35))
    return target, dict(mode="fixed_reference", level_db=target, requested_min=lo, requested_max=hi,
                        actual_min=float(actual_lo), actual_max=float(actual_hi), coverage_complete=True,
                        points=len(grid), measurement_ids=[m.get("id", "") for m in ms],
                        statistic="P0 同聲道重錄 dB 平均 → 左右中位數 → 固定對數參考網格第 35 百分位（產品啟發式）")


def _position_weights(names):
    # Keep the primary seat influential without hiding offset measurements.
    result = np.zeros(len(names))
    channels = sorted({ch for ch, _ in names})
    for ch in channels:
        ids = [i for i, (cc, _) in enumerate(names) if cc == ch]
        p0 = [i for i in ids if names[i][1] == "P0"]
        others = [i for i in ids if i not in p0]
        if p0 and others:
            result[p0] = .67 / len(p0) / len(channels)
            result[others] = .33 / len(others) / len(channels)
        else:
            result[ids] = 1 / len(ids) / len(channels)
    return result


def _desired_model(grid, ys, target, s, boost_allowed):
    # 1/48 octave Gaussian smoothing is a published, explicit product setting.
    sigma = GRID_PPO / 48 / 2.355
    smooth = gaussian_filter1d(ys, sigma=sigma, axis=1, mode="nearest")
    excess = smooth - target
    desired = -np.maximum(excess - .25, 0)
    support = np.all((excess < -.8) & (excess > -4), axis=0)
    support &= grid >= s["f_min"] * np.sqrt(2)
    if boost_allowed:
        desired[:, support] = np.minimum(-excess[:, support] - .25, s["max_boost"])
    desired = np.clip(desired, -s["max_total_cut"], s["max_boost"] if boost_allowed else 0)
    # This penalizes additional attenuation of already-low points continuously.
    # It does NOT classify a point as an acoustic null or a room mode.
    over_weight = s.get("overshoot_scale", 1.) * (2 + np.minimum(12, 2 * np.maximum(-excess, 0)))
    return desired, over_weight, support, excess


def _objective(grid, ys, target, s, bands, weights, boost_allowed):
    if s.get("objective_mode", "legacy") != "legacy":
        from .solver import objective_components
        return objective_components(grid, ys, target, s, bands, weights, boost_allowed)
    desired, over_weight, support, _ = _desired_model(grid, ys, target, s, boost_allowed)
    response = filter_response(grid, bands, s["sample_rate"])
    error = response[None, :] - desired
    fit = float(np.mean(np.sum(weights[:, None] * error ** 2, axis=0)))
    overshoot = float(np.mean(np.sum(weights[:, None] * over_weight * np.minimum(error, 0) ** 2, axis=0)))
    effort = float(.0036 * np.mean(response ** 2))
    unsupported = float(16 * np.mean((np.maximum(response, 0) * ~support) ** 2)) if boost_allowed else 0.
    complexity = .025 * sum(b.get("enabled", True) and abs(b["gain"]) >= .05 for b in bands)
    return dict(value=fit + overshoot + effort + unsupported + complexity,
                correction_error=fit, overshoot=overshoot, effort=effort,
                unsupported_boost=unsupported, complexity=complexity)


def _evaluation_policy(s):
    return dict(algorithm_version=ALGORITHM_VERSION, overshoot_scale=s["overshoot_scale"],
                objective_mode=s["objective_mode"], low_cut_limit_db=s["low_cut_limit_db"],
                hard_low_cut_constraint=s["objective_mode"] != "legacy",
                preamp_in_objective=False)


def _combined_boost_limit(s):
    return (s["max_boost"] if s["objective_mode"] == "legacy" else s["max_total_boost"]) if s["allow_boost"] else 0.


def _boundary_flags(band, s):
    """Numerical boundary proximity is not evidence of a physical room mode."""
    flags = []
    qhi = min(2., s["max_q"]) if band["gain"] > 0 else s["max_q"]
    for code, value, bound, step in (("q_min", band["q"], s["min_q"], s["q_step"]),
                                     ("q_max", band["q"], qhi, s["q_step"]),
                                     ("gain_min", band["gain"], -s["max_cut"], s["gain_step"]),
                                     ("gain_max", band["gain"], s["max_boost"] if s["allow_boost"] else 0., s["gain_step"])):
        if abs(value - bound) <= step + 1e-9:
            flags.append(code)
    return flags


def _metric_snapshot(grid, ys, names, target, filters, sample_rate):
    """Raw dB metrics on one common grid, separate from the fitting policy."""
    weights = _position_weights(names)
    positions = []
    for i, (ch, pos) in enumerate(names):
        response = filter_response(grid, filters.get(ch, filters.get("Shared", [])), sample_rate)
        below = ys[i] < target
        value = dict(residual_peak_rms_db=float(np.sqrt(np.mean(np.maximum(ys[i] + response - target, 0.) ** 2))),
                     below_target_cut_rms_db=float(np.sqrt(np.mean(np.maximum(-response[below], 0.) ** 2))) if below.any() else 0.,
                     below_target_bin_count=int(below.sum()), total_bin_count=len(grid))
        positions.append(dict(id=f"{ch}:{pos}", channel=ch, position=pos, weight=float(weights[i]), **value))
    summary = {field: float(np.sqrt(sum(p["weight"] * p[field] ** 2 for p in positions)))
               for field in ("residual_peak_rms_db", "below_target_cut_rms_db")}
    summary["worst_below_target_cut_rms_db"] = max(p["below_target_cut_rms_db"] for p in positions)
    return dict(summary=summary, positions=positions)


def _explain_peq(grid, ys, names, target, s, filters, frozen_counts=None):
    """Counterfactuals assess the rounded filters, including disabled/frozen rows.

    The diagnostic metrics are invariant to overshoot_scale for fixed filters.
    Native costs remain policy-specific and must not rank different policies.
    """
    frozen_counts = frozen_counts or {}
    empty = {key: [] for key in filters}
    before = _metric_snapshot(grid, ys, names, target, empty, s["sample_rate"])
    after = _metric_snapshot(grid, ys, names, target, filters, s["sample_rate"])
    position_rows = []
    for b, p in zip(before["positions"], after["positions"]):
        fields = ("residual_peak_rms_db", "below_target_cut_rms_db", "below_target_bin_count", "total_bin_count")
        position_rows.append({**{k:p[k] for k in ("id", "channel", "position", "weight")},
                              "before":{k:b[k] for k in fields}, "after":{k:p[k] for k in fields}})
    band_rows, native_before, native_after = [], [], []
    for key, bands in filters.items():
        ids = [i for i, (ch, _) in enumerate(names) if key == "Shared" or ch == key]
        subset_names = [names[i] for i in ids]
        weights = _position_weights(subset_names)
        positions_by_channel = {ch:{pos for cc, pos in subset_names if ch == cc} for ch, _ in subset_names}
        boost_ready = s["allow_boost"] and s["min_q"] <= 2 and all({"P0", "P-10", "P+10"}.issubset(ps) for ps in positions_by_channel.values())
        cost = _objective(grid, ys[ids], target, s, bands, weights, boost_ready)
        native_before.append(_objective(grid, ys[ids], target, s, [], weights, boost_ready)["value"])
        native_after.append(cost["value"])
        for index, band in enumerate(bands):
            remaining = bands[:index] + bands[index + 1:]
            removed = {**filters, key:remaining}
            without_cost = _objective(grid, ys[ids], target, s, remaining, weights, boost_ready)
            enabled = band.get("enabled", True)
            frozen = index < frozen_counts.get(key, 0)
            flags = _boundary_flags(band, s)
            labels = (["未啟用，移除不改變曲線"] if not enabled else []) + (["保留參數"] if frozen else [])
            labels += ["Q 接近限制" for _ in [0] if any(f.startswith("q_") for f in flags)]
            labels += ["Gain 接近限制" for _ in [0] if any(f.startswith("gain_") for f in flags)]
            band_rows.append(dict(channel=key, index=index + 1, frequency=band["frequency"], gain=band["gain"], q=band["q"],
                                  enabled=enabled, frozen=frozen, boundary_flags=flags, labels=labels,
                                  with_band=deepcopy(after), without_band=_metric_snapshot(grid, ys, names, target, removed, s["sample_rate"]),
                                  native_cost=dict(with_band=cost, without_band=without_cost,
                                                   benefit={k:without_cost[k] - cost[k] for k in cost}, unit="objective")))
    if s["objective_mode"] != "legacy":
        before["summary"]["primary_rms_db"] = float(np.sqrt(np.mean(native_before)))
        after["summary"]["primary_rms_db"] = float(np.sqrt(np.mean(native_after)))
    return dict(schema_version=1, target_level_db=target,
                cost_provenance="current_policy_manual_evaluation",
                evaluation_policy=_evaluation_policy(s),
                grid=dict(f_min=float(grid[0]), f_max=float(grid[-1]), points=len(grid), spacing="globally anchored log2; 192 points/octave plus endpoints",
                          averaging="同聲道同位置在對數頻率軸插值後以 dB 平均；診斷使用未平滑 SPL"),
                definitions=dict(residual_peak_rms_db="sqrt(mean(max(raw_SPL+EQ-target,0)^2)); 分母為全部頻點",
                                 below_target_cut_rms_db="sqrt(mean(max(-EQ,0)^2 | raw_SPL<target)); 分母只含原先低於目標頻點，空集合回 0 並顯示數量",
                                 worst_below_target_cut_rms_db="所有已量聲道／位置的 conditional 低處新增削減 RMS 最大值",
                                 native_cost="既有策略為無單位成本；v4 削峰／精修為主指標的平方（dB²），沒有 Band 數罰分；都不是聽感分數。",
                                 primary_rms_db="削峰模式＝殘留波峰 RMS；增減益精修＝殘留波峰及具多位置寬頻支持之低處誤差的加權 RMS；前級不進入目標"),
                weighting=dict(policy="聲道等權；各聲道有 P0 與偏移時 P0 67%、偏移共 33%；只有一類位置則在該聲道內等權；總 RMS 為加權平方平均後開根號",
                               positions=[{k:p[k] for k in ("id", "channel", "position", "weight")} for p in after["positions"]]),
                positions=position_rows, summary=dict(before=before["summary"], after=after["summary"]), bands=band_rows,
                notes=["上述 EQ 是濾波器合成曲線，不含前級；固定同一目標和網格才可比較。",
                       "低於目標是診斷遮罩，不代表物理凹洞或 minimum-phase 辨識；0 個低處頻點不代表已證明安全。",
                       "移除 Band 是其餘參數不變的反事實，沒有重新最佳化；成本差只代表本策略偏好。",
                       "低處 RMS 包含完整 EQ 與所有保留 Band，不含前級，並不是每個頻點的最大減益限制。",
                       "資料只覆蓋已量位置；預測不等於套用後或其他位置的實測。"])


def explain_peq(measurements, settings, filters, target_level, *, frozen_counts=None):
    """Read-only diagnostics for saved filters, without rounding or refitting.

    Existing filters need valid biquad parameters, but do not need to obey the
    current device bounds. Native costs here are explicitly post-hoc policy costs.
    """
    measurements = list(measurements)
    s = _settings({**(settings or {}), "target_level":target_level})
    usable = [m for m in measurements if m.get("channel") in ("L", "R")]
    if not usable:
        raise ValueError("診斷需要已指定 L／R 的量測。")
    lo = max(s["f_min"], max(_arrays(m)[0][0] for m in usable))
    hi = min(s["f_max"], min(_arrays(m)[0][-1] for m in usable))
    if hi <= lo:
        raise ValueError("診斷量測沒有共同有效頻段。")
    grid = _log_grid(lo, hi)
    ys, names, _ = _group_curves(usable, grid)
    expected = {ch for ch, _ in names} if s["independent"] else {"Shared"}
    if not isinstance(filters, dict) or set(filters) != expected:
        raise ValueError("診斷濾波器聲道與量測設定不一致。")
    for bands in filters.values():
        if not isinstance(bands, (list, tuple)):
            raise ValueError("診斷濾波器必須為清單。")
        for band in bands:
            if (not isinstance(band, dict) or not isinstance(band.get("enabled", True), bool)
                    or not all(_number(band.get(k)) for k in ("frequency", "gain", "q"))
                    or not 0 < band["frequency"] < s["sample_rate"] / 2 or band["q"] <= 0 or abs(band["gain"]) > 100
                    or band.get("type", "PK") not in ("PK", "Peak", "Bell", "Peaking")):
                raise ValueError("保存的濾波器含無效 biquad 參數，無法產生診斷。")
    explanation = _explain_peq(grid, ys, names, s["target_level"], s, filters, frozen_counts)
    explanation["cost_provenance"] = "posthoc_current_policy"
    explanation["evaluation_policy"] = _evaluation_policy(s)
    explanation["notes"].append("成本是以目前評估政策事後重算，不代表舊版產生當時的成本或取捨；保存參數未取整、未重算。")
    return explanation


def _guard_filters(bands, s, grid, frozen=(), protect_grid=None):
    """Only reduce unlocked bands, one responsible band at a time.

    Dense response checks enforce total gain and extension tail bounds after
    quantization. Locked filters are never rounded, scaled, merged or reordered.
    """
    bands = _rounded(bands, s)
    frozen = list(frozen)
    centers = [b["frequency"] for b in frozen + bands if b.get("enabled", True)]
    full = np.unique(np.r_[np.geomspace(1, s["sample_rate"] / 2 * .99999, 8192), grid, centers])
    frozen_response = filter_response(full, frozen, s["sample_rate"])
    # Leave 0.01 dB headroom for extrema between frequency-grid samples.
    cut_limit = s["max_total_cut"] - .01
    boost_limit = s["max_boost"] - .01 if s["allow_boost"] else 0.
    for _ in range(2000):
        if not bands:
            break
        parts = np.array([filter_response(full, [b], s["sample_rate"]) for b in bands])
        response = frozen_response + parts.sum(axis=0)
        cut_error = -response - cut_limit
        boost_error = response - boost_limit
        if max(cut_error.max(), boost_error.max()) > 1e-8:
            is_cut = cut_error.max() >= boost_error.max()
            index = int(np.argmax(cut_error if is_cut else boost_error))
            impact = -parts[:, index] if is_cut else parts[:, index]
        elif protect_grid is not None:
            local = np.array([filter_response(protect_grid, [b], s["sample_rate"]) for b in bands])
            drift = local.sum(axis=0)
            index = int(np.argmax(np.abs(drift)))
            if abs(drift[index]) <= max(0., s["bass_drift_limit_db"] - .005) + 1e-8:
                break
            impact = local[:, index] * np.sign(drift[index])
        else:
            break
        if impact.max(initial=0) <= 1e-10:
            # The immutable incumbent, rather than the additions, violates a
            # constraint. Validation should have rejected it before fitting.
            return []
        index = int(np.argmax(impact))
        b = bands[index]
        decrement = max(s["gain_step"], abs(b["gain"]) * .08)
        units = math.floor(max(0., abs(b["gain"]) - decrement) / s["gain_step"] + 1e-9)
        b["gain"] = round(math.copysign(units * s["gain_step"], b["gain"]), 10)
        bands = [b for b in bands if abs(b["gain"]) >= s["gain_step"] * .5]
    return bands


def _fit(grid, ys, target, s, boost_allowed, cancel, progress, *, names=None,
         frozen=(), center_min=None, center_max=None, slots=None, protect_grid=None):
    frozen = deepcopy(list(frozen))
    slots = s["bands"] - len(frozen) if slots is None else slots
    if slots <= 0:
        return [], 0, dict(skipped=["可用 Band 已用完；保留原設定，沒有重排。"], reason_codes=["no_budget"], merges=0, rounds=[])
    lo = s["f_min"] if center_min is None else center_min
    hi = s["f_max"] if center_max is None else center_max
    lo = math.ceil(lo / s["freq_step"] - 1e-9) * s["freq_step"]
    hi = math.floor(hi / s["freq_step"] + 1e-9) * s["freq_step"]
    if hi < lo or hi <= 0:
        return [], 0, dict(skipped=["延伸範圍沒有符合 Hz 步進的新增中心頻率。"], reason_codes=["no_valid_center"], merges=0, rounds=[])
    rounding = {**s, "f_min": lo, "f_max": hi}
    weights = _position_weights(names) if names else np.full(len(ys), 1 / len(ys))
    desired, over_weight, boost_mask, excess = _desired_model(grid, ys, target, s, boost_allowed)
    root_weights = np.sqrt(weights[:, None])
    frozen_response = filter_response(grid, frozen, s["sample_rate"])
    bands, evaluations, merges, rounds = [], 0, 0, []
    candidate_seen = False
    rng = np.random.default_rng(731)
    n = len(grid)

    def unpack(x):
        return [dict(frequency=float(np.exp(row[0])), gain=float(row[1]), q=float(np.exp(row[2])), enabled=True, type="PK") for row in np.reshape(x, (-1, 3))]

    def pack(bs):
        return np.array([[np.log(b["frequency"]), b["gain"], np.log(b["q"])] for b in bs]).ravel()

    def residual_for(bs):
        nonlocal evaluations
        evaluations += 1
        _cancelled(cancel)
        additions = filter_response(grid, bs, s["sample_rate"])
        r = frozen_response + additions
        error = r[None, :] - desired
        err = (root_weights * error).ravel()
        overshoot = (root_weights * np.sqrt(over_weight) * np.minimum(error, 0)).ravel()
        total_penalty = np.minimum(r + s["max_total_cut"] - .02, 0) * 20
        boost_penalty = np.maximum(r - (s["max_boost"] if boost_allowed else 0), 0) * 20
        unsupported = np.maximum(r, 0) * ~boost_mask * (4 if boost_allowed else 20)
        tail = np.maximum(np.abs(filter_response(protect_grid, bs, s["sample_rate"])) - s["bass_drift_limit_db"] + .01, 0) * 20 if protect_grid is not None else np.zeros(1)
        return np.concatenate((err, overshoot, total_penalty, boost_penalty, unsupported, .06 * r, tail))

    def score(bs):
        return _objective(grid, ys, target, s, frozen + bs, weights, boost_allowed)["value"]

    def trace_round(index, before_bands, candidate, accepted, reason, projected=False):
        before_cost = _objective(grid, ys, target, s, frozen + before_bands, weights, boost_allowed)
        candidate_cost = _objective(grid, ys, target, s, frozen + candidate, weights, boost_allowed) if candidate is not None else None
        flags = sorted({flag for b in (candidate or []) for flag in _boundary_flags(b, s)})
        if projected:
            flags.append("guard_changed_rounded_candidate")
        if candidate is not None:
            r = filter_response(grid, frozen + candidate, s["sample_rate"])
            if -r.min(initial=0) >= s["max_total_cut"] - max(.05, s["gain_step"]):
                flags.append("total_cut_near_limit")
            if protect_grid is not None and np.max(np.abs(filter_response(protect_grid, candidate, s["sample_rate"]))) >= max(0., s["bass_drift_limit_db"] - .02):
                flags.append("preserved_range_drift_near_limit")
        rounds.append(dict(round=index + 1, phase="add_band", accepted=accepted, reason_code=reason,
                           reason={"accepted":"取整與限制處理後，本輪最佳候選成本改善超過 0.015。",
                                   "insufficient_search_gain":"本輪搜尋未找到取整後改善超過 0.015 的可行新增方案。",
                                   "below_candidate_threshold":"本輪搜尋未找到剩餘理想修正需求達 0.55 dB 的中心候選。"}[reason],
                           before_filters=deepcopy(frozen + before_bands), candidate_filters=deepcopy(frozen + candidate) if candidate is not None else None,
                           before_cost=before_cost, candidate_cost=candidate_cost,
                           improvement=before_cost["value"] - candidate_cost["value"] if candidate_cost is not None else None,
                           active_constraints=flags,
                           constraints_considered=["parameter_bounds", "entry_step_rounding", "total_gain", "band_budget"] + (["preserved_range_drift"] if protect_grid is not None else [])))

    def project(bs):
        trial = _guard_filters(bs, rounding, grid, frozen=frozen, protect_grid=protect_grid)
        safe = []
        for b in trial:
            if b["gain"] > 0:
                core = np.abs(np.log2(grid / b["frequency"])) <= min(.2, 1 / b["q"] / 3)
                if (not boost_allowed or b["q"] > 2 or b["frequency"] < s["f_min"] * np.sqrt(2)
                        or not core.any() or np.any(np.mean((excess[:, core] < -.5) & (excess[:, core] > -4), axis=1) < .8)):
                    continue
            safe.append(b)
        return safe

    best_score = score([])
    for index in range(slots):
        _cancelled(cancel)
        current = frozen_response + filter_response(grid, bands, s["sample_rate"])
        need = weights @ desired - current
        strength = np.abs(need) if boost_allowed else np.maximum(-need, 0)
        possible = (grid >= lo) & (grid <= hi)
        strength = np.where(possible, strength, 0.)
        candidates, _ = find_peaks(strength, distance=max(2, GRID_PPO // 24))
        candidates = sorted(set(list(candidates) + [int(np.argmax(strength))]), key=lambda j: strength[j], reverse=True)
        if not candidates or strength[candidates[0]] < .55:
            trace_round(index, bands, None, False, "below_candidate_threshold")
            break
        candidate_seen = True
        best = None
        best_observed = None
        starts = 6 if s["mode"] == "standard" else 18
        for attempt in range(starts):
            _cancelled(cancel)
            ci = candidates[min(attempt // 3, len(candidates) - 1)]
            if not possible[ci]:
                continue
            is_boost = need[ci] > 0 and boost_allowed and boost_mask[ci]
            if need[ci] > 0 and not is_boost:
                continue
            q_hi = min(s["max_q"], 2.) if is_boost else s["max_q"]
            if q_hi < s["min_q"]:
                continue
            gain_lo, gain_hi = ((.001, s["max_boost"]) if is_boost else (-s["max_cut"], -.001))
            q_guess = np.clip([.7, 1.5, 3., 5., 8., 2.][attempt % 6], s["min_q"], q_hi)
            center = float(grid[ci])
            if attempt >= 6:
                center = np.clip(center * np.exp(rng.normal(0, .07)), lo, hi)
            seed = dict(frequency=center, gain=float(np.clip(need[ci], gain_lo, gain_hi)), q=float(q_guess))
            lower = np.array([np.log(lo), gain_lo, np.log(s["min_q"])])
            upper = np.maximum([np.log(hi), gain_hi, np.log(q_hi)], lower + 1e-9)
            x0 = np.clip(pack([seed]), lower + 1e-12, upper - 1e-12)
            fit = least_squares(lambda x: residual_for(bands + unpack(x)), x0, bounds=(lower, upper),
                                max_nfev=70 if s["mode"] == "standard" else 150, ftol=1e-7, xtol=1e-7, gtol=1e-7)
            trial = project(bands + unpack(fit.x))
            value = score(trial)
            if best_observed is None or value < best_observed[0]:
                best_observed = (value, trial, _rounded(bands + unpack(fit.x), rounding) != trial)
            if value < best_score - .015 and (best is None or value < best[0]):
                best = value, trial
        if best is None:
            trace_round(index, bands, best_observed[1] if best_observed else None, False, "insufficient_search_gain", best_observed[2] if best_observed else False)
            break
        trace_round(index, bands, best[1], True, "accepted", best_observed[2] if best_observed else False)
        best_score, bands = best
        if progress:
            progress((index + 1) / slots * .8)
    # Deterministic multistart joint refinement of *unlocked* bands only.
    if bands:
        lower, upper = [], []
        for b in bands:
            gl, gh = ((.001, s["max_boost"]) if b["gain"] > 0 else (-s["max_cut"], -.001))
            lower += [np.log(lo), gl, np.log(s["min_q"])]
            upper += [np.log(hi), gh, np.log(min(2., s["max_q"]) if b["gain"] > 0 else s["max_q"])]
        lower = np.asarray(lower)
        upper = np.maximum(upper, lower + 1e-9)
        seed = pack(bands)
        for attempt in range(1 if s["mode"] == "standard" else 4):
            x0 = seed.copy()
            if attempt:
                x0[0::3] += rng.normal(0, .035, len(seed) // 3)
                x0[2::3] += rng.normal(0, .15, len(seed) // 3)
            fit = least_squares(lambda x: residual_for(unpack(x)), np.clip(x0, lower + 1e-12, upper - 1e-12),
                                bounds=(lower, upper), max_nfev=100 if s["mode"] == "standard" else 220,
                                ftol=1e-7, xtol=1e-7, gtol=1e-7)
            trial = project(unpack(fit.x))
            value = score(trial)
            if value < best_score:
                best_score, bands = value, trial
    # Merge near duplicates only if one legal band reproduces their useful fit.
    changed = True
    while changed:
        changed = False
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                a, b = bands[i], bands[j]
                gain = a["gain"] + b["gain"]
                if (a["gain"] * b["gain"] <= 0 or abs(np.log2(a["frequency"] / b["frequency"])) > 1 / 24
                        or abs(np.log(a["q"] / b["q"])) > .25 or not -s["max_cut"] <= gain <= s["max_boost"]):
                    continue
                weight = abs(a["gain"]) / (abs(a["gain"]) + abs(b["gain"]))
                combined = dict(frequency=np.exp(weight * np.log(a["frequency"]) + (1 - weight) * np.log(b["frequency"])),
                                gain=gain, q=np.exp(weight * np.log(a["q"]) + (1 - weight) * np.log(b["q"])))
                trial = project([band for k, band in enumerate(bands) if k not in (i, j)] + [combined])
                if score(trial) <= score(bands):
                    bands, changed, merges = trial, True, merges + 1
                    break
            if changed:
                break
    for i in range(len(bands) - 1, -1, -1):
        reduced = bands[:i] + bands[i + 1:]
        if score(reduced) <= score(bands) + .01:
            bands = reduced
    skipped, reason_codes = [], []
    if len(bands) < slots:
        if not candidate_seen:
            skipped.append("本輪搜尋未找到超過目前目標、容差與候選門檻的待修正峰；局部波峰不一定高於共同目標，因此未新增 Band。")
            reason_codes.append("no_above_target_peak")
        else:
            skipped.append("本輪搜尋未找到在取整、總減益、曲線變化與新增收益檢查後有足夠收益的剩餘候選，保留空白 Band。")
            reason_codes.append("constrained_or_insufficient_benefit")
    if protect_grid is not None and not bands:
        skipped.append(f"未新增延伸 Band；保留範圍曲線變化上限為 {s['bass_drift_limit_db']:g} dB，沒有為了延伸而改動已保留參數。")
    remaining_need = weights @ desired - frozen_response - filter_response(grid, bands, s["sample_rate"])
    possible = (grid >= lo) & (grid <= hi)
    remaining_demand = float(np.max(np.where(possible, np.maximum(-remaining_need, 0), 0)))
    if len(bands) >= slots and remaining_demand > .55:
        skipped.append(f"已使用全部可配置 Band，仍有約 {remaining_demand:.2f} dB 的最大未擬合減益需求；不會為填平所有起伏而超出數量或重排保留設定。")
        reason_codes.append("budget_exhausted_with_residual")
    if progress:
        progress(1.)
    return bands, evaluations, dict(skipped=skipped, reason_codes=reason_codes, merges=merges,
                                    remaining_correction_demand_db=remaining_demand, rounds=rounds,
                                    final_filters=deepcopy(frozen + bands), final_cost=_objective(grid, ys, target, s, frozen + bands, weights, boost_allowed),
                                    cost_note="成本按取整後參數重算；局部搜尋、聯合精修與移除冗餘後的最終值，不是全域最優證明。")


def _validate_base_peq(base, s, expected_keys):
    if not isinstance(base, dict) or not isinstance(base.get("filters"), dict):
        raise ValueError("延伸既有方案需要先選擇一版 PEQ。")
    old = _settings(base.get("settings", {}))
    if old["independent"] != s["independent"] or set(base["filters"]) != expected_keys:
        raise ValueError("延伸方案的左右獨立／共用方式必須與原版相同。")
    if not math.isclose(old["sample_rate"], s["sample_rate"]):
        raise ValueError("延伸既有方案需維持相同濾波器模擬取樣率。")
    if s["f_min"] > old["f_min"] + 1e-9 or s["f_max"] < old["f_max"] - 1e-9:
        raise ValueError("延伸範圍不可縮小原方案的頻段。")
    if not _number(base.get("target_level")):
        raise ValueError("原方案沒有有效目標水平，無法鎖定延伸。")
    if s["target_level"] is not None and not math.isclose(s["target_level"], float(base["target_level"]), abs_tol=1e-8):
        raise ValueError("延伸既有方案會鎖定原目標水平；若要改目標，請選整體重算。")
    for key, bands in base["filters"].items():
        if not isinstance(bands, list) or len(bands) > s["bands"]:
            raise ValueError(f"{key} 原方案需要 {len(bands)} Band，目前僅 {s['bands']}；請增加可用數量或選擇另一版，不會刪改已保留 Band。")
        for b in bands:
            if not isinstance(b, dict) or b.get("type", "PK") != "PK" or not all(_number(b.get(k)) for k in ("frequency", "gain", "q")):
                raise ValueError("原方案包含無效或不支援的濾波器。")
            for field, low, high, step in (("frequency", s["f_min"], s["f_max"], s["freq_step"]),
                                           ("gain", -s["max_cut"], s["max_boost"] if s["allow_boost"] else 0., s["gain_step"]),
                                           ("q", s["min_q"], min(2., s["max_q"]) if b["gain"] > 0 else s["max_q"], s["q_step"])):
                value = float(b[field])
                if not low - 1e-9 <= value <= high + 1e-9 or not math.isclose(value / step, round(value / step), abs_tol=1e-6):
                    raise ValueError(f"{key} 原 Band 的 {field} 不符合本次限制或步進；延伸模式不會自動取整改寫原參數。")
        grid = np.unique(np.r_[np.geomspace(1, s["sample_rate"] / 2 * .99999, 16384), [b["frequency"] for b in bands]])
        response = filter_response(grid, bands, s["sample_rate"])
        if response.min(initial=0) < -s["max_total_cut"] - 1e-6 or response.max(initial=0) > _combined_boost_limit(s) + 1e-6:
            raise ValueError(f"{key} 原方案已超過本次總 Gain 限制，不能在保持原值的條件下延伸。")
    return old


def generate_peq(measurements, settings, progress=None, cancel=None, *, base_peq=None) -> dict:
    """Return one complete replacement; extension freezes a previous filter set."""
    started = time.perf_counter()
    _cancelled(cancel)
    measurements = list(measurements)
    s = _settings(settings)
    fit_function = _fit
    if s["objective_mode"] != "legacy":
        from .solver import fit_strategy
        fit_function = fit_strategy
    if any(m.get("applied_peq_id") or m.get("role") == "verification" for m in measurements):
        raise ValueError("PEQ 必須由原始 Baseline 計算。補錄用來驗證，不能直接疊加新的濾波器。")
    report = quality_report(measurements, s)
    errors = [i for i in report if i.get("blocking")]
    if errors:
        raise ValueError("；".join(dict.fromkeys(i["title"] for i in errors)))
    usable = [m for m in measurements if m.get("channel") in ("L", "R")]
    if not usable:
        raise ValueError("請先將量測指定為 L 或 R；L+R 不能代替獨立聲道。")
    low = max(s["f_min"], max(_arrays(m)[0][0] for m in usable))
    high = min(s["f_max"], min(_arrays(m)[0][-1] for m in usable))
    if high <= low:
        raise ValueError("所選量測沒有共同校正頻段。")
    grid = _log_grid(low, high)
    ys, names, repeat = _group_curves(usable, grid)
    if not any(pos == "P0" for _, pos in names):
        raise ValueError("請提供中央位置 P0 的 Baseline。")
    groups = [(ch, [i for i, (channel, _) in enumerate(names) if channel == ch]) for ch in sorted({c for c, _ in names})] if s["independent"] else [("Shared", list(range(len(names))))]
    prior = None
    if s["strategy"] == "extend_existing":
        prior = _validate_base_peq(base_peq, s, {key for key, _ in groups})
        target = float(base_peq["target_level"])
        reference = dict(mode="inherited", level_db=target, source_peq_id=base_peq.get("id", ""),
                         original=deepcopy(base_peq.get("target_reference", {})), coverage_complete=None,
                         measurement_ids=[], statistic="沿用已選方案的目標水平")
    else:
        target, reference = _target_reference(usable, s)
    warnings = [f"{i['title']}：{i['detail']}" for i in report if i["level"] == "warning"]
    if repeat is None:
        warnings.append("缺少同位置重錄，無法量化重現性；建議先補錄 A/B。")
    elif repeat > 1:
        warnings.append(f"校正範圍 {grid[0]:g}–{grid[-1]:g} Hz 的重錄差異約 {repeat:.2f} dB；請先確認環境與設定，細微差異可能不可靠。")
    if any((m.get("metadata") or {}).get("cal_status") != "loaded" for m in usable):
        warnings.append("至少一筆量測的麥克風 Cal 未確認，建議核對原量測資訊；可以繼續分析但不能因此視為已校正。")
    if any((m.get("metadata") or {}).get("clipping") is None for m in usable):
        warnings.append("來源未提供完整 Clipping 資訊，預測不能證明原錄製沒有過載。")
    filters, allocations, objectives = {}, {}, {}
    total_evals = 0
    for gi, (key, ids) in enumerate(groups):
        subset_names = [names[i] for i in ids]
        positions = {ch: {pos for cc, pos in subset_names if cc == ch} for ch, _ in subset_names}
        boost_ready = s["allow_boost"] and s["min_q"] <= 2 and all({"P0", "P-10", "P+10"}.issubset(ps) for ps in positions.values())
        if s["allow_boost"] and not boost_ready:
            warnings.append(f"{key} 增益需要 P0 與左右 10 cm Baseline 及 Q ≤ 2；本輪新增濾波器只減益。")
        frozen, additions, protected, boundary = [], [], None, None
        stages, skipped, reason_codes, evals = [], [], [], 0
        def callback(value):
            if progress:
                progress((gi + .95 * value) / len(groups))
        if prior is not None:
            frozen = deepcopy(base_peq["filters"][key])
            boundary = prior["f_max"]
            protected = _log_grid(max(prior["f_min"], low), min(prior["f_max"], high))
            stages.append(dict(name="保留既有方案", band_count=len(frozen), source_peq_id=base_peq.get("id", "")))
        elif s["strategy"] == "bass_first" and low < 200 < high:
            bass_grid = _log_grid(low, 200.)
            bass_ys, bass_names, _ = _group_curves(usable, bass_grid)
            if s["objective_mode"] == "legacy":
                frozen, count, info = fit_function(bass_grid, bass_ys[ids], target, {**s, "f_max": 200.}, boost_ready,
                                                  cancel, lambda v: callback(v * .65), names=[bass_names[i] for i in ids])
            else:
                # Account for the whole requested domain and all filter tails
                # from the first stage; only the first-stage centres are <=200.
                frozen, count, info = fit_function(grid, ys[ids], target, s, boost_ready,
                                                  cancel, lambda v: callback(v * .65), names=subset_names, center_max=200.)
            evals += count
            stages.append(dict(name="低頻優先", band_count=len(frozen), f_min=low, f_max=200., objective_evaluations=count, search=info))
            skipped.extend(info["skipped"])
            reason_codes.extend(info["reason_codes"])
            boundary, protected = 200., bass_grid
        if boundary is not None:
            slots = s["bands"] - len(frozen)
            if high <= boundary or slots <= 0:
                skipped.append("沒有剩餘 Band 或尚未擴大頻段；已完整保留原方案，未新增延伸修正。")
                reason_codes.append("no_budget" if slots <= 0 else "no_extension_range")
            else:
                center_min = (math.floor(boundary / s["freq_step"] + 1e-9) + 1) * s["freq_step"]
                additions, count, info = fit_function(grid, ys[ids], target, s, boost_ready, cancel,
                                             lambda v: callback(.65 + .35 * v), names=subset_names,
                                             frozen=frozen, center_min=center_min, slots=slots, protect_grid=protected)
                evals += count
                skipped.extend(info["skipped"])
                reason_codes.extend(info["reason_codes"])
                stages.append(dict(name="使用剩餘 Band 延伸", band_count=len(additions), f_min=center_min, f_max=high, objective_evaluations=count, merges=info["merges"], search=info))
        else:
            additions, evals, info = fit_function(grid, ys[ids], target, s, boost_ready, cancel, callback, names=subset_names)
            stages.append(dict(name="整體重算" if s["strategy"] == "joint" else "低頻優先", band_count=len(additions), objective_evaluations=evals, merges=info["merges"], search=info))
            skipped.extend(info["skipped"])
            reason_codes.extend(info["reason_codes"])
        bands = frozen + additions
        filters[key] = bands
        total_evals += evals
        drift = float(np.max(np.abs(filter_response(protected, additions, s["sample_rate"])))) if protected is not None else 0.
        allocations[key] = dict(frozen_count=len(frozen), new_count=len(additions), remaining_count=s["bands"] - len(bands),
                                bass_drift_db=drift, protected_max_hz=boundary, skipped=skipped,
                                reason_codes=list(dict.fromkeys(reason_codes)), stages=stages)
        weights = _position_weights(subset_names)
        objectives[key] = dict(before=_objective(grid, ys[ids], target, s, [], weights, boost_ready),
                               after=_objective(grid, ys[ids], target, s, bands, weights, boost_ready),
                               prior=_objective(grid, ys[ids], target, s, frozen, weights, boost_ready),
                               position_weights={f"{ch}:{pos}": float(w) for (ch, pos), w in zip(subset_names, weights)})
    # Reuse exact manual validation/metrics. It never changes feasible rounded
    # values, including the order and disabled parameters of the locked filters.
    evaluated = evaluate_peq(usable, s, filters, target)
    evaluated["filters"] = deepcopy(filters)
    evaluated["settings"] = s
    evaluated["target_reference"] = reference
    evaluated["metrics"].update(manual_edit=False, objective_evaluations=total_evals,
                                elapsed_seconds=time.perf_counter() - started, grid_points_per_octave=GRID_PPO)
    evaluated["objective"] = dict(formula="mean_f(sum_position(w*(response-desired)^2 + w*overshoot_scale*(2+min(12,2*max(-excess,0)))*min(response-desired,0)^2)) + .0036*mean_f(response^2) + 16*mean_f(unsupported_boost^2) + .025*active_bands",
                                  desired="cut-only: -clip(max(smoothed_SPL-target-0.25,0),0,max_total_cut); supported broad boost only when explicitly allowed",
                                  smoothing="1/48 octave Gaussian; 192 points/octave", channels=objectives, overshoot_scale=s["overshoot_scale"],
                                  thresholds="權重、0.25 dB 容差、Band 成本及曲線變化上限是可檢驗的產品啟發式，不是聲學標準")
    if s["objective_mode"] != "legacy":
        evaluated["objective"].update(
            mode=s["objective_mode"],
            formula="weighted mean(max(raw_SPL + EQ - target, 0)^2) + supported_deficit_MSE (shape only)",
            desired="直接降低殘留波峰；精修模式另計具多位置寬頻支持之低處誤差。",
            smoothing="主削峰指標使用原始 dB 曲線；增益支持判斷沿用 1/48 octave 平滑。",
            hard_constraints=dict(low_cut_limit_db=s["low_cut_limit_db"], max_bands=s["bands"],
                                  max_total_cut_db=s["max_total_cut"], max_total_boost_db=_combined_boost_limit(s)),
            thresholds="Band 數是上限，沒有每段固定罰分。有限搜尋以 0.005 dB 主 RMS 改善作新增起始門檻；不是可聽差異標準。")
    evaluated["allocation"] = dict(strategy=s["strategy"], channels=allocations,
                                   frozen_counts={k: a["frozen_count"] for k, a in allocations.items()},
                                   remaining_counts={k: a["remaining_count"] for k, a in allocations.items()},
                                   bass_drift_db={k: a["bass_drift_db"] for k, a in allocations.items()},
                                   skipped=[f"{k}：{reason}" for k, a in allocations.items() for reason in a["skipped"]])
    evaluated["explanation"] = _explain_peq(grid, ys, names, target, s, filters, evaluated["allocation"]["frozen_counts"])
    evaluated["explanation"]["cost_provenance"] = "current_policy_on_rounded_result"
    evaluated["explanation"]["evaluation_policy"] = _evaluation_policy(s)
    rationale = [f"校正 {s['f_min']:g}–{s['f_max']:g} Hz；左右共用 {target:.2f} dB 目標，校正範圍不會改變參考範圍。",
                 (f"自動目標固定參考 P0 的 {s['target_ref_min']:g}–{s['target_ref_max']:g} Hz；" + reference["statistic"] + "。") if reference["mode"] == "fixed_reference" else "目標水平由使用者指定或從保留方案繼承，不另行估算。",
                 "同聲道同位置重錄在對數頻率軸以 dB 平均，不混合複數相位；多位置目標函數以 P0 67%、偏移位置合計 33% 加權。",
                 "擬合可實現的修正曲線；以連續額外衰減懲罰減少切過頭，不把低於目標的頻點直接判成物理凹洞。",
                 "預設以多個候選起點和聯合精修計算；只在取整後收益足夠才保留 Band。計算分數不等於聽感或實測改善。",
                 "參數已按輸入步進取整並重新驗算；這是完整替換設定，不與前一版疊加。"]
    if s["objective_mode"] != "legacy":
        rationale[3] = f"主目標直接使用殘留波峰{'與具支持低處' if s['objective_mode'] == 'shape' else ''}誤差；低處額外減益 {s['low_cut_limit_db']:.3f} dB RMS 在求解中逐位置約束，沒有固定 Band 罰分。"
        rationale[4] = "搜尋涵蓋各剩餘峰與對數頻率起點，交錯新增與精修，再嘗試新增；停止表示本輪未找到達門檻的可行改善，並非全域最優。"
    for key, a in allocations.items():
        if a["protected_max_hz"] is not None:
            rationale.append(f"{key}：鎖定 {a['frozen_count']} Band，新增 {a['new_count']} Band，剩餘 {a['remaining_count']} Band；保留範圍合成曲線變化 {a['bass_drift_db']:.3f} dB（上限 {s['bass_drift_limit_db']:g} dB）。")
        else:
            rationale.append(f"{key}：使用 {a['new_count']} / {s['bands']} Band；只保留有足夠收益的修正。")
        for b in filters[key]:
            if b.get("enabled", True):
                rationale.append(f"{key}：{b['frequency']:g} Hz，{b['gain']:+g} dB，Q {b['q']:g}；此為濾波器 Q，並未辨識房間模態。")
                if b["q"] >= s["max_q"] - s["q_step"]:
                    warnings.append(f"{key} {b['frequency']:g} Hz 的 Q 接近設定上限，表示搜尋受限制；不能視為房間需要相同 Q 的證據。")
        for i, a_band in enumerate(filters[key]):
            for b_band in filters[key][i + 1:]:
                if a_band.get("enabled", True) and b_band.get("enabled", True) and abs(np.log2(a_band["frequency"] / b_band["frequency"])) < 1 / 12:
                    warnings.append(f"{key} {a_band['frequency']:g} / {b_band['frequency']:g} Hz 有重疊 Band；以合成曲線判讀，可能受單段 Gain 或曲線形狀限制，並不代表兩個已辨識模態。")
    rationale += evaluated["allocation"]["skipped"]
    rationale.append("預測曲線以 Baseline 原水平呈現，未扣除前級衰減；補錄比較另顯示含前級的絕對預測。")
    evaluated["rationale"] = rationale
    evaluated["warnings"] = list(dict.fromkeys(warnings + evaluated["warnings"]))
    for curve in evaluated["curves"]:
        curve["name"] = curve["name"].replace("手動調整預測", "PEQ 預測")
    if progress:
        progress(1.)
    return evaluated


def _analysis_config(md):
    """Window origin/sample indices are acquisition timing, not user settings."""
    analysis = md.get("analysis") or {}
    result = {key: analysis[key] for key in ("smoothing", "smoothing_octave_fraction", "fdw_enabled", "fdw_width", "fdw_cycles") if key in analysis}
    windows = analysis.get("windows") or {}
    for key in ("preType", "postType", "preImpulseDurn", "postImpulseDurn", "frequencyDependent", "fdwWidth"):
        if key in windows:
            result["window_" + key] = windows[key]
    return result


def evaluate_peq(measurements, settings, filters, target_level=None) -> dict:
    """Validate and score a manually edited, full-replacement filter set.

    Unlike the optimizer this never scales gains, deletes bands, or changes band
    order to make an unsafe edit pass. It rounds to configured entry steps and
    rejects edits that break acoustic/device constraints. Disabled bands retain
    their parameters but do not affect evidence checks or the combined response.
    Input measurements must be the immutable Baseline of the edited revision.
    """
    measurements = list(measurements)
    s = _settings({**(settings or {}), **({"target_level": target_level} if target_level is not None else {})})
    if any(m.get("applied_peq_id") or m.get("role") == "verification" for m in measurements):
        raise ValueError("手動 PEQ 必須根據原始 Baseline 驗算，不能使用已套用 PEQ 的補錄。")
    report = quality_report(measurements, s)
    errors = [r for r in report if r.get("blocking")]
    if errors:
        raise ValueError("；".join(dict.fromkeys(r["title"] for r in errors)))
    usable = [m for m in measurements if m.get("channel") in ("L", "R")]
    if not usable:
        raise ValueError("請提供已指定 L／R 的 Baseline。")
    lo = max(s["f_min"], max(_arrays(m)[0][0] for m in usable))
    hi = min(s["f_max"], min(_arrays(m)[0][-1] for m in usable))
    if hi <= lo:
        raise ValueError("所選量測沒有共同校正頻段。")
    grid = _log_grid(lo, hi)
    ys, names, repeat = _group_curves(measurements, grid)
    if not any(pos == "P0" for _, pos in names):
        raise ValueError("手動驗算需要中央 P0 的 Baseline。")
    target, reference = _target_reference(usable, s)
    expected_keys = {ch for ch, _ in names} if s["independent"] else {"Shared"}
    if not isinstance(filters, dict) or set(filters) != expected_keys:
        raise ValueError("濾波器聲道必須符合本版左右獨立／共用設定。")
    normalized = {}
    for key, bands in filters.items():
        if not isinstance(bands, (list, tuple)) or len(bands) > s["bands"]:
            raise ValueError(f"{key} 的 Band 數超過本版可用數量。")
        normalized[key] = []
        for index, band in enumerate(bands):
            if not isinstance(band, dict) or band.get("type", "PK") != "PK":
                raise ValueError("手動調整僅支援 Peak / Bell (PK)。")
            if not isinstance(band.get("enabled", True), bool):
                raise ValueError("Band 啟用狀態必須為布林值。")
            if not all(_number(band.get(field)) for field in ("frequency", "gain", "q")):
                raise ValueError("Band 的 Hz／Gain／Q 必須為有效有限數值。")
            fc, gain, q = (float(band[k]) for k in ("frequency", "gain", "q"))
            gain_hi = s["max_boost"] if s["allow_boost"] else 0.
            limits = ((fc, s["f_min"], s["f_max"], s["freq_step"]), (gain, -s["max_cut"], gain_hi, s["gain_step"]), (q, s["min_q"], s["max_q"], s["q_step"]))
            if any(not low <= value <= high for value, low, high, step in limits):
                raise ValueError(f"{key} Band {index + 1} 超過頻率、Gain 或 Q 限制。")
            rounded = [round(round(value / step) * step, 10) for value, low, high, step in limits]
            if any(not low - 1e-9 <= value <= high + 1e-9 for value, (_, low, high, _) in zip(rounded, limits)):
                raise ValueError("依輸入步進取整後超出範圍，請選擇範圍內可輸入的值。")
            fc, gain, q = rounded
            normalized[key].append(dict(frequency=fc, gain=gain, q=q, enabled=band.get("enabled", True), type="PK"))
    sigma = GRID_PPO / 48 / 2.355
    excess = gaussian_filter1d(ys, sigma=sigma, axis=1, mode="nearest") - target
    centers = [b["frequency"] for bands in normalized.values() for b in bands]
    dense = np.unique(np.concatenate((np.geomspace(1, s["sample_rate"] / 2 * .99999, 16384), grid, centers)))
    max_gain, max_attenuation = 0., 0.
    for key, bands in normalized.items():
        ids = [i for i, (ch, _) in enumerate(names) if key == "Shared" or key == ch]
        enabled_boosts = [b for b in bands if b["enabled"] and b["gain"] > 0]
        if enabled_boosts:
            involved_channels = {names[i][0] for i in ids}
            for ch in involved_channels:
                positions = {pos for cc, pos in names if cc == ch}
                if not {"P0", "P-10", "P+10"}.issubset(positions):
                    raise ValueError(f"{ch} 增益需要 P0 與左右 10 cm 的 Baseline 支持；目前只能減益。")
            for band in enabled_boosts:
                fc = band["frequency"]
                if band["q"] > 2 or fc < grid[0] * np.sqrt(2):
                    raise ValueError("增益僅支援 Q ≤ 2 的寬頻修正，且不補校正範圍最低半個八度的自然衰減。")
                core = np.abs(np.log2(grid / fc)) <= min(.2, 1 / band["q"] / 3)
                if not core.any():
                    raise ValueError("增益中心缺少有效量測頻點。")
                support = np.mean((excess[ids][:, core] < -.5) & (excess[ids][:, core] > -4.), axis=1)
                if np.any(support < .8):
                    raise ValueError(f"{key} 的 {fc:g} Hz 增益缺少各位置一致的寬頻凹陷支持，或會填補深凹洞。")
        response = filter_response(dense, bands, s["sample_rate"])
        if response.min(initial=0) < -s["max_total_cut"] - 1e-6 or response.max(initial=0) > _combined_boost_limit(s) + 1e-6:
            raise ValueError("多段濾波器疊加後超出總 Gain 限制，請減少修正幅度。")
        local = filter_response(grid, bands, s["sample_rate"])
        if s["objective_mode"] != "legacy":
            for i in ids:
                below = ys[i] < target
                low_cut = float(np.sqrt(np.mean(np.maximum(-local[below], 0.) ** 2))) if below.any() else 0.
                if low_cut > s["low_cut_limit_db"] + 1e-6:
                    raise ValueError(f"{names[i][0]} · {names[i][1]} 的低處額外減益 {low_cut:.3f} dB RMS 超過本策略 {s['low_cut_limit_db']:.3f} dB 上限。")
            if enabled_boosts:
                from .solver import _Model
                model = _Model(grid, ys[ids], target, s, True, _position_weights([names[i] for i in ids]))
                unsupported = ~model.support
                if unsupported.any() and np.any(local[unsupported] > model.boost_tail_cap[unsupported] + 1e-6):
                    raise ValueError(f"{key} 合成增益在缺少支持的頻點超過允許的尾端影響；請減少增益或調整頻寬。")
        max_gain = max(max_gain, float(response.max(initial=0)))
        max_attenuation = max(max_attenuation, -float(response.min(initial=0)))
    curves, channel_metrics, predictions = [], {}, []
    for i, (ch, pos) in enumerate(names):
        after = ys[i] + filter_response(grid, normalized.get(ch, normalized.get("Shared", [])), s["sample_rate"])
        predictions.append(after)
        before_rmse = float(np.sqrt(np.mean((ys[i] - target) ** 2)))
        after_rmse = float(np.sqrt(np.mean((after - target) ** 2)))
        channel_metrics[f"{ch}:{pos}"] = dict(initial_rmse_db=before_rmse, predicted_rmse_db=after_rmse, improvement_db=before_rmse - after_rmse)
        for kind, yy, label in (("baseline", ys[i], "Baseline"), ("predicted", after, "手動調整預測")):
            curves.append(dict(name=f"{ch} · {pos} {label}", channel=ch, position=pos, kind=kind, frequency=grid.tolist(), spl=yy.tolist()))
    curves.append(dict(name="目標水平", channel="Shared", kind="target", frequency=grid.tolist(), spl=[target] * len(grid)))
    before = float(np.sqrt(np.mean((ys - target) ** 2)))
    after = float(np.sqrt(np.mean((np.asarray(predictions) - target) ** 2)))
    preamp = -math.ceil((max_gain + s["preamp_margin_db"]) * 10) / 10 if max_gain > .01 else 0.
    warnings = [f"{r['title']}：{r['detail']}" for r in report if r["level"] == "warning"]
    if repeat is None:
        warnings.append("缺少同位置重錄，無法量化重現性；請先補錄 A/B。")
    elif before - after <= repeat:
        warnings.append(f"校正範圍 {grid[0]:g}–{grid[-1]:g} Hz 的預測誤差減少 {before - after:.2f} dB，未超過重錄差異 {repeat:.2f} dB；需補錄驗證。")
    if repeat is not None and repeat > 1:
        warnings.append(f"校正範圍 {grid[0]:g}–{grid[-1]:g} Hz 的重錄差異約 {repeat:.2f} dB；請先確認環境與量測設定。")
    if any((m.get("metadata") or {}).get("cal_status") != "loaded" for m in measurements):
        warnings.append("至少一筆量測的麥克風 Cal 未確認，請在 REW 檢查。")
    if any((m.get("metadata") or {}).get("clipping") is None for m in measurements):
        warnings.append("來源未提供完整 Clipping 資訊；預測無法證明原錄製沒有過載。")
    if any(metric["improvement_db"] < -.25 for metric in channel_metrics.values()):
        warnings.append("至少一個聲道／位置的預測誤差增加；請查看個別曲線並重新補錄，不能只看整體平均。")
    for key, bands in normalized.items():
        ids = [i for i, (ch, _) in enumerate(names) if key == "Shared" or key == ch]
        local = filter_response(grid, bands, s["sample_rate"])
        lower = np.any(excess[ids] < 0, axis=0)
        if lower.any() and local[lower].min(initial=0) < -.7:
            warnings.append(f"{key} 在原先低於目標的部分仍有額外減益；請檢查合成曲線與個別位置，這是修正代價而非已識別的物理凹洞。")
    if max_gain > .01:
        warnings.append(f"组合濾波器最高增益 {max_gain:.2f} dB；建議前級 {preamp:.1f} dB，仍需確認喇叭與擴大機餘裕。")
    if s["allow_extended"]:
        warnings.append("已啟用 200 Hz 以上校正；請以偏移量測核對空間一致性。")
    rationale = ["手動修改已按設備步進取整，並重新檢查每段限制、總 Gain 與增益證據；不會為了通過檢查自動改寫其他 Band。",
                 f"採用原 Baseline 與同一個 {target:.2f} dB 目標水平；這是完整替換設定，不疊加上一版。",
                 "預測曲線以 Baseline 原水平呈現，未扣除前級衰減；補錄比較另顯示含前級的絕對預測。",
                 "手動參數尚未經實測驗證；請重新套用這份完整設定並以相同條件補錄。"]
    return dict(filters=normalized, settings=s, target_level=target, target_reference=reference,
                metrics=dict(initial_rmse_db=before, predicted_rmse_db=after, improvement_db=before - after,
                             repeatability_db=repeat, repeatability_definition="同聲道同位置各量測配對差值的 RMS（不移除音量差），再取各組最大值", max_attenuation_db=max_attenuation, max_boost_db=max_gain,
                             channel_metrics=channel_metrics, evaluation_points=len(grid), objective_evaluations=0,
                             evaluated_f_min=float(grid[0]), evaluated_f_max=float(grid[-1]), manual_edit=True),
                rationale=rationale, warnings=warnings, curves=curves, preamp_db=preamp,
                algorithm_version=ALGORITHM_VERSION, replacement=True,
                explanation=_explain_peq(grid, ys, names, target, s, normalized))


def _same_config(a, b):
    if a.keys() != b.keys():
        return False
    for key in a:
        if _number(a[key]) and _number(b[key]):
            if not math.isclose(float(a[key]), float(b[key]), rel_tol=1e-5, abs_tol=1e-6):
                return False
        elif a[key] != b[key]:
            return False
    return True


def _spatial_consistency(groups, s):
    """Current spatial spread only, with no inferred before/after improvement."""
    answer = {}
    for ch in sorted({ch for ch, _ in groups}):
        keys = sorted(key for key in groups if key[0] == ch)
        if len(keys) < 2:
            continue
        ms = [m for key in keys for m in groups[key]]
        lo = max(s["f_min"], max(_arrays(m)[0][0] for m in ms))
        hi = min(s["f_max"], min(_arrays(m)[0][-1] for m in ms))
        if hi <= lo:
            continue
        grid = np.geomspace(lo, hi, 384)
        ys = np.array([np.mean([np.interp(np.log(grid), np.log(_arrays(m)[0]), _arrays(m)[1]) for m in groups[key]], axis=0) for key in keys])
        spread = np.ptp(ys, axis=0)
        answer[ch] = dict(positions=[key[1] for key in keys], rms_spread_db=float(np.sqrt(np.mean(spread ** 2))),
                          p95_spread_db=float(np.percentile(spread, 95)), max_spread_db=float(np.max(spread)),
                          scope="after_only", level_normalized=False)
    return answer


def compare_verification(baselines, measurements, peq, *, allow_mismatch=False) -> dict:
    """Separate volume drift from shape improvement; never stack stored filters."""
    baselines, measurements = list(baselines), list(measurements)
    result = dict(status="incomparable", title="尚無可比較的補錄", details=[], adjustments=[], metrics={}, curves=[],
                  allow_override=False, override_applied=False, reasons=[])
    if not baselines or not measurements:
        result["details"].append("請指定 Baseline 並匯入套用此版 PEQ 後的補錄。")
        return result
    for m in baselines + measurements:
        try:
            _arrays(m)
        except ValueError as exc:
            result["details"].append(str(exc))
            return result
    s = _settings(peq.get("settings", {}))
    groups_before, groups_after = defaultdict(list), defaultdict(list)
    for m in baselines:
        if m.get("channel") in ("L", "R"):
            groups_before[(m["channel"], m.get("position", "P0"))].append(m)
    for m in measurements:
        if m.get("channel") in ("L", "R"):
            if peq.get("id") and m.get("applied_peq_id") != peq["id"]:
                result["details"].append(f"{m.get('name', '')} 未綁定此版 PEQ，已排除。")
                continue
            groups_after[(m["channel"], m.get("position", "P0"))].append(m)
    paired = sorted(groups_before.keys() & groups_after.keys())
    unpaired = sorted(groups_after.keys() - groups_before.keys())
    for ch, pos in unpaired:
        result["details"].append(f"{ch} · {pos} 沒有同位置校正前量測，只能查看目前位置一致性。")
        for m in groups_after[(ch, pos)]:
            f, y = _arrays(m)
            lo, hi = max(s["f_min"], f[0]), min(s["f_max"], f[-1])
            if hi > lo:
                grid = np.geomspace(lo, hi, 384)
                result["curves"].append(dict(name=f"{ch} · {pos} 補錄（無前測）", channel=ch, position=pos, kind="verified", frequency=grid.tolist(), spl=np.interp(np.log(grid), np.log(f), y).tolist()))
    spatial = _spatial_consistency(groups_after, s)
    result["metrics"]["spatial_consistency"] = spatial
    for ch, metric in spatial.items():
        result["details"].append(f"{ch} 目前 {len(metric['positions'])} 個位置的差距：RMS {metric['rms_spread_db']:.2f} dB、95 百分位 {metric['p95_spread_db']:.2f} dB；保留原始水平，須確認各位置播放音量相同。")
    if not paired:
        if groups_after:
            result.update(status="consistency_only", title="偏移補錄缺少同位置前測")
            result["details"].append("請補上相同位置、同一播放路徑且未套用 PEQ 的 Baseline，才能判定校正前後改善。")
        return result
    concerns, unavailable, incompatible = [], set(), []
    missing_pairs = sorted(groups_before.keys() - groups_after.keys())
    for ch, pos in missing_pairs:
        if pos == "P0":
            concerns.append(f"尚缺 {ch} · P0 的此版 PEQ 補錄，不能完成整组驗證。")
    positions, before_errors, after_errors, agreements, drifts = {}, [], [], [], {}
    target = float(peq.get("target_level", 0))
    preamp = float(peq.get("preamp_db", 0) or 0)
    band_evidence = []
    for ch, pos in paired:
        before_ms, after_ms = groups_before[(ch, pos)], groups_after[(ch, pos)]
        all_ms = before_ms + after_ms
        lo = max(s["f_min"], max(_arrays(m)[0][0] for m in all_ms))
        hi = min(s["f_max"], min(_arrays(m)[0][-1] for m in all_ms))
        if hi <= lo or not all(_covers(_arrays(m)[0], s["f_min"], s["f_max"]) for m in all_ms):
            result["details"].append(f"{ch} · {pos} 補錄未涵蓋完整校正頻段；缺少的頻響資料不能以略過檢查補足。")
            result["reasons"] = list(result["details"])
            result["curves"] = []
            result["metrics"] = {}
            return result
        grid = np.geomspace(lo, hi, 512)
        yb = np.mean([np.interp(np.log(grid), np.log(_arrays(m)[0]), _arrays(m)[1]) for m in before_ms], axis=0)
        ya = np.mean([np.interp(np.log(grid), np.log(_arrays(m)[0]), _arrays(m)[1]) for m in after_ms], axis=0)
        response = filter_response(grid, peq.get("filters", {}).get(ch, peq.get("filters", {}).get("Shared", [])), s["sample_rate"])
        expected = yb + response + preamp
        # Estimate drift from the residual to the *known* EQ response, preferably
        # where its magnitude is smallest; preserve actual shape and L/R levels.
        anchor = np.abs(response) <= np.percentile(np.abs(response), 30) + 1e-9
        drift = float(np.median((ya - expected)[anchor]))
        adjusted = ya - preamp - drift
        # Independently centered shape score is invariant to a pure volume shift.
        # Absolute scores are included too but never classify improvement alone.
        err_b = float(np.sqrt(np.mean(((yb - target) - np.mean(yb - target)) ** 2)))
        err_a = float(np.sqrt(np.mean(((adjusted - target) - np.mean(adjusted - target)) ** 2)))
        agreement = float(np.sqrt(np.mean((adjusted - (yb + response)) ** 2)))
        key = f"{ch}:{pos}"
        drifts[key] = drift
        positions[key] = dict(shape_error_before_db=err_b, shape_error_after_db=err_a, improvement_db=err_b - err_a,
                              predicted_agreement_db=agreement, volume_offset_db=drift,
                              absolute_error_before_db=float(np.sqrt(np.mean((yb - target) ** 2))),
                              absolute_error_after_db=float(np.sqrt(np.mean((ya - target) ** 2))))
        before_errors.append(err_b)
        after_errors.append(err_a)
        agreements.append(agreement)
        for band in peq.get("filters", {}).get(ch, peq.get("filters", {}).get("Shared", [])):
            if not band.get("enabled", True):
                continue
            fc = float(band["frequency"])
            mask = np.abs(np.log2(grid / fc)) <= min(.25, 1 / max(float(band["q"]), .1) / 3)
            if not mask.any():
                continue
            band_evidence.append(dict(channel=ch, position=pos, frequency=fc, gain=float(band["gain"]),
                                      residual_db=float(np.median((adjusted - (yb + response))[mask])),
                                      target_excess_db=float(np.median((adjusted - target)[mask])),
                                      before_excess_db=float(np.median((yb - target)[mask]))))
        for kind, yy, label in (("baseline", yb, "Baseline"), ("predicted", expected, "PEQ 預測含前級"), ("verified", ya, "補錄原始"), ("verified_aligned", adjusted, "補錄扣除前級與電平差")):
            result["curves"].append(dict(name=f"{ch} · {pos} {label}", channel=ch, position=pos, kind=kind, frequency=grid.tolist(), spl=yy.tolist()))
        if abs(drift) > 1:
            concerns.append(f"{ch} · {pos} 估計有 {drift:+.2f} dB 額外電平差；請確認音量、麥克風位置與 DSP 播放路徑。")
        for field, label in (("sample_rate", "取樣率"), ("sweep_length", "掃頻長度"), ("sweep_level_dbfs", "掃頻輸出"), ("input_device", "輸入裝置"), ("output_device", "輸出裝置"), ("cal_name", "麥克風 Cal"), ("cal_fingerprint", "Cal 內容"), ("output_volume", "REW 輸出音量"), ("timing_reference", "時間參考")):
            bv = {(m.get("metadata") or {}).get(field) for m in before_ms if (m.get("metadata") or {}).get(field) not in (None, "")}
            av = {(m.get("metadata") or {}).get(field) for m in after_ms if (m.get("metadata") or {}).get(field) not in (None, "")}
            if not bv or not av:
                if field not in ("cal_fingerprint", "output_volume"):
                    unavailable.add(label)
            elif bv != av or len(bv) != 1 or len(av) != 1:
                message = f"{ch} · {pos} 的{label}與 Baseline 不一致。"
                concerns.append(message)
                if field in ("input_device", "output_device", "cal_name", "cal_fingerprint"):
                    incompatible.append(message)
        for field, label in (("input_device", "所選輸入裝置"), ("output_device", "所選輸出裝置"), ("volume_note", "主音量紀錄"), ("mic_orientation", "麥克風朝向"), ("route_note", "DSP 播放路徑")):
            # A current project's device selection may belong to a different
            # computer; it is not evidence of the imported recording's setup.
            def session_values(ms):
                values = set()
                for m in ms:
                    md = m.get("metadata") or {}
                    session = md.get("session_conditions") or {}
                    if md.get("conditions_source") == "project_record" or session.get("conditions_source") == "project_record":
                        continue
                    if session.get(field) not in (None, ""):
                        values.add(str(session[field]))
                return values
            bv, av = session_values(before_ms), session_values(after_ms)
            if bv and av and (bv != av or len(bv) != 1 or len(av) != 1):
                message = f"{ch} · {pos} 的{label}紀錄已改變；專案條件紀錄來自使用者，並非檔案自動偵測。"
                concerns.append(message)
                if field != "volume_note":
                    incompatible.append(message)
            elif bool(bv) != bool(av):
                unavailable.add(label)
        analyses = [_analysis_config(m.get("metadata") or {}) for m in all_ms]
        if any(analyses) and not all(_same_config(a, analyses[0]) for a in analyses):
            concerns.append(f"{ch} · {pos} 的平滑或視窗設定不同，請先在 REW 統一分析條件。")
        if any((m.get("metadata") or {}).get("cal_status") != "loaded" for m in all_ms):
            unavailable.add("逐筆麥克風 Cal 載入狀態")
        if any((m.get("metadata") or {}).get("clipping") is True for m in all_ms):
            message = f"{ch} · {pos} 的 Baseline 或補錄有 Clipping 記錄，強烈建議重新量測；不能直接確認校正效果。"
            concerns.append(message)
            incompatible.append(message)
        if any((m.get("metadata") or {}).get("clipping") is None for m in all_ms):
            unavailable.add("Clipping")
    if not before_errors:
        result["details"].extend(concerns)
        return result
    delta = float(np.mean(before_errors) - np.mean(after_errors))
    repeat = peq.get("metrics", {}).get("repeatability_db")
    threshold = max(.25, float(repeat) if _number(repeat) else .5)
    worst_improvement = min(metric["improvement_db"] for metric in positions.values())
    shape_assessment = "worse" if worst_improvement < -threshold else "improved" if delta > threshold else "unchanged"
    status = shape_assessment
    condition_concerns = list(concerns)
    for key, metric in positions.items():
        if metric["improvement_db"] < -threshold:
            concerns.append(f"{key.replace(':', ' · ')} 曲線形狀惡化 {-metric['improvement_db']:.2f} dB；不能以其他位置的平均改善抵銷，請優先檢查此位置。")
    labels = dict(improved="補錄曲線形狀有改善", worse="補錄曲線形狀變差", unchanged="改善未超過可確認門檻")
    if incompatible:
        status = "incomparable"
        title = "量測條件已改變，需要可比的補錄"
    elif concerns or unavailable:
        status = "needs_confirmation"
        title = "先確認量測條件，再判定改善"
    else:
        title = labels[status]
    missing_primary = any(pos == "P0" for _, pos in missing_pairs)
    override_possible = bool((incompatible or condition_concerns or unavailable) and not missing_primary)
    result["allow_override"] = override_possible
    if allow_mismatch and override_possible:
        status, title = "provisional", "已略過條件檢查：此為暫時比較，尚未驗證"
        result["override_applied"] = True
    result.update(status=status, title=title)
    result["metrics"] = dict(shape_error_before_db=float(np.mean(before_errors)), shape_error_after_db=float(np.mean(after_errors)),
                             improvement_db=delta, predicted_agreement_db=float(np.mean(agreements)), volume_offset_db=drifts,
                             positions=positions, improvement_threshold_db=threshold, shape_assessment=shape_assessment,
                             worst_position_improvement_db=worst_improvement, spatial_consistency=spatial)
    result["details"].append(f"曲線形狀誤差變化 {delta:+.2f} dB，判定門檻 {threshold:.2f} dB；已將整體音量變化與形狀改善分開。")
    result["details"].extend(dict.fromkeys(concerns))
    if unavailable:
        result["details"].append("無法自動確認：" + "、".join(sorted(unavailable)) + "；請在 REW 與環境清單核對。")
    result["reasons"] = list(dict.fromkeys(concerns)) + (["無法自動確認：" + "、".join(sorted(unavailable))] if unavailable else [])
    if result["override_applied"]:
        result["details"].append("使用者已選擇略過條件檢查；數值僅供觀察，不將此方案標示為已驗證或確認改善。")
    if np.mean(agreements) > max(1., threshold):
        result["details"].append("補錄與濾波器預測有明顯差異；檢查 PEQ 是否確實套用、Band 參數與訊號路徑。")
    if delta <= threshold or worst_improvement < -threshold:
        result["details"].append("暫不追加濾波器；保留或調整原方案後，從 Baseline 重新產生完整替換設定。")
    confidence_problem = bool(concerns or unavailable)
    for evidence in band_evidence:
        mismatch = evidence["residual_db"]
        remaining = evidence["target_excess_db"]
        if abs(mismatch) > max(1., threshold):
            suggestion = "先核對這一段的 Hz / Gain / Q 與 DSP 播放路徑，再以原條件補錄。"
            reason = f"{evidence['position']} 實測與預測在這段相差約 {mismatch:+.2f} dB；現階段無法判定是參數或量測條件造成。"
        elif confidence_problem:
            continue
        elif evidence["gain"] < 0 and remaining < -max(1., threshold) and evidence["before_excess_db"] >= -.5:
            suggestion = "考慮縮小原段減益，再重新驗證；優先避免切過頭。"
            reason = f"{evidence['position']} 原先高於或接近目標，補錄在此段低於目標約 {-remaining:.2f} dB。"
        elif evidence["gain"] < 0 and remaining > max(1., threshold) and abs(evidence["gain"]) + s["gain_step"] <= s["max_cut"]:
            suggestion = "若各位置都呈現相同凸峰，可小幅加深原段減益；重新檢查總衰減限制後再補錄。"
            reason = f"{evidence['position']} 此段仍高於目標约 {remaining:.2f} dB，且預測與實測接近。"
        else:
            continue
        result["adjustments"].append(dict(channel=evidence["channel"], frequency=evidence["frequency"], position=evidence["position"], suggestion=suggestion, reason=reason))
    if not result["adjustments"]:
        result["details"].append("目前沒有證據支持修改特定 Band；保留原設定，先補齊可比量測或確認已低於重錄差異。")
    if unpaired:
        result["details"].append("未配對的偏移位置不能宣稱校正前後改善；建議補齊同位置 Baseline。")
    if s["allow_boost"]:
        expected_keys = {(ch, pos) for ch, _ in groups_before for pos in ("P0", "P-10", "P+10")}
        if not expected_keys.issubset(set(paired)):
            result["details"].append("增益精校前請补齊 P0、左右 10 cm 各位置的校正前與校正後量測；目前維持減益策略較有依據。")
    return result
