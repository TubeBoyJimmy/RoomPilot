"""Conservative, reproducible PEQ analysis of immutable REW measurements.

This module predicts magnitude responses; it does not capture/play audio and does
not claim an EQ prediction is a verified acoustic result. RBJ peaking coefficients:
https://www.w3.org/TR/audio-eq-cookbook/ . Every reported score uses the rounded,
device-enterable filters, evaluated on a logarithmic frequency grid in float64.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks

ALGORITHM_VERSION = "roompilot-peq-1.0"
DEFAULTS = dict(bands=5, independent=True, f_min=30.0, f_max=200.0,
                max_cut=6.0, max_total_cut=9.0, min_q=0.4, max_q=6.0,
                gain_step=0.1, freq_step=1.0, q_step=0.01,
                target_level=None, mode="standard", allow_boost=False,
                max_boost=3.0, allow_extended=False, sample_rate=48000.0)


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
                measurement_ids=[str(m.get("id", "")) for m in measurements])


def _covers(f, lo, hi):
    # REW FFT bins seldom land exactly on the requested sweep boundary. Accept
    # at most one adjacent bin, capped to 1%, then *clamp* the analysis domain.
    tolerance_lo = min(float(f[1] - f[0]), lo * .01) + 1e-9
    tolerance_hi = min(float(f[-1] - f[-2]), hi * .01) + 1e-9
    return f[0] <= lo + tolerance_lo and f[-1] >= hi - tolerance_hi


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
            report.append(_item("clipping", "error", "記錄顯示 Clipping", "請降低錄製或播放電平，重新量測。", [m]))
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
                spread = float(np.sqrt(np.mean(np.var(ys, axis=0))) * np.sqrt(2))
                report.append(_item("repeatability", "warning" if spread > 1 else "pass", f"{ch} · {pos} 重錄一致性", f"重錄差異約 {spread:.2f} dB RMS（未移除音量差）；1 dB 為可調整的產品提醒門檻。", ms))
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
    if not isinstance(s["bands"], int) or isinstance(s["bands"], bool) or not 1 <= s["bands"] <= 20:
        raise ValueError("Band 數必須為 1–20 的整數。")
    for key in ("independent", "allow_boost", "allow_extended"):
        if not isinstance(s[key], bool):
            raise ValueError(key + " 必須為布林值。")
    for key in ("f_min", "f_max", "max_cut", "max_total_cut", "min_q", "max_q", "gain_step", "freq_step", "q_step", "max_boost", "sample_rate"):
        if not _number(s[key]) or s[key] <= 0:
            raise ValueError(key + " 必須為有限正數。")
        s[key] = float(s[key])
    if s["mode"] not in ("standard", "deep"):
        raise ValueError("搜尋模式必須為 standard 或 deep。")
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
            repeats.append(float(np.sqrt(np.mean(np.var(yy, axis=0))) * np.sqrt(2)))
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


def _guard_filters(bands, s, grid, deep_dip):
    """Project rounded gains toward zero until combined constraints hold."""
    bands = _rounded(bands, s)
    full = np.unique(np.concatenate((np.geomspace(1, s["sample_rate"] / 2 * .99999, 4096), grid, [b["frequency"] for b in bands])))
    # A 0.7 dB deep-dip guard applies independently to every position/channel.
    for _ in range(300):
        response = filter_response(full, bands, s["sample_rate"])
        local = filter_response(grid, bands, s["sample_rate"])
        violation = (response.min(initial=0) < -s["max_total_cut"] + .015 or response.max(initial=0) > s["max_boost"] + .00001 or
                     (deep_dip.any() and local[deep_dip].min(initial=0) < -.7))
        if not violation:
            break
        for b in bands:
            sign = 1 if b["gain"] > 0 else -1
            units = math.floor(max(0., abs(b["gain"]) * .96 / s["gain_step"] - 1e-8))
            b["gain"] = round(sign * units * s["gain_step"], 10)
        bands = [b for b in bands if abs(b["gain"]) >= s["gain_step"] * .5]
    return bands


def _fit(grid, ys, target, s, boost_allowed, cancel, progress):
    n = len(grid)
    # Slight smoothing suppresses sub-resolution numerical texture (1/48 octave).
    sigma = max(.4, n / max(np.log2(grid[-1] / grid[0]), .1) / 48 / 2.355)
    smooth = gaussian_filter1d(ys, sigma=sigma, axis=1, mode="nearest")
    excess = smooth - target
    desired = -np.maximum(excess - .25, 0)
    deep_dip = np.any(excess < -3, axis=0)
    boost_mask = np.all((excess < -.8) & (excess > -4), axis=0)
    # Exclude the bottom half octave: natural bass roll-off is not boost evidence.
    boost_mask &= grid >= grid[0] * np.sqrt(2)
    if boost_allowed:
        desired[:, boost_mask] = np.minimum(-excess[:, boost_mask] - .25, s["max_boost"])
    desired = np.clip(desired, -s["max_total_cut"], s["max_boost"] if boost_allowed else 0)
    bands = []
    rng = np.random.default_rng(731)
    evaluations = 0

    def unpack(x):
        return [dict(frequency=float(np.exp(row[0])), gain=float(row[1]), q=float(np.exp(row[2])), enabled=True, type="PK") for row in np.reshape(x, (-1, 3))]

    def pack(bs):
        return np.array([[np.log(b["frequency"]), b["gain"], np.log(b["q"])] for b in bs]).ravel()

    def residual_for(bs):
        nonlocal evaluations
        evaluations += 1
        _cancelled(cancel)
        r = filter_response(grid, bs, s["sample_rate"])
        err = (r[None, :] - desired).ravel() / np.sqrt(len(ys))
        null_penalty = np.minimum(r + .65, 0) * deep_dip * 18
        total_penalty = np.minimum(r + s["max_total_cut"] - .05, 0) * 12
        boost_penalty = np.maximum(r - s["max_boost"], 0) * 12
        unsupported_boost = np.maximum(r, 0) * (~boost_mask) * (4 if boost_allowed else 12)
        return np.concatenate((err, null_penalty, total_penalty, boost_penalty, unsupported_boost, .06 * r))

    def score(bs):
        rr = residual_for(bs)
        return float(rr @ rr / n + .025 * len(bs))

    best_score = score([])
    for index in range(s["bands"]):
        _cancelled(cancel)
        current = filter_response(grid, bands, s["sample_rate"])
        need = np.mean(desired, axis=0) - current
        strength = np.abs(need) if boost_allowed else np.maximum(-need, 0)
        candidates, _ = find_peaks(strength, distance=max(2, n // 30))
        candidates = list(candidates) + [0, n - 1, int(np.argmax(strength))]
        candidates = sorted(set(candidates), key=lambda j: strength[j], reverse=True)
        if strength[candidates[0]] < .65:
            break
        best = None
        starts = 4 if s["mode"] == "standard" else 12
        for attempt in range(starts):
            ci = candidates[min(attempt // 2, len(candidates) - 1)]
            is_boost = need[ci] > 0 and boost_allowed and boost_mask[ci]
            if need[ci] > 0 and not is_boost:
                continue
            q_hi = min(s["max_q"], 2.) if is_boost else s["max_q"]
            if q_hi < s["min_q"]:
                continue
            gain_lo, gain_hi = ((.001, s["max_boost"]) if is_boost else (-s["max_cut"], -.001))
            q_guess = np.clip([1., 3., 1.8, 5.][attempt % 4], s["min_q"], q_hi)
            center = float(grid[ci])
            if attempt >= 4:
                center = np.clip(center * np.exp(rng.normal(0, .08)), s["f_min"], s["f_max"])
            seed = dict(frequency=center, gain=float(np.clip(need[ci], gain_lo, gain_hi)), q=float(q_guess))
            lower = np.array([np.log(s["f_min"]), gain_lo, np.log(s["min_q"])])
            upper = np.array([np.log(s["f_max"]), gain_hi, np.log(q_hi)])
            if np.any(upper <= lower):
                # Fixed Q is implemented by a narrow numerical interval, then rounded back.
                upper = np.maximum(upper, lower + 1e-9)
            x0 = np.clip(pack([seed]), lower + 1e-12, upper - 1e-12)
            fit = least_squares(lambda x: residual_for(bands + unpack(x)), x0, bounds=(lower, upper), max_nfev=65 if s["mode"] == "standard" else 140, ftol=1e-6, xtol=1e-6, gtol=1e-6)
            trial = _guard_filters(bands + unpack(fit.x), s, grid, deep_dip)
            value = score(trial)
            if value < best_score - .015 and (best is None or value < best[0]):
                best = (value, trial)
        if best is None:
            break
        best_score, bands = best
        if progress:
            progress((index + 1) / s["bands"])
    # Joint local refinement after greedy multi-start allocation. Keep the rounded
    # incumbent unless the rounded replacement improves the same objective.
    if bands:
        low, high = [], []
        for b in bands:
            lo_g, hi_g = ((.001, s["max_boost"]) if b["gain"] > 0 else (-s["max_cut"], -.001))
            qhi = min(2., s["max_q"]) if b["gain"] > 0 else s["max_q"]
            low.extend([np.log(s["f_min"]), lo_g, np.log(s["min_q"])])
            high.extend([np.log(s["f_max"]), hi_g, np.log(qhi)])
        lower, upper = np.array(low), np.maximum(high, np.array(low) + 1e-9)
        x0 = np.clip(pack(bands), lower + 1e-12, upper - 1e-12)
        fit = least_squares(lambda x: residual_for(unpack(x)), x0, bounds=(lower, upper), max_nfev=90 if s["mode"] == "standard" else 220, ftol=1e-6, xtol=1e-6, gtol=1e-6)
        trial = _guard_filters(unpack(fit.x), s, grid, deep_dip)
        if score(trial) < best_score:
            bands = trial
    # Consolidate nearly identical bands when the device-rounded single band
    # keeps the same fit and limits. Cascaded bells are not algebraically equal
    # to a single bell, so the merged response must be evaluated again.
    merged = True
    while merged:
        merged = False
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                a, b = bands[i], bands[j]
                gain = a["gain"] + b["gain"]
                if (a["gain"] * b["gain"] <= 0 or abs(np.log2(a["frequency"] / b["frequency"])) > 1 / 24 or
                        abs(np.log(a["q"] / b["q"])) > .2 or not -s["max_cut"] <= gain <= s["max_boost"]):
                    continue
                weight = abs(a["gain"]) / (abs(a["gain"]) + abs(b["gain"]))
                combined = dict(frequency=np.exp(weight * np.log(a["frequency"]) + (1 - weight) * np.log(b["frequency"])),
                                gain=gain, q=np.exp(weight * np.log(a["q"]) + (1 - weight) * np.log(b["q"])))
                trial = _guard_filters([band for k, band in enumerate(bands) if k not in (i, j)] + [combined], s, grid, deep_dip)
                if score(trial) <= score(bands) + .025:
                    bands, merged = trial, True
                    break
            if merged:
                break
    # Redundant bands must earn their place after rounding.
    for i in range(len(bands) - 1, -1, -1):
        reduced = bands[:i] + bands[i + 1:]
        if score(reduced) <= score(bands) + .01:
            bands = reduced
    return bands, evaluations


def generate_peq(measurements, settings, progress=None, cancel=None) -> dict:
    """Return a complete replacement PEQ; never append to an applied revision."""
    measurements = list(measurements)
    s = _settings(settings)
    if any(m.get("applied_peq_id") or m.get("role") == "verification" for m in measurements):
        raise ValueError("PEQ 必須由原始 Baseline 計算。補錄用來驗證，不能直接疊加新的濾波器。")
    errors = [i for i in quality_report(measurements, s) if i["level"] == "error"]
    if errors:
        raise ValueError("；".join(dict.fromkeys(i["title"] for i in errors)))
    usable = [m for m in measurements if m.get("channel") in ("L", "R")]
    if not usable:
        raise ValueError("請先將量測指定為 L 或 R；L+R 不能代替獨立聲道。")
    low = max(s["f_min"], max(_arrays(m)[0][0] for m in usable))
    high = min(s["f_max"], min(_arrays(m)[0][-1] for m in usable))
    if high <= low:
        raise ValueError("所選量測沒有共同校正頻段。")
    grid = np.geomspace(low, high, 384 if s["mode"] == "standard" else 768)
    ys, names, repeat = _group_curves(measurements, grid)
    if not any(pos == "P0" for _, pos in names):
        raise ValueError("請提供中央位置 P0 的 Baseline。")
    upper = grid >= np.sqrt(s["f_min"] * s["f_max"])
    target = s["target_level"]
    if target is None:
        # Shared absolute level: no per-channel leveling; upper correction range
        # avoids dragging the reference down with natural low-frequency roll-off.
        target = float(np.percentile(np.median(ys[:, upper], axis=0), 35))
    warnings = []
    if repeat is None:
        warnings.append("缺少同位置重錄，無法量化重現性；建議先補錄 A/B。")
    elif repeat > 1:
        warnings.append(f"重錄差異約 {repeat:.2f} dB；請先確認環境與設定，細微改善可能不可靠。")
    if any((m.get("metadata") or {}).get("cal_status") != "loaded" for m in measurements):
        warnings.append("至少一筆量測的麥克風 Cal 未確認，建議先在 REW 檢查。")
    if any((m.get("metadata") or {}).get("clipping") is None for m in measurements):
        warnings.append("來源未提供完整 Clipping 資訊，預測不能證明原錄製沒有過載。")
    filters = {}
    total_evals = 0
    groups = [(ch, [i for i, (channel, _) in enumerate(names) if channel == ch]) for ch in sorted({c for c, _ in names})] if s["independent"] else [("Shared", list(range(len(names))))]
    for gi, (key, ids) in enumerate(groups):
        positions = {ch: {pos for cc, pos in names if cc == ch} for ch in {names[i][0] for i in ids}}
        boost_ready = s["allow_boost"] and all({"P0", "P-10", "P+10"}.issubset(ps) for ps in positions.values())
        if s["allow_boost"] and not boost_ready:
            warnings.append(f"{key} 缺少 P0 與左右 10 cm 的 Baseline；本輪只減益，保留增益選項供多位置驗證後使用。")
        if s["allow_boost"] and s["min_q"] > 2:
            warnings.append("增益僅允許 Q ≤ 2 的寬頻修正；目前 Q 下限不符，因此不產生增益。")
            boost_ready = False
        callback = (lambda value, gi=gi: progress((gi + value * .95) / len(groups))) if progress else None
        bands, evaluations = _fit(grid, ys[ids], target, s, boost_ready, cancel, callback)
        filters[key] = bands
        total_evals += evaluations
    predicted, curves, channel_metrics = [], [], {}
    for i, (ch, pos) in enumerate(names):
        response = filter_response(grid, filters.get(ch, filters.get("Shared", [])), s["sample_rate"])
        after = ys[i] + response
        predicted.append(after)
        before_rmse = float(np.sqrt(np.mean((ys[i] - target) ** 2)))
        after_rmse = float(np.sqrt(np.mean((after - target) ** 2)))
        channel_metrics[f"{ch}:{pos}"] = dict(initial_rmse_db=before_rmse, predicted_rmse_db=after_rmse, improvement_db=before_rmse - after_rmse)
        for kind, values, label in (("baseline", ys[i], "Baseline"), ("predicted", after, "PEQ 預測")):
            curves.append(dict(name=f"{ch} · {pos} {label}", channel=ch, position=pos, kind=kind, frequency=grid.tolist(), spl=values.tolist()))
    curves.append(dict(name="目標水平", channel="Shared", kind="target", frequency=grid.tolist(), spl=[target] * len(grid)))
    dense = np.unique(np.concatenate([np.geomspace(1, s["sample_rate"] / 2 * .99999, 8192)] + [np.array([b["frequency"] for b in fs]) for fs in filters.values()]))
    responses = [filter_response(dense, fs, s["sample_rate"]) for fs in filters.values()]
    max_gain = max(0., max(float(r.max()) for r in responses))
    max_attenuation = max(0., -min(float(r.min()) for r in responses))
    preamp = -math.ceil((max_gain + .5) * 10) / 10 if max_gain > .01 else 0.
    before = float(np.sqrt(np.mean((ys - target) ** 2)))
    after = float(np.sqrt(np.mean((np.asarray(predicted) - target) ** 2)))
    rationale = [f"校正 {s['f_min']:g}–{s['f_max']:g} Hz，使用同一個 {target:.2f} dB 目標水平比較左右聲道。",
                 "同位置重錄在對數頻率軸以 dB 平均；不混合未對齊的複數相位。",
                 "先處理凸峰，保護原有深凹洞與低頻自然衰減；Band 必須帶來可量化改善才保留。",
                 "參數已依輸入步進取整並重新驗算；這是完整替換設定，不與前一版疊加。"]
    for key, fs in filters.items():
        for b in fs:
            rationale.append(f"{key}：{b['frequency']:g} Hz、{b['gain']:+g} dB、Q {b['q']:g}，" + ("削減量測中的凸峰，需以補錄驗證。" if b["gain"] < 0 else "多位置支持的寬頻補償；套用前級衰減後再驗證。"))
    rationale.append("此頁預測曲線以 Baseline 原水平呈現，未扣除前級衰減；補錄比較另顯示含前級的絕對預測。")
    if not any(filters.values()):
        rationale.append("在目前限制下沒有足夠收益的濾波器，建議保留現況或檢查量測／目標水平。")
    if repeat is not None and before - after <= repeat:
        warnings.append("預測改善未超過重錄差異；請用相同條件補錄確認，勿將小數差異當成確定改善。")
    if max_gain > .01:
        warnings.append(f"組合濾波器最高增益 {max_gain:.2f} dB；建議前級 {preamp:.1f} dB。實際喇叭與擴大機餘裕仍需確認。")
    if s["allow_extended"]:
        warnings.append("已啟用 200 Hz 以上校正，單點頻響不足以判定方向性或反射問題；請核對偏移位置。")
    if progress:
        progress(1.)
    return dict(filters=filters, settings=s, target_level=target,
                metrics=dict(initial_rmse_db=before, predicted_rmse_db=after, improvement_db=before - after,
                             repeatability_db=repeat, max_attenuation_db=max_attenuation, max_boost_db=max_gain,
                             channel_metrics=channel_metrics, evaluation_points=len(grid), objective_evaluations=total_evals,
                             evaluated_f_min=float(grid[0]), evaluated_f_max=float(grid[-1])),
                rationale=rationale, warnings=list(dict.fromkeys(warnings)), curves=curves, preamp_db=preamp,
                algorithm_version=ALGORITHM_VERSION, replacement=True)


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
    errors = [r for r in quality_report(measurements, s) if r["level"] == "error"]
    if errors:
        raise ValueError("；".join(dict.fromkeys(r["title"] for r in errors)))
    usable = [m for m in measurements if m.get("channel") in ("L", "R")]
    if not usable:
        raise ValueError("請提供已指定 L／R 的 Baseline。")
    lo = max(s["f_min"], max(_arrays(m)[0][0] for m in usable))
    hi = min(s["f_max"], min(_arrays(m)[0][-1] for m in usable))
    if hi <= lo:
        raise ValueError("所選量測沒有共同校正頻段。")
    grid = np.geomspace(lo, hi, 384 if s["mode"] == "standard" else 768)
    ys, names, repeat = _group_curves(measurements, grid)
    if not any(pos == "P0" for _, pos in names):
        raise ValueError("手動驗算需要中央 P0 的 Baseline。")
    target = s["target_level"]
    if target is None:
        upper = grid >= np.sqrt(s["f_min"] * s["f_max"])
        target = float(np.percentile(np.median(ys[:, upper], axis=0), 35))
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
    n = len(grid)
    sigma = max(.4, n / max(np.log2(grid[-1] / grid[0]), .1) / 48 / 2.355)
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
        if response.min(initial=0) < -s["max_total_cut"] - 1e-6 or response.max(initial=0) > (s["max_boost"] if s["allow_boost"] else 0.) + 1e-6:
            raise ValueError("多段濾波器疊加後超出總 Gain 限制，請減少修正幅度。")
        local = filter_response(grid, bands, s["sample_rate"])
        deep_dip = np.any(excess[ids] < -3., axis=0)
        if deep_dip.any() and local[deep_dip].min(initial=0) < -.70001:
            raise ValueError(f"{key} 這項修改會在原有深凹洞再衰減超過 0.7 dB；請調整中心頻率、減益或 Q。")
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
    preamp = -math.ceil((max_gain + .5) * 10) / 10 if max_gain > .01 else 0.
    warnings = []
    if repeat is None:
        warnings.append("缺少同位置重錄，無法量化重現性；請先補錄 A/B。")
    elif before - after <= repeat:
        warnings.append(f"預測改善 {before - after:.2f} dB 未超過重錄差異 {repeat:.2f} dB；需補錄驗證。")
    if repeat is not None and repeat > 1:
        warnings.append(f"重錄差異約 {repeat:.2f} dB；請先確認環境與量測設定。")
    if any((m.get("metadata") or {}).get("cal_status") != "loaded" for m in measurements):
        warnings.append("至少一筆量測的麥克風 Cal 未確認，請在 REW 檢查。")
    if any((m.get("metadata") or {}).get("clipping") is None for m in measurements):
        warnings.append("來源未提供完整 Clipping 資訊；預測無法證明原錄製沒有過載。")
    if any(metric["improvement_db"] < -.25 for metric in channel_metrics.values()):
        warnings.append("至少一個聲道／位置的預測誤差增加；請查看個別曲線並重新補錄，不能只看整體平均。")
    if max_gain > .01:
        warnings.append(f"组合濾波器最高增益 {max_gain:.2f} dB；建議前級 {preamp:.1f} dB，仍需確認喇叭與擴大機餘裕。")
    if s["allow_extended"]:
        warnings.append("已啟用 200 Hz 以上校正；請以偏移量測核對空間一致性。")
    rationale = ["手動修改已按設備步進取整，並重新檢查每段限制、總 Gain、深凹洞與增益證據。",
                 f"採用原 Baseline 與同一個 {target:.2f} dB 目標水平；這是完整替換設定，不疊加上一版。",
                 "預測曲線以 Baseline 原水平呈現，未扣除前級衰減；補錄比較另顯示含前級的絕對預測。",
                 "手動參數尚未經實測驗證；請重新套用這份完整設定並以相同條件補錄。"]
    return dict(filters=normalized, settings=s, target_level=target,
                metrics=dict(initial_rmse_db=before, predicted_rmse_db=after, improvement_db=before - after,
                             repeatability_db=repeat, max_attenuation_db=max_attenuation, max_boost_db=max_gain,
                             channel_metrics=channel_metrics, evaluation_points=len(grid), objective_evaluations=0,
                             evaluated_f_min=float(grid[0]), evaluated_f_max=float(grid[-1]), manual_edit=True),
                rationale=rationale, warnings=warnings, curves=curves, preamp_db=preamp,
                algorithm_version=ALGORITHM_VERSION, replacement=True)


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


def compare_verification(baselines, measurements, peq) -> dict:
    """Separate volume drift from shape improvement; never stack stored filters."""
    baselines, measurements = list(baselines), list(measurements)
    result = dict(status="incomparable", title="尚無可比較的補錄", details=[], adjustments=[], metrics={}, curves=[])
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
        if hi <= lo:
            concerns.append(f"{ch} · {pos} 沒有共同校正頻段。")
            continue
        if not all(_covers(_arrays(m)[0], s["f_min"], s["f_max"]) for m in all_ms):
            concerns.append(f"{ch} · {pos} 補錄未涵蓋完整校正頻段。")
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
            bv = {str(((m.get("metadata") or {}).get("session_conditions") or {}).get(field)) for m in before_ms if ((m.get("metadata") or {}).get("session_conditions") or {}).get(field) not in (None, "")}
            av = {str(((m.get("metadata") or {}).get("session_conditions") or {}).get(field)) for m in after_ms if ((m.get("metadata") or {}).get("session_conditions") or {}).get(field) not in (None, "")}
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
        if any((m.get("metadata") or {}).get("clipping") is True for m in after_ms):
            message = f"{ch} · {pos} 補錄有 Clipping 記錄，請重新量測。"
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
    result.update(status=status, title=title)
    result["metrics"] = dict(shape_error_before_db=float(np.mean(before_errors)), shape_error_after_db=float(np.mean(after_errors)),
                             improvement_db=delta, predicted_agreement_db=float(np.mean(agreements)), volume_offset_db=drifts,
                             positions=positions, improvement_threshold_db=threshold, shape_assessment=shape_assessment,
                             worst_position_improvement_db=worst_improvement, spatial_consistency=spatial)
    result["details"].append(f"曲線形狀誤差變化 {delta:+.2f} dB，判定門檻 {threshold:.2f} dB；已將整體音量變化與形狀改善分開。")
    result["details"].extend(dict.fromkeys(concerns))
    if unavailable:
        result["details"].append("無法自動確認：" + "、".join(sorted(unavailable)) + "；請在 REW 與環境清單核對。")
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
