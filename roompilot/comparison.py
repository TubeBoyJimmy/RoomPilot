"""Bounded PEQ candidate comparison with common, policy-independent metrics.

The three internal policy scales explore tradeoffs. Their native optimization
costs are deliberately never used to rank across policies. No module monkeypatch
or mutable process-wide state is used.
"""
from copy import deepcopy
import json

from .analysis import _cancelled, _number, _settings, generate_peq


def generate_peq_candidates(measurements, settings, progress=None, cancel=None, *,
                            base_peq=None, low_cut_limit_db=1.0) -> dict:
    """Return at most three complete alternatives; nothing is saved or applied."""
    if not _number(low_cut_limit_db) or not 0 <= low_cut_limit_db <= 6:
        raise ValueError("低處新增削減 RMS 上限必須為 0–6 dB 的有限數值。")
    limit = float(low_cut_limit_db)
    measurements = list(measurements)
    s = _settings(settings)
    _cancelled(cancel)
    profiles = [(1., "保護低處"), (.25, "較多削峰"), (0., "削峰優先")]
    candidates, by_fingerprint = [], {}
    fixed_target, target_reference, reference_metrics, target_rationale = None, None, None, None
    last_progress = 0.

    def report(value):
        nonlocal last_progress
        if progress:
            last_progress = max(last_progress, min(1., max(0., value)))
            progress(last_progress)

    for index, (scale, title) in enumerate(profiles):
        _cancelled(cancel)
        fit_settings = {**s, "overshoot_scale":scale}
        if fixed_target is not None:
            fit_settings["target_level"] = fixed_target
        result = generate_peq(measurements, fit_settings,
                              lambda v, i=index:report((i + v) / len(profiles)), cancel,
                              base_peq=base_peq)
        _cancelled(cancel)
        if fixed_target is None:
            fixed_target = result["target_level"]
            target_reference = deepcopy(result["target_reference"])
            target_rationale = result["rationale"][1]
            explanation = result["explanation"]
            reference_metrics = dict(summary=deepcopy(explanation["summary"]["before"]),
                                     positions=[{**{k:p[k] for k in ("id", "channel", "position", "weight")}, **p["before"]}
                                                for p in explanation["positions"]])
        else:
            # Keep the true target provenance visible; only its numeric value was
            # supplied to later search profiles to prohibit silent retargeting.
            result["target_reference"] = deepcopy(target_reference)
            result["settings"]["target_level"] = s["target_level"]
            result["rationale"][1] = target_rationale
            assert result["target_level"] == fixed_target
        result["comparison_context"] = dict(target_fixed_across_candidates=True,
                                            target_level_db=fixed_target, target_source_mode=target_reference["mode"],
                                            low_cut_limit_db=limit,
                                            low_cut_definition="逐已量位置，以原低於目標頻點的 conditional RMS 檢查完整 EQ（包含保留 Band，不含前級）；不是逐頻點上限")
        fingerprint = json.dumps(result["filters"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fingerprint in by_fingerprint:
            by_fingerprint[fingerprint]["profile_scales"].append(scale)
            by_fingerprint[fingerprint]["notes"].append(f"另一個內部搜尋政策（倍率 {scale:g}）得到相同可輸入參數，已合併。")
            continue
        explanation = result["explanation"]
        metrics = deepcopy(explanation["summary"]["after"])
        violations = []
        for pos in explanation["positions"]:
            value = pos["after"]["below_target_cut_rms_db"]
            if value > limit + 1e-9:
                violations.append(dict(id=pos["id"], channel=pos["channel"], position=pos["position"], value_db=value,
                                       limit_db=limit, below_target_bin_count=pos["after"]["below_target_bin_count"]))
        active_count = sum(b.get("enabled", True) and abs(b["gain"]) >= .05 for bands in result["filters"].values() for b in bands)
        within = not violations
        eligible = within and active_count > 0 and not s["allow_boost"]
        notes = ["只與本輪候選在同一目標、網格和已量位置比較；不代表全域最佳或聽感排名。",
                 "上限逐已量位置檢查原先低於目標頻點的 conditional RMS；包含完整 EQ 與保留 Band，不含前級，並非逐頻點最大減益限制。"]
        if not within:
            notes.append("至少一個已量位置超過使用者上限；保留供預覽比較，不列為建議。")
        if not active_count:
            notes.append("本輪沒有需保存的有效修正；此為無校正結果，不列為 PEQ 建議。")
        if s["allow_boost"]:
            notes.append("已要求進階增益；既有增益證據門檻仍適用。本比較只供預覽，不列為 cut-only 建議。")
        candidate = dict(key=f"candidate_{len(candidates) + 1}", title=title, result=result,
                         within_limit=within, pareto=False, is_recommended=False, recommendation_eligible=eligible,
                         low_cut_limit_db=limit, limit_violations=violations, metrics=metrics, active_band_count=active_count,
                         profile_scales=[scale], notes=notes)
        candidates.append(candidate)
        by_fingerprint[fingerprint] = candidate
    _cancelled(cancel)
    feasible = [c for c in candidates if c["within_limit"]]
    fields = ("residual_peak_rms_db", "worst_below_target_cut_rms_db")
    for c in feasible:
        c["pareto"] = not any(
            all(other["metrics"][f] <= c["metrics"][f] + 1e-9 for f in fields)
            and any(other["metrics"][f] < c["metrics"][f] - 1e-9 for f in fields)
            for other in feasible if other is not c)
    # Lower overshoot cost need not lead to a monotone result in a finite local
    # search, so prefer neutral names whenever the observed metrics disagree.
    monotone = all(
        left["metrics"]["worst_below_target_cut_rms_db"] <= right["metrics"]["worst_below_target_cut_rms_db"] + 1e-9
        and left["metrics"]["residual_peak_rms_db"] >= right["metrics"]["residual_peak_rms_db"] - 1e-9
        for left, right in zip(candidates, candidates[1:]))
    if not monotone:
        for index, candidate in enumerate(candidates):
            candidate["title"] = f"候選 {chr(65 + index)}"
            candidate["notes"].append("本輪實際取捨未隨搜尋倍率單調變化，採中性名稱，請直接比較兩項診斷。")
    def preview_order(candidate):
        return (candidate["metrics"]["worst_below_target_cut_rms_db"], candidate["metrics"]["residual_peak_rms_db"], candidate["key"])
    eligible = [c for c in feasible if c["recommendation_eligible"]]
    recommended = min(eligible, key=preview_order) if eligible else None
    selected = recommended or min(feasible or candidates, key=preview_order)
    if recommended:
        recommended["is_recommended"] = True
    notes = ["預設選取合格候選中低處新增削減最少的一版，不以跨政策總成本排名。",
             "Pareto 只表示本輪可行候選中，沒有另一版同時在剩餘凸峰與最差位置低處代價更好；不是全域最優。",
             "未套 EQ 僅列為共同參考；每個候選都是完整替換方案，不疊加前一版。",
             "上限是各位置原先低於目標頻點的 RMS，包含完整 EQ 與保留 Band、不含前級；不是每一頻率的硬上限，尚未量測的位置不在證據範圍。"]
    if not feasible:
        notes.append("本輪未找到符合低處上限的候選；目前選取僅供預覽，沒有推薦方案。")
    elif not eligible:
        notes.append("本輪沒有可列為 cut-only 建議的有效修正；目前選取僅供預覽。")
    report(1.)
    return dict(schema_version=1, candidates=candidates, target_level=fixed_target, target_reference=target_reference,
                low_cut_limit_db=limit, reference_metrics=reference_metrics, notes=notes,
                selected_key=selected["key"], recommended_key=recommended["key"] if recommended else "",
                has_feasible_candidate=bool(feasible), comparison_scope="本輪最多三個候選；不是全域最優",
                metric_axes=list(fields), titles_monotone=monotone)
