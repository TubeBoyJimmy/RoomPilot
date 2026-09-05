"""Bounded PEQ candidate comparison with common, policy-independent metrics.

The three internal policy scales explore tradeoffs. Their native optimization
costs are deliberately never used to rank across policies. No module monkeypatch
or mutable process-wide state is used.
"""
from copy import deepcopy
import json

from .analysis import _cancelled, _number, _settings, generate_peq


def _generate_legacy_candidates(measurements, settings, progress=None, cancel=None, *,
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


def generate_peq_candidates(measurements, settings, progress=None, cancel=None, *,
                            base_peq=None, low_cut_limit_db=1.0) -> dict:
    """V4 returns one three-variant calculation group; persistence is atomic in Bridge.

    Explicit legacy mode remains for old callers and reproducible old-policy
    diagnostics. The v4 UI always supplies peak or shape.
    """
    if (settings or {}).get("objective_mode", "legacy") == "legacy":
        return _generate_legacy_candidates(measurements, settings, progress, cancel,
                                           base_peq=base_peq, low_cut_limit_db=low_cut_limit_db)
    from .analysis import _target_reference, _validate_base_peq, explain_peq, evaluate_peq
    if not _number(low_cut_limit_db) or not 0 <= low_cut_limit_db <= 6:
        raise ValueError("低處額外減益 RMS 上限必須為 0–6 dB 的有限數值。")
    limit = float(low_cut_limit_db)
    measurements = list(measurements)
    s = _settings(settings)
    _cancelled(cancel)
    floor = 0.
    if s["strategy"] == "extend_existing":
        if not base_peq or not _number(base_peq.get("target_level")):
            raise ValueError("請先選取要保留的 PEQ 方案。")
        expected = {m["channel"] for m in measurements if m.get("channel") in ("L", "R")} if s["independent"] else {"Shared"}
        _validate_base_peq(base_peq, s, expected)
        target = float(base_peq["target_level"])
        reference = dict(mode="inherited", level_db=target, source_peq_id=base_peq.get("id", ""),
                         original=deepcopy(base_peq.get("target_reference", {})), coverage_complete=None,
                         measurement_ids=[], statistic="沿用已選方案的目標水平")
        frozen_explanation = explain_peq(measurements, s, base_peq["filters"], target)
        floor = frozen_explanation["summary"]["after"]["worst_below_target_cut_rms_db"]
        if floor > limit + 1e-6:
            raise ValueError(f"保留方案在本次完整頻段已造成 {floor:.3f} dB 低處 RMS，超過 {limit:.3f} dB 上限；請提高容許量或改成重新計算。")
        floor = min(floor, limit)
    else:
        target, reference = _target_reference(measurements, s)
    labels = ("保護低處", "平衡精修", "充分精修") if s["objective_mode"] == "shape" else ("保護低處", "平衡削峰", "充分削峰")
    candidates, reference_metrics = [], None
    last_progress = 0.

    def report(value):
        nonlocal last_progress
        if progress:
            last_progress = max(last_progress, min(1., max(0., value)))
            progress(last_progress)

    for index, (fraction, title) in enumerate(zip((.35, .65, 1.), labels)):
        cap = floor + (limit - floor) * fraction
        fit_settings = {**s, "target_level":target, "low_cut_limit_db":cap}
        result = generate_peq(measurements, fit_settings,
                              lambda value, i=index: report((i + value) / 3), cancel, base_peq=base_peq)
        _cancelled(cancel)
        result["target_reference"] = deepcopy(reference)
        result["settings"]["target_level"] = s["target_level"]
        target_rationale = (f"自動目標固定參考 {s['target_ref_min']:g}–{s['target_ref_max']:g} Hz；" + reference["statistic"] + "。") if reference["mode"] == "fixed_reference" else "目標水平由使用者指定或從保留方案繼承，不另行估算。"
        result["rationale"][1] = target_rationale
        result["comparison_context"] = dict(target_fixed_across_candidates=True, target_level_db=target,
                                            target_source_mode=reference["mode"], low_cut_limit_db=cap,
                                            requested_max_low_cut_db=limit, budget_fraction=fraction,
                                            frozen_low_cut_floor_db=floor)
        # A looser cap contains the preceding feasible solution. Retaining that
        # incumbent prevents a weaker finite search from regressing the primary
        # objective. Its historical search is not relabelled as the new search.
        if candidates and result["explanation"]["summary"]["after"]["primary_rms_db"] > candidates[-1]["metrics"]["primary_rms_db"] + 1e-9:
            previous = candidates[-1]["result"]
            original_attempt = result
            result = evaluate_peq(measurements, fit_settings, previous["filters"], target)
            result["settings"]["target_level"] = s["target_level"]
            result["target_reference"] = deepcopy(reference)
            result["comparison_context"] = original_attempt["comparison_context"]
            result["allocation"] = deepcopy(previous["allocation"])
            result["allocation"]["reused_variant_key"] = candidates[-1]["key"]
            result["allocation"]["attempted_search"] = original_attempt["allocation"]
            result["allocation"]["skipped"].append("本策略搜尋未改善較嚴格策略的已知解，保留該可行參數；繼承的搜尋紀錄仍屬原策略。")
            result["metrics"].update(manual_edit=False, objective_evaluations=original_attempt["metrics"]["objective_evaluations"], elapsed_seconds=original_attempt["metrics"]["elapsed_seconds"])
            result["rationale"] = [target_rationale, "本策略提高容許量後未找到更好的主指標，沿用前一策略的可行參數；不是另一組全域最優解。"]
            result["warnings"] = list(dict.fromkeys(result["warnings"] + original_attempt["warnings"]))
            frozen_counts = previous["allocation"].get("frozen_counts", {})
            result["explanation"] = explain_peq(measurements, result["settings"], result["filters"], target, frozen_counts=frozen_counts)
            result["explanation"]["cost_provenance"] = "current_policy_reused_feasible_solution"
        explanation = result["explanation"]
        if reference_metrics is None:
            reference_metrics = dict(summary=deepcopy(explanation["summary"]["before"]),
                                     positions=[{**{k:p[k] for k in ("id", "channel", "position", "weight")}, **p["before"]} for p in explanation["positions"]])
        violations = [dict(id=p["id"], channel=p["channel"], position=p["position"], value_db=p["after"]["below_target_cut_rms_db"], limit_db=cap)
                      for p in explanation["positions"] if p["after"]["below_target_cut_rms_db"] > cap + 1e-6]
        if violations:
            raise ValueError(title + " 的取整後方案超出低處 RMS 限制，沒有保存不完整版本。")
        count = sum(b.get("enabled", True) and abs(b["gain"]) >= .05 for bands in result["filters"].values() for b in bands)
        purpose = f"低處額外減益容許量 {cap:.3f} dB RMS；在此限制內降低" + ("殘留波峰與具支持低處誤差。" if s["objective_mode"] == "shape" else "殘留波峰。")
        candidates.append(dict(key=f"candidate_{index+1}", title=title, result=result, purpose=purpose,
                               within_limit=True, pareto=True, is_recommended=False, recommendation_eligible=count > 0,
                               low_cut_limit_db=cap, budget_fraction=fraction, limit_violations=[],
                               metrics=deepcopy(explanation["summary"]["after"]), active_band_count=count,
                               profile_scales=[], notes=[purpose, "每組獨立完整替換；低處上限在求解過程中檢查，含保留 Band，不含前級。"] ))
    # Keep three slots even when their filter parameters coincide; the budgets
    # and search records remain distinct and available in the saved version.
    for c in candidates:
        c["identical_to"] = [other["key"] for other in candidates if other is not c and other["result"]["filters"] == c["result"]["filters"]]
        c["pareto"] = not any(other is not c and all(other["metrics"][field] <= c["metrics"][field] + 1e-9 for field in ("primary_rms_db", "worst_below_target_cut_rms_db")) and any(other["metrics"][field] < c["metrics"][field] - 1e-9 for field in ("primary_rms_db", "worst_below_target_cut_rms_db")) for other in candidates)
    report(1.)
    return dict(schema_version=2, candidates=candidates, target_level=target, target_reference=reference,
                low_cut_limit_db=limit, frozen_low_cut_floor_db=floor, reference_metrics=reference_metrics,
                selected_key=candidates[0]["key"], recommended_key="", has_feasible_candidate=True,
                objective_mode=s["objective_mode"], comparison_scope="同一 PEQ 版本內三個 RMS 容許量策略；有限搜尋，不保證全域最優。",
                metric_axes=["primary_rms_db", "worst_below_target_cut_rms_db"], titles_monotone=True,
                notes=["三組共用 Baseline、目標與設備能力，低處 RMS 容許量為 35%、65%、100%。比例是可檢驗的產品預設，不是聲學標準。",
                       "保留擴充時，從既有完整 EQ 的低處 RMS 起算剩餘容許量；不能先降低目標或改寫保留參數。",
                       "三組一同保存在一個 PEQ 版本，切換方案不表示已套用。"])
