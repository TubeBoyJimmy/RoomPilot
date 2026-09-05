"""Explicit use of an existing plan as a feasible seed and tonal comparison.

No listening preference is inferred. Reference curves do not change the target,
the objective, or the protection settings.
"""
from copy import deepcopy
import math

import numpy as np


def choose_incumbent(measurements, settings, target, references):
    from .analysis import evaluate_peq, _number

    checks, feasible = [], []
    for reference in references:
        if not reference:
            continue
        identity = {"id": reference.get("id", ""),
                    "label": reference.get("name", "既有可行方案") +
                    (" · " + reference["variant_title"] if reference.get("variant_title") else "")}
        old = reference.get("settings", {})
        reason = None
        if not _number(reference.get("target_level")) or not math.isclose(reference["target_level"], target, abs_tol=1e-8, rel_tol=0):
            reason = "目標不同，沒有沿用為搜尋起點。"
        elif old.get("independent", True) != settings["independent"]:
            reason = "左右設定方式不同，沒有沿用為搜尋起點。"
        elif not math.isclose(old.get("sample_rate", 48000), settings["sample_rate"], abs_tol=1e-8, rel_tol=0):
            reason = "濾波器模擬取樣率不同，沒有沿用為搜尋起點。"
        else:
            try:
                validated = evaluate_peq(measurements, settings, reference["filters"], target)
                # An incumbent is an exact feasible plan, never a silently
                # rounded substitute for historical parameters.
                for ch, bands in reference["filters"].items():
                    for a, b in zip(bands, validated["filters"][ch]):
                        if any(not math.isclose(a[k], b[k], abs_tol=1e-8, rel_tol=0) for k in ("frequency", "gain", "q")):
                            raise ValueError("舊參數不符合目前輸入步進。")
                feasible.append((validated["explanation"]["summary"]["after"]["primary_rms_db"], reference, identity))
            except (ValueError, KeyError, TypeError) as exc:
                reason = "未通過本次限制：" + str(exc)
        checks.append({**identity, "eligible": reason is None,
                       "reason": reason or "完整既有參數通過本次限制，可比較並精修；不鎖定參數。"})
    selected = min(feasible, key=lambda item: item[0]) if feasible else None
    return (deepcopy(selected[1]) if selected else None), {"checks": checks, "selected": selected[2] if selected else None}


def compare_reference(measurements, settings, filters, reference):
    """Compare complete EQ curves on a fixed measured 80–200 Hz domain."""
    from .analysis import filter_response, _arrays

    if not reference or not isinstance(reference.get("filters"), dict):
        return None
    if reference.get("settings", {}).get("independent", True) != settings["independent"]:
        return None
    if reference.get("settings", {}).get("sample_rate", 48000) != settings["sample_rate"]:
        return None
    ms = [m for m in measurements if m.get("channel") in ("L", "R")]
    low = max(80., max(_arrays(m)[0][0] for m in ms))
    high = min(200., min(_arrays(m)[0][-1] for m in ms))
    out = dict(source_id=reference.get("id", ""),
               source_label=reference.get("name", "既有方案") + (" · " + reference["variant_title"] if reference.get("variant_title") else ""),
               coverage_complete=low <= 80. and high >= 200., requested_min=80., requested_max=200.,
               actual_min=low if high > low else None, actual_max=high if high > low else None,
               channels=[], note="只比較完整 EQ 的低頻走勢；不含前級，不改變目標或保護限制，不代表音質排序。")
    if high <= low:
        return out
    grid = np.geomspace(low, high, max(2, math.ceil(np.log2(high / low) * 192) + 1))
    width = max(1, round((len(grid) - 1) / np.log2(high / low) / 3))
    for channel, bands in filters.items():
        if channel not in reference["filters"]:
            return None
        current = filter_response(grid, bands, settings["sample_rate"])
        old = filter_response(grid, reference["filters"][channel], settings["sample_rate"])
        difference = current - old
        trend = np.convolve(difference, np.ones(width) / width, mode="valid") if len(grid) >= width else difference
        out["channels"].append(dict(channel=channel, mean_gain_difference_db=float(np.mean(difference)),
                                    max_local_mean_difference_db=float(np.max(np.abs(trend))),
                                    current_mean_cut_db=float(np.mean(np.maximum(-current, 0))),
                                    reference_mean_cut_db=float(np.mean(np.maximum(-old, 0)))))
    return out
