"""Explicit, constrained PEQ policies on the measured magnitude response.

The finite search does not prove a global optimum.  Feasibility is checked on
every accepted, device-rounded result.  More bands remain available after
refinement; there is no complexity penalty hidden in the peak objective.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks, peak_widths

from .biquad import responses as _responses


MIN_MATERIAL_RMS_DB = .005
UNSUPPORTED_BOOST_TAIL_DB = .25
MERGE_MAX_CURVE_ERROR_DB = .05
MERGE_RMS_CURVE_ERROR_DB = .01
MERGE_OBJECTIVE_RMS_TOLERANCE_DB = .002


def _sum_response(grid, bands, sample_rate):
    return _responses(grid, bands, sample_rate).sum(axis=0)


def peak_regions(grid, ys, target, settings, weights=None, raw_ys=None, response=None):
    """Magnitude-only peak/shoulder evidence, used as starts, never acoustic Q.

    Half-prominence shoulders describe a local feature even on a tilted base.
    They do not estimate modal decay or prove spatial stability. Every original
    recording subsequently gets its own protection constraints, including when
    records disagree; an averaged peak never overrides that protection.
    """
    grid, ys = np.asarray(grid), np.atleast_2d(ys)
    raw = ys if raw_ys is None else np.atleast_2d(raw_ys)
    weights = np.full(len(ys), 1 / len(ys)) if weights is None else weights
    response = np.zeros(len(grid)) if response is None else np.asarray(response)
    excess = np.maximum(ys + response - target, 0.)
    strength = np.sqrt(np.asarray(weights) @ (excess ** 2))
    peaks, properties = find_peaks(strength, prominence=.05)
    if not len(peaks):
        return []
    widths, _, left, right = peak_widths(strength, peaks, rel_height=.5)
    log_grid = np.log(grid)
    result = []
    for n, index in enumerate(peaks):
        if not settings["f_min"] <= grid[index] <= settings["f_max"]:
            continue
        lf, rf = np.exp(np.interp([left[n], right[n]], np.arange(len(grid)), log_grid))
        width = float(rf-lf)
        if width <= 0:
            continue
        raw_excess = raw[:, index] + response[index] - target
        result.append(dict(center_hz=float(grid[index]), left_shoulder_hz=float(lf),
            right_shoulder_hz=float(rf), half_prominence_width_hz=width,
            seed_q=float(np.clip(grid[index]/width, settings["min_q"], settings["max_q"])),
            excess_db=float(strength[index]), prominence_db=float(properties["prominences"][n]),
            raw_support_fraction=float(np.mean(raw_excess > 0)),
            raw_excess_min_db=float(raw_excess.min()), raw_excess_max_db=float(raw_excess.max()),
            raw_record_count=len(raw), width_truncated=bool(left[n] <= 0 or right[n] >= len(grid)-1)))
    return sorted(result, key=lambda row: (-row["prominence_db"], row["center_hz"]))[:48]


def merge_curve_equivalent(bands, settings, *, validator=None, objective=None,
                           cancel=None, dense_grid=None):
    """Try replacing adjacent same-sign near duplicates by one fitted RBJ.

    A cascade is not represented by summing gains at unchanged Q. The complete
    rounded response must remain within explicit curve and objective tolerances
    and all caller constraints. Frozen rows are excluded by the caller.
    """
    from .analysis import _cancelled, _rounded
    result, records = deepcopy(list(bands)), []
    fs = settings["sample_rate"]
    dense = np.asarray(dense_grid) if dense_grid is not None else np.geomspace(1., fs/2*.99999, 16384)
    fit_grid = np.unique(np.r_[np.geomspace(1., fs/2*.99999, 1024),
                               np.geomspace(settings["f_min"], settings["f_max"], 512)])
    attempted = set()
    # At most one bounded optimization per original pair; successful merges
    # restart matching so a longer redundant cascade may collapse safely.
    attempts_left = min(64, max(0, len(result)*(len(result)-1)//2))
    while attempts_left:
        pair = None
        for i in range(len(result)):
            a = result[i]
            if not a.get("enabled", True) or not a["gain"]:
                continue
            for j in range(i+1, len(result)):
                b = result[j]
                signature = tuple((v["frequency"],v["gain"],v["q"]) for v in (a,b))
                if (signature not in attempted and b.get("enabled", True)
                    and a["gain"]*b["gain"] > 0
                    and abs(np.log2(a["frequency"]/b["frequency"])) <= 1/12
                    and abs(np.log2(a["q"]/b["q"])) <= 1):
                    pair = i,j,signature
                    break
            if pair:
                break
        if pair is None:
            break
        _cancelled(cancel)
        i,j,signature = pair
        attempted.add(signature)
        attempts_left -= 1
        originals = [result[i], result[j]]
        fc = float(np.exp(np.mean(np.log([b["frequency"] for b in originals]))))
        q = float(np.exp(np.mean(np.log([b["q"] for b in originals]))))
        positive = originals[0]["gain"] > 0
        gain_limit = settings["max_boost"] if positive else settings["max_cut"]
        gain_bounds = (.001, gain_limit) if positive else (-gain_limit, -.001)
        qhi = min(2.,settings["max_q"]) if positive else settings["max_q"]
        record = dict(original_filters=deepcopy(originals), accepted=False,
            max_curve_error_limit_db=MERGE_MAX_CURVE_ERROR_DB,
            rms_curve_error_limit_db=MERGE_RMS_CURVE_ERROR_DB,
            objective_rms_tolerance_db=MERGE_OBJECTIVE_RMS_TOLERANCE_DB)
        pair_dense = _sum_response(np.unique(np.r_[dense, [b["frequency"] for b in originals]]), originals, fs)
        required = float(np.max(pair_dense) if positive else -np.min(pair_dense))
        if required > gain_limit + MERGE_MAX_CURVE_ERROR_DB:
            record.update(reason_code="single_band_depth_limit", required_peak_gain_db=required,
                          single_band_gain_limit_db=float(gain_limit))
            records.append(record)
            continue
        wanted = _sum_response(fit_grid, originals, fs)
        def unpack(x):
            return dict(frequency=float(np.exp(x[0])), gain=float(x[1]),
                        q=float(np.exp(x[2])), enabled=True, type="PK")
        def loss(x):
            _cancelled(cancel)
            return float(np.mean((_sum_response(fit_grid,[unpack(x)],fs)-wanted)**2))
        fit = minimize(loss, [np.log(fc), np.clip(sum(b["gain"] for b in originals),*gain_bounds), np.log(np.clip(q,settings["min_q"],qhi))],
            method="L-BFGS-B", bounds=[(np.log(settings["f_min"]),np.log(settings["f_max"])),gain_bounds,(np.log(settings["min_q"]),np.log(qhi))],
            options=dict(maxiter=60, ftol=1e-13))
        merged = _rounded([unpack(fit.x)], settings)
        if not merged:
            record["reason_code"]="rounded_to_empty"
            records.append(record)
            continue
        trial = result[:i]+merged+result[i+1:j]+result[j+1:]
        check_grid = np.unique(np.r_[dense, [b["frequency"] for b in result+trial]])
        delta = _sum_response(check_grid,trial,fs)-_sum_response(check_grid,result,fs)
        rms = float(np.sqrt(np.mean(delta**2)))
        maximum = float(np.max(np.abs(delta), initial=0))
        before = float(objective(result)) if objective else None
        after = float(objective(trial)) if objective else None
        record.update(replacement_filter=deepcopy(merged[0]), max_curve_error_db=maximum,
                      rms_curve_error_db=rms, objective_before=before, objective_after=after,
                      dense_validation_points=len(check_grid))
        if maximum > MERGE_MAX_CURVE_ERROR_DB or rms > MERGE_RMS_CURVE_ERROR_DB:
            record["reason_code"]="curve_not_equivalent"
        elif validator and not validator(trial):
            record["reason_code"]="protection_or_device_constraint"
        elif before is not None and np.sqrt(max(after,0))-np.sqrt(max(before,0)) > MERGE_OBJECTIVE_RMS_TOLERANCE_DB:
            record["reason_code"]="objective_tolerance"
        else:
            result = trial
            record.update(accepted=True, reason_code="curve_equivalent_merge")
        records.append(record)
    return result, records


class _Model:
    def __init__(self, grid, ys, target, settings, boost_allowed, weights, evidence_ys=None):
        from .analysis import _desired_model

        self.grid = np.asarray(grid, dtype=float)
        self.ys = np.asarray(ys, dtype=float)
        self.target, self.s = float(target), settings
        self.weights = np.asarray(weights, dtype=float)
        self.mode = settings.get("objective_mode", "peak")
        if self.mode not in ("peak", "balanced", "shape"):
            raise ValueError("未知的 PEQ 最佳化目標。")
        self.limit = float(settings.get("low_cut_limit_db", 1.))
        self.alpha = float(settings.get("balanced_low_cut_weight", 1.))
        if not np.isfinite(self.limit) or not 0 <= self.limit <= 6:
            raise ValueError("低處額外減益 RMS 容許量必須為 0–6 dB。")
        if not np.isfinite(self.alpha) or self.alpha < 0:
            raise ValueError("低處減益權重必須為有限非負數。")
        below = self.ys < self.target
        self.below_counts = below.sum(axis=1)
        self.below_weights = below / np.maximum(self.below_counts[:, None], 1)
        evidence_ys = ys if evidence_ys is None else evidence_ys
        _, _, support, excess = _desired_model(grid, evidence_ys, target, settings, boost_allowed)
        self.excess = excess
        self.boost_allowed = bool(boost_allowed and settings.get("allow_boost") and settings.get("supports_preamp", True) and settings["min_q"] <= 2)
        self.support = support if self.boost_allowed else np.zeros(len(grid), dtype=bool)
        # Broad positive filters have tails outside the fitting mask. Permit
        # only their shallow deficit headroom common to *every* position,
        # plus 0.25 dB tolerance; a deep or inconsistent dip offers no credit.
        shallow = np.all((excess < 0.) & (excess > -4.), axis=0)
        self.boost_tail_cap = UNSUPPORTED_BOOST_TAIL_DB + np.where(shallow,np.maximum(np.min(-excess,axis=0),0.),0.)

    def metrics(self, response):
        """Vectorized costs for a batch of complete EQ responses."""
        response = np.atleast_2d(response)
        peak = np.zeros(len(response))
        deficit = np.zeros(len(response))
        for weight, y in zip(self.weights, self.ys):
            error = y[None, :] + response - self.target
            peak += weight * np.mean(np.maximum(error, 0.) ** 2, axis=1)
            if self.mode == "shape" and self.support.any():
                # Uncorrectable/deep deficits never become a fitting demand.
                deficit += weight * np.mean(np.minimum(error, 0.) ** 2 * self.support[None, :], axis=1)
        lower = np.maximum(-response, 0.) ** 2 @ self.below_weights.T
        low = lower @ self.weights
        value = peak.copy()
        if self.mode == "balanced":
            value += self.alpha * low
        elif self.mode == "shape":
            value += deficit
        return value, peak, low, lower, deficit

    def components(self, response):
        value, peak, low, lower, deficit = self.metrics(response)
        return dict(value=float(value[0]), correction_error=float(peak[0]),
                    overshoot=float(self.alpha * low[0]) if self.mode == "balanced" else 0.,
                    shape_deficit=float(deficit[0]), shape_deficit_mse=float(deficit[0]), effort=0., unsupported_boost=0., complexity=0.,
                    peak_mse=float(peak[0]), low_cut_mse=float(low[0]),
                    worst_below_target_cut_rms_db=float(np.sqrt(lower[0].max(initial=0.))))

    def boost_supported(self, band):
        if band["gain"] <= 0:
            return True
        if not self.boost_allowed or band["q"] > 2 + 1e-9 or band["frequency"] < self.grid[0] * np.sqrt(2):
            return False
        core = np.abs(np.log2(self.grid / band["frequency"])) <= min(.2, 1 / band["q"] / 3)
        return bool(core.any() and np.all(np.mean((self.excess[:, core] < -.5) & (self.excess[:, core] > -4), axis=1) >= .8))


def objective_components(grid, ys, target, settings, bands, weights=None, boost_allowed=False, evidence_ys=None):
    """Current policy cost; no fitting, rounding, preamp or hidden band cost."""
    ys = np.asarray(ys, dtype=float)
    weights = np.full(len(ys), 1 / len(ys)) if weights is None else weights
    model = _Model(grid, ys, target, settings, boost_allowed, weights, evidence_ys)
    return model.components(_sum_response(grid, bands, settings["sample_rate"]))


def fit_strategy(grid, ys, target, s, boost_allowed, cancel, progress, *, names=None,
                 frozen=(), center_min=None, center_max=None, slots=None, protect_grid=None,
                 raw_ys=None, raw_names=None, incumbent=None):
    """Return additions, evaluation count and an auditable bounded-search log.

    Each batch covers all residual local extrema and regular log-frequency seeds.
    Feasible candidates receive constrained local refinement; additions reopen
    after coordinate/joint refinement. A simultaneous multiband start provides
    an alternative to the sequential basin. Frozen filters remain byte-for-byte.
    """
    from .analysis import _cancelled, _position_weights, _rounded, _boundary_flags

    _cancelled(cancel)
    grid, ys = np.asarray(grid, dtype=float), np.asarray(ys, dtype=float)
    if ys.ndim != 2 or ys.shape[1] != len(grid) or not len(ys) or not np.all(np.isfinite(ys)):
        raise ValueError("PEQ 搜尋需要有限、對齊網格的量測。")
    frozen = deepcopy(list(frozen))
    slots = s["bands"] - len(frozen) if slots is None else int(slots)
    slots = max(0, min(slots, s["bands"] - len(frozen)))
    weights = _position_weights(names) if names else np.full(len(ys), 1 / len(ys))
    v5 = s.get("guard_policy") == "v5"
    raw_ys = ys if raw_ys is None else np.asarray(raw_ys, dtype=float)
    if raw_ys.ndim != 2 or raw_ys.shape[1] != len(grid) or not len(raw_ys) or not np.all(np.isfinite(raw_ys)):
        raise ValueError("逐筆量測保護需要有限、對齊網格的原始量測。")
    model = _Model(grid, ys, target, s, boost_allowed, weights, raw_ys if v5 else None)
    protection, footprint = None, None
    if v5:
        from .protection import ProtectionModel, CurveFootprint
        protection_names = raw_names if raw_names is not None else names if names is not None and len(names) == len(raw_ys) else None
        protection = ProtectionModel(grid, raw_ys, target, s, names=protection_names)
        footprint = CurveFootprint(s)
    lo = s["f_min"] if center_min is None else center_min
    hi = s["f_max"] if center_max is None else center_max
    lo = math.ceil(lo / s["freq_step"] - 1e-9) * s["freq_step"]
    hi = math.floor(hi / s["freq_step"] + 1e-9) * s["freq_step"]
    rounding = {**s, "f_min":lo, "f_max":hi}
    fs = s["sample_rate"]
    frozen_response = _sum_response(grid, frozen, fs)
    # Use the measured grid plus a global grid while solving; recheck on the
    # same denser global domain used by manual validation before every accept.
    global_grid = np.unique(np.r_[np.geomspace(1., fs / 2 * .99999, 768), grid])
    dense_grid = np.unique(np.r_[np.geomspace(1., fs / 2 * .99999, 16384), grid])
    if footprint is not None:
        dense_grid = np.unique(np.r_[dense_grid, footprint.grid])
    frozen_global = _sum_response(global_grid, frozen, fs)
    frozen_dense = _sum_response(dense_grid, frozen, fs)
    frozen_footprint = _sum_response(footprint.grid,frozen,fs) if footprint is not None else None
    dense_outside = (dense_grid < s["f_min"]) | (dense_grid > s["f_max"])
    protected = None if protect_grid is None else np.asarray(protect_grid, dtype=float)
    cut_bound = s["max_total_cut"]
    boost_bound = s.get("max_total_boost", s["max_boost"]) if s["allow_boost"] else 0.
    evaluations, rounds = 0, []
    merge_records = []
    incumbent_record = dict(provided=incumbent is not None, accepted=False, reason_code="not_provided")
    original_incumbent = None
    evidence = peak_regions(grid,ys,target,s,weights,raw_ys) if v5 else []
    search_branch, selected_branch = "sequential", "sequential"

    def cancelled():
        _cancelled(cancel)

    def response(bs):
        return frozen_response + _sum_response(grid, bs, fs)

    def footprint_response(bs):
        return frozen_footprint + _sum_response(footprint.grid,bs,fs)

    def dense_outside_check(bs, full_response=None):
        frequencies = dense_grid[dense_outside]
        values = (frozen_dense[dense_outside] + _sum_response(frequencies,bs,fs)
                  if full_response is None else full_response[dense_outside])
        centers = [b["frequency"] for b in frozen+bs if b.get("enabled",True)
                   and (b["frequency"] < s["f_min"] or b["frequency"] > s["f_max"])]
        if centers:
            frequencies = np.r_[frequencies,centers]
            values = np.r_[values,_sum_response(np.asarray(centers),frozen+bs,fs)]
        index = int(np.argmin(values)) if len(values) else None
        maximum = float(max(0.,-values[index])) if index is not None else 0.
        return float(s.get("outside_cut_limit_db",3.))-maximum, maximum, float(frequencies[index]) if index is not None else None

    def cost(bs):
        nonlocal evaluations
        evaluations += 1
        cancelled()
        return model.components(response(bs))

    def energy(bs):
        return float(np.mean(response(bs) ** 2))

    def objective_better(candidate, incumbent):
        cv, iv = cost(candidate)["value"], cost(incumbent)["value"]
        return cv < iv-1e-9 or (abs(cv-iv) <= 1e-9 and energy(candidate) < energy(incumbent)-1e-8)

    def constraints(bs, dense=False):
        nonlocal evaluations
        evaluations += 1
        cancelled()
        r = response(bs)
        lower = model.metrics(r)[3][0]
        full = (frozen_dense + _sum_response(dense_grid, bs, fs)) if dense else (frozen_global + _sum_response(global_grid, bs, fs))
        full_response = full
        if dense:
            centers = [b["frequency"] for b in frozen+bs if b.get("enabled",True)]
            if centers:
                full = np.r_[full,_sum_response(np.asarray(centers),frozen+bs,fs)]
        constraints_ = [*(model.limit ** 2 - lower if protection is None else []),
                        float(full.min(initial=0) + cut_bound), float(boost_bound - full.max(initial=0))]
        if protected is not None:
            drift = _sum_response(protected, bs, fs)
            constraints_.append(float(s["bass_drift_limit_db"] - np.max(np.abs(drift), initial=0)))
        if model.boost_allowed and (~model.support).any():
            constraints_.append(float(np.min(model.boost_tail_cap[~model.support] - r[~model.support])))
        if protection is not None:
            constraints_.extend(np.asarray(protection.slacks(r)).ravel())
        if footprint is not None:
            constraints_.extend(np.asarray(footprint.slacks(footprint_response(bs))).ravel())
            if dense:
                constraints_.append(dense_outside_check(bs,full_response)[0])
        return np.asarray(constraints_)

    def feasible(bs, dense=False):
        if not all(model.boost_supported(b) for b in bs):
            return False
        return bool(np.min(constraints(bs, dense), initial=0) >= -1e-8)

    def safe_rounded(bs):
        """Quantization is followed by feasibility, never silently saved unsafe."""
        bs = [b for b in _rounded(bs, rounding) if model.boost_supported(b)]
        if feasible(bs, dense=True):
            return bs
        # Quantization can cross a boundary. Search smaller unlocked gains,
        # keeping the incumbent immutable; no change to the measured target.
        original = deepcopy(bs)
        for scale in (.99, .97, .94, .9, .8, .65, .5, .3, .1, 0.):
            cancelled()
            trial = _rounded([{**b, "gain":b["gain"] * scale} for b in original], rounding)
            if feasible(trial, dense=True):
                return trial
        return []

    baseline_constraints = constraints([], dense=True)
    if baseline_constraints.min(initial=0) < -1e-7:
        if protection is not None and np.min(protection.slacks(frozen_response),initial=0) < -1e-7:
            detail = protection.describe_violations(frozen_response,tolerance=1e-7)
            raise ValueError(f"保留方案超過目前逐筆量測保護限制：{detail}。請調整明示限制或選擇整體重算；保留的 Band 未修改。")
        if footprint is not None:
            detail = footprint.describe_violations(frozen_footprint,tolerance=1e-7)
            slack,maximum,frequency = dense_outside_check([])
            if not detail and slack < -1e-7:
                detail = f"频段外 {frequency:.3f} Hz 的 EQ 減益 {maximum:.3f} dB，上限 {s.get('outside_cut_limit_db',3.):.3f} dB"
            if detail:
                raise ValueError(f"保留方案超過完整 EQ 曲線保護限制：{detail}。這是濾波器的頻段外影響，不代表已量到該處 SPL；請調整明示限制或整體重算，保留的 Band 未修改。")
        lower = (protection.diagnostics(frozen_response) if protection is not None else model.components(frozen_response))["worst_below_target_cut_rms_db"]
        if lower > model.limit + 1e-7:
            raise ValueError(f"保留方案在目前頻段的低處额外減益為 {lower:.3f} dB RMS，已超過 {model.limit:g} dB 容許量；請提高容許量或選擇整體重算，不能只新增 Band 來掩蓋限制。")
        raise ValueError("保留方案已超過目前的合成 Gain 或增益支持限制；請檢查設定或整體重算。")

    def finish(bs, codes, skipped):
        cancelled()
        if not feasible(bs, dense=True):
            raise ValueError("取整後方案未通過最終限制驗證，沒有產出 PEQ。")
        r = response(bs)
        remaining = np.maximum(ys + r - target, 0.)
        peak = float(np.max(remaining, initial=0))
        if progress:
            progress(1.)
        return deepcopy(bs), evaluations, dict(skipped=skipped, reason_codes=list(dict.fromkeys(codes)), merges=sum(row["accepted"] for row in merge_records),
            rounds=rounds, remaining_correction_demand_db=peak, final_filters=deepcopy(frozen + bs),
            final_cost=model.components(r), objective_mode=model.mode, low_cut_limit_db=model.limit,
            minimum_material_rms_db=MIN_MATERIAL_RMS_DB, dense_validation_points=len(dense_grid),
            selected_search_branch=selected_branch,
            guard_policy="v5" if v5 else "legacy", protection=protection.diagnostics(r) if protection is not None else None,
            curve_footprint=footprint.diagnostics(footprint_response(bs)) if footprint is not None else None,
            peak_regions=evidence, merge_attempts=merge_records, incumbent=incumbent_record,
            evidence_note="半突出高度兩側寬度只提供頻率、Q、深度的共同搜尋起點；Q 不是聲學衰減估計。逐筆原始量測各自受相同保護，量測差異不會自動導致較寬濾波器。" if v5 else None,
            search_limits=dict(regular_centers_per_octave=24, refined_addition_seeds=6 if s["mode"]=="deep" else 3,
                               joint_refinement_max_bands=12, simultaneous_seed_max_bands=24),
            search_policy="all residual extrema + regular log seeds; batch screening; constrained local, coordinate and joint refinement; reopened additions; simultaneous multiband alternate start",
            cost_note="成本依实际取整方案與選定目標計算；低處 RMS 在搜尋內逐位置限制。有限搜尋不是全域最優或聽感改善的證明。")

    if not slots:
        return finish([], ["no_budget"], ["可用 Band 已用完；保留原設定，沒有重排。"])
    if hi < lo or hi <= 0:
        return finish([], ["no_valid_center"], ["指定範圍沒有符合 Hz 步進的新增中心頻率。"])

    def trace(before, candidate, accepted, phase, reason):
        first = cost(before)
        second = cost(candidate) if candidate is not None else None
        flags = sorted({f for b in (candidate or []) for f in _boundary_flags(b, s)})
        if candidate is not None:
            cc = constraints(candidate)
            old_count = len(ys) if protection is None else 0
            if protection is None and cc[:old_count].min(initial=np.inf) < .02:
                flags.append("low_cut_rms_near_limit")
            if cc[old_count] < .05:
                flags.append("total_cut_near_limit")
            if protected is not None and cc[old_count+2] < .02:
                flags.append("preserved_range_drift_near_limit")
            if protection is not None:
                flags.extend(name+"_near_limit" for name,slack in zip(protection.constraint_names,protection.slacks(response(candidate))) if slack < .02)
            if footprint is not None:
                flags.extend(name+"_near_limit" for name,slack in zip(footprint.constraint_names,footprint.slacks(footprint_response(candidate))) if slack < .02)
        rounds.append(dict(round=len(rounds)+1, phase=phase, branch=search_branch, accepted=accepted, reason_code=reason,
            reason={"accepted":"取整後符合全部限制，且選定目標有可量化改善。",
                    "material_gain":"新增後的目標 RMS 改善達 0.005 dB，符合逐位置低處與設備限制。",
                    "insufficient_search_gain":"精修並重新搜尋後，這輪未找到目標 RMS 改善達 0.005 dB 的可行新增方案。",
                    "alternate_start":"比較同時配置多段的另一個起點，採用取整後較佳的可行結果。",
                    "redundant_removed":"移除此段未惡化主要目標，且限制仍符合。"}.get(reason, reason),
            before_filters=deepcopy(frozen+before), candidate_filters=deepcopy(frozen+candidate) if candidate is not None else None,
            before_cost=first, candidate_cost=second, improvement=first["value"]-second["value"] if second else None,
            active_constraints=flags, constraints_considered=["parameter_bounds", "entry_step_rounding", "total_gain", "band_budget"] + (["per_position_low_cut_rms"] if protection is None else list(protection.constraint_names)) + (["preserved_range_drift"] if protected is not None else []) + (["boost_evidence", "unsupported_boost_tail"] if model.boost_allowed else []) + (list(footprint.constraint_names) if footprint is not None else []),
            protection_after=protection.diagnostics(response(candidate)) if protection is not None and candidate is not None else None,
            curve_footprint_after=footprint.diagnostics(footprint_response(candidate)) if footprint is not None and candidate is not None else None))

    def seeds(bs, simultaneous=False):
        r = response(bs)
        remaining = np.maximum(ys + r - target, 0.)
        strength = np.sqrt(weights @ (remaining ** 2))
        in_range = (grid >= lo) & (grid <= hi)
        indices = set()
        for row in np.r_[remaining, strength[None, :]]:
            peaks, _ = find_peaks(np.where(in_range, row, 0.))
            indices.update(int(i) for i in peaks if row[i] > .02 and in_range[i])
            possible = np.flatnonzero(in_range)
            if len(possible):
                indices.add(int(possible[np.argmax(row[possible])]))
        regular = np.geomspace(lo, hi, max(2, int(np.ceil(np.log2(hi/lo)*24))+1)) if hi > lo else np.array([lo])
        centers = np.unique(np.r_[[grid[i] for i in sorted(indices)], regular, [lo, hi]])
        qvalues = np.unique(np.clip([s["min_q"], .7, 1.2, 2., 3.5, 6., 9., s["max_q"]], s["min_q"], s["max_q"]))
        result = []
        if v5:
            # Explicit shoulder-derived joint starts supplement, rather than
            # replace, narrow and wide alternatives. Disagreement does not
            # widen Q: use shallower common-support starts and per-record caps.
            for region in peak_regions(grid,ys,target,s,weights,raw_ys,r):
                fc, q, need = region["center_hz"],region["seed_q"],region["excess_db"]
                if not lo <= fc <= hi:
                    continue
                common = max(0.,region["raw_excess_min_db"])
                for freq in (fc, np.sqrt(region["left_shoulder_hz"]*region["right_shoulder_hz"])):
                    if not lo <= freq <= hi:
                        continue
                    for width_scale, depth_scale in ((.65,.5),(1.,1.),(1.5,1.)):
                        for gain in (need*depth_scale, min(need,common)*depth_scale):
                            if gain < s["gain_step"]:
                                continue
                            result.append(dict(frequency=float(freq), gain=-float(min(s["max_cut"],gain)),
                                q=float(np.clip(q*width_scale,s["min_q"],s["max_q"])),enabled=True,type="PK"))
        for fc in centers:
            need = float(np.interp(np.log(fc), np.log(grid), strength))
            if need > .015:
                gains = np.unique(np.clip([s["gain_step"], .25*need, .5*need, need, min(s["max_cut"], need*1.4)], s["gain_step"], s["max_cut"]))
                for q in qvalues:
                    for gain in gains:
                        result.append(dict(frequency=float(fc), gain=-float(gain), q=float(q), enabled=True, type="PK"))
        if model.boost_allowed and model.mode == "shape":
            deficit = np.sqrt(weights @ (np.minimum(ys+r-target, 0.) ** 2)) * model.support
            pp, _ = find_peaks(deficit)
            boost_centers = np.unique(np.r_[grid[pp], regular])
            for fc in boost_centers:
                if not lo <= fc <= hi:
                    continue
                need = float(np.interp(np.log(fc), np.log(grid), deficit))
                if need <= .02:
                    continue
                for q in qvalues[qvalues <= 2]:
                    for gain in np.unique(np.clip([s["gain_step"], .25, .5*need, need, 1.4*need], s["gain_step"], s["max_boost"])):
                        b = dict(frequency=float(fc), gain=float(gain), q=float(q), enabled=True, type="PK")
                        if model.boost_supported(b):
                            result.append(b)
        # Apply device entry steps before the broad discrete feasibility scan.
        unique = {}
        for b in result:
            rounded = _rounded([b], rounding)
            if rounded:
                b = rounded[0]
                unique[(b["frequency"], b["gain"], b["q"])] = b
        return list(unique.values())

    def screen(bs, trials, count=8):
        nonlocal evaluations
        if not trials:
            return []
        r = response(bs)
        ranked = []
        for start in range(0, len(trials), 192):
            cancelled()
            part = trials[start:start+192]
            rr = r[None, :] + _responses(grid, part, fs)
            values, _, _, lower, _ = model.metrics(rr)
            ok = np.max(lower, axis=1) <= model.limit**2 + 1e-10 if protection is None else np.ones(len(part),dtype=bool)
            ok &= rr.min(axis=1) >= -cut_bound - 1e-8
            ok &= rr.max(axis=1) <= boost_bound + 1e-8
            if protection is not None:
                ok &= np.min(protection.slacks(rr),axis=-1) >= -1e-8
            if footprint is not None:
                footprint_curves = footprint_response(bs)[None,:]+_responses(footprint.grid,part,fs)
                ok &= np.min(footprint.slacks(footprint_curves),axis=-1) >= -1e-8
            if protected is not None:
                drift = _sum_response(protected, bs, fs)[None,:] + _responses(protected, part, fs)
                ok &= np.max(np.abs(drift), axis=1) <= s["bass_drift_limit_db"] + 1e-8
            if model.boost_allowed and (~model.support).any():
                ok &= np.all(rr[:, ~model.support] <= model.boost_tail_cap[None, ~model.support] + 1e-8,axis=1)
            evaluations += len(part)
            indices = np.flatnonzero(ok)
            ranked.extend((float(values[i]), part[i]) for i in indices)
        ranked.sort(key=lambda pair:(round(pair[0], 9), float(np.mean((r+_sum_response(grid,[pair[1]],fs))**2))))
        # Reserve distinct frequency/Q basins, not eight nearly-identical gains.
        picked = []
        for value, b in ranked:
            if not feasible(bs+[b]):
                continue
            if any(abs(np.log2(b["frequency"]/old[1]["frequency"])) < 1/36 and abs(np.log(b["q"]/old[1]["q"])) < .3 and b["gain"]*old[1]["gain"] > 0 for old in picked):
                continue
            picked.append((value,b))
            if len(picked) >= count:
                break
        return picked

    def optimize(seed, fixed=(), iterations=70, polish=False):
        """Continuous constrained proposal; every returned result is rounded."""
        if not seed:
            return list(fixed)
        bounds = []
        for b in seed:
            gl, gh = (max(s["gain_step"]*.25, .001), s["max_boost"]) if b["gain"] > 0 else (-s["max_cut"], -.001)
            qhi = min(2., s["max_q"]) if b["gain"] > 0 else s["max_q"]
            bounds.extend([(np.log(lo), np.log(hi)), (gl, gh), (np.log(s["min_q"]), np.log(qhi))])
        packed = np.array([[np.log(b["frequency"]), b["gain"], np.log(b["q"])] for b in seed]).ravel()
        def unpack(x):
            return [dict(frequency=float(np.exp(row[0])), gain=float(row[1]), q=float(np.exp(row[2])), enabled=True, type="PK") for row in x.reshape(-1,3)]
        def fun(x):
            return cost(list(fixed)+unpack(x))["value"]
        fit = minimize(fun, packed, method="SLSQP", bounds=bounds,
                       constraints=[dict(type="ineq", fun=lambda x:constraints(list(fixed)+unpack(x)))],
                       options=dict(maxiter=iterations, ftol=2e-9, disp=False))
        trial = safe_rounded(list(fixed)+unpack(fit.x))
        original = safe_rounded(list(fixed)+list(seed))
        answer = trial if objective_better(trial,original) else original
        if polish and cost(answer)["value"] < 1e-8 and not fixed:
            # Lexicographic zero-error tie-break, not a regularizer that can
            # defeat real peak reduction. Stops gratuitous broad attenuation
            # when all measured bins started above the target (no lower mask).
            polish_x = np.array([[np.log(b["frequency"]),b["gain"],np.log(b["q"])] for b in answer]).ravel()
            if len(polish_x) == len(packed):
                polished = minimize(lambda x:energy(unpack(x)),polish_x,method="SLSQP",bounds=bounds,
                    constraints=[dict(type="ineq",fun=lambda x:np.r_[constraints(unpack(x)),1e-10-fun(x)])],
                    options=dict(maxiter=100,ftol=1e-10,disp=False))
                candidate = safe_rounded(unpack(polished.x))
                if objective_better(candidate,answer):
                    answer = candidate
        return answer

    def refine(bs, thorough=False):
        if not bs:
            return bs
        best = deepcopy(bs)
        score = cost(best)["value"]
        # Joint refinement is kept modest; many-band software profiles use
        # bounded three-variable coordinate passes rather than a huge Jacobian.
        if len(bs) <= 12:
            trial = optimize(best, iterations=100 if thorough else 45, polish=thorough)
            value = cost(trial)["value"]
            if objective_better(trial,best):
                best, score = trial, value
        if thorough or len(bs) > 12:
            for i in range(len(best)):
                cancelled()
                if i >= len(best):
                    break
                fixed = best[:i]+best[i+1:]
                trial = optimize([best[i]], fixed=fixed, iterations=55 if thorough else 30)
                value = cost(trial)["value"]
                if objective_better(trial,best):
                    best, score = trial, value
        return best

    def better(candidate, incumbent, material=False):
        first, second = cost(incumbent), cost(candidate)
        if material:
            return np.sqrt(max(first["value"],0)) - np.sqrt(max(second["value"],0)) >= MIN_MATERIAL_RMS_DB - 1e-10
        return objective_better(candidate,incumbent)

    def add_until_done(bs, phase="add_band", progress_base=0., progress_span=.6):
        # Always refine after acceptance. A failed addition gets another full
        # refinement and reopened search before the finite stop is recorded.
        failures = 0
        while len(bs) < slots:
            cancelled()
            options = screen(bs, seeds(bs), count=6 if s["mode"] == "deep" else 3)
            best = None
            for _, seed in options:
                trial = optimize([seed], fixed=bs, iterations=65 if s["mode"] == "deep" else 35)
                if len(trial) > len(bs) and (best is None or better(trial,best)):
                    best = trial
            if best is None or not better(best,bs,material=True):
                refined = refine(bs, thorough=True)
                if failures == 0 and better(refined,bs):
                    trace(bs,refined,True,"refine_before_reopen","accepted")
                    bs, failures = refined, 1
                    continue
                trace(bs,best,False,phase,"insufficient_search_gain")
                break
            trace(bs,best,True,phase,"material_gain")
            bs, failures = best, 0
            refined = refine(bs)
            if better(refined,bs):
                trace(bs,refined,True,"interleaved_refinement","accepted")
                bs = refined
            if progress:
                progress(progress_base + progress_span * min(len(bs)/max(slots,1),1))
        return bs

    if incumbent is not None:
        # Do not round or weaken the old solution and then call it preserved.
        # Only an already device-enterable, currently feasible candidate gets
        # an immutable fallback; a rejected candidate never blocks normal fit.
        reason = None
        if not isinstance(incumbent, (list,tuple)) or len(incumbent) > slots:
            reason = "incumbent_band_budget"
        else:
            for b in incumbent:
                if not isinstance(b,dict) or b.get("type","PK") != "PK":
                    reason = "incumbent_filter_type"
                    break
                if any(not isinstance(b.get(k),(int,float,np.number)) or not np.isfinite(b[k]) for k in ("frequency","gain","q")):
                    reason = "incumbent_nonfinite_parameter"
                    break
                if not (lo-1e-9 <= b["frequency"] <= hi+1e-9 and s["min_q"]-1e-9 <= b["q"] <= s["max_q"]+1e-9
                        and -s["max_cut"]-1e-9 <= b["gain"] <= (s["max_boost"] if s["allow_boost"] else 0)+1e-9):
                    reason = "incumbent_parameter_bounds"
                    break
                if any(abs(b[k]/step-round(b[k]/step)) > 1e-6 for k,step in (("frequency",s["freq_step"]),("gain",s["gain_step"]),("q",s["q_step"]))):
                    reason = "incumbent_entry_steps"
                    break
        if reason is None and not feasible(list(incumbent),dense=True):
            reason = "incumbent_current_protection"
            incumbent_record["protection"] = protection.diagnostics(response(incumbent)) if protection is not None else None
            incumbent_record["curve_footprint"] = footprint.diagnostics(footprint_response(incumbent)) if footprint is not None else None
            incumbent_record["minimum_constraint_slack"] = float(np.min(constraints(incumbent,dense=True)))
        if reason is None:
            original_incumbent = deepcopy(list(incumbent))
            incumbent_record.update(accepted=True, reason_code="currently_feasible", filters=deepcopy(original_incumbent),
                                    value=cost(original_incumbent)["value"])
        else:
            incumbent_record["reason_code"] = reason
    start = original_incumbent if original_incumbent is not None else []
    bands = refine(start,thorough=True) if start else []
    if original_incumbent is not None:
        search_branch = selected_branch = "incumbent"
        trace(start,bands,True,"incumbent_refinement","accepted")
    bands = add_until_done(bands)
    if progress:
        progress(.65)
    # Independent simultaneous seeds avoid requiring each band to clear the
    # material threshold before joint allocation has a chance to improve it.
    if slots >= 2 and s["mode"] == "deep":
        search_branch = "simultaneous"
        options = screen([], seeds([]), count=min(slots*3,72))
        seed = []
        for _, b in options:
            if any(abs(np.log2(b["frequency"]/old["frequency"])) < max(1/12, .5/b["q"]) and b["gain"]*old["gain"] > 0 for old in seed):
                continue
            seed.append({**b,"gain":b["gain"]*.5})
            if len(seed) >= min(slots,24):
                break
        alternate = safe_rounded(seed)
        if alternate:
            alternate = refine(alternate, thorough=True)
            alternate = add_until_done(alternate, "alternate_add_band", .65, .3)
            if better(alternate,bands):
                trace(bands,alternate,True,"alternate_multiband_start","alternate_start")
                bands = alternate
                selected_branch = "simultaneous"
    search_branch = selected_branch
    # Remove only numerically redundant bands (no arbitrary per-band cost).
    for i in range(len(bands)-1,-1,-1):
        reduced = bands[:i]+bands[i+1:]
        if feasible(reduced,dense=True) and cost(reduced)["value"] <= cost(bands)["value"]+1e-10:
            trace(bands,reduced,True,"remove_redundant","redundant_removed")
            bands = reduced
    # Pruning/refinement can reopen slots; keep the promise at the final step.
    refined = refine(bands, thorough=True)
    if better(refined,bands):
        trace(bands,refined,True,"final_refinement","accepted")
        bands = refined
    if len(bands) < slots:
        bands = add_until_done(bands,"final_reopened_add_band", .95, .04)
    if v5:
        before_merge = deepcopy(bands)
        bands, merge_records = merge_curve_equivalent(bands,rounding,
            validator=lambda candidate:feasible(candidate,dense=True),
            objective=lambda candidate:cost(candidate)["value"],cancel=cancel,dense_grid=dense_grid)
        if len(bands) < len(before_merge):
            # Freed hardware capacity may now treat another feature. A final
            # equivalence pass keeps the output compact if search restacks it.
            bands = add_until_done(bands,"post_merge_reopened_add_band", .99, 0.)
            bands, extra_merges = merge_curve_equivalent(bands,rounding,
                validator=lambda candidate:feasible(candidate,dense=True),
                objective=lambda candidate:cost(candidate)["value"],cancel=cancel,dense_grid=dense_grid)
            merge_records.extend(extra_merges)
    if original_incumbent is not None and objective_better(original_incumbent,bands):
        trace(bands,original_incumbent,True,"restore_feasible_incumbent","accepted")
        bands = deepcopy(original_incumbent)
        selected_branch = "incumbent"
        incumbent_record["restored_at_finish"] = True
    if original_incumbent is not None:
        incumbent_record["final_value"] = cost(bands)["value"]
        incumbent_record["not_worse_than_incumbent"] = bool(cost(bands)["value"] <= incumbent_record["value"]+1e-9)
    codes, skipped = [], []
    if len(bands) >= slots:
        codes.append("budget_exhausted_with_residual")
        skipped.append("已使用全部可配置 Band；仍可能有殘留波峰，未超出使用者設定的數量。")
    else:
        codes.append("constrained_or_insufficient_benefit")
        skipped.append("已精修並重新檢查剩餘 Band；有限搜尋未找到符合所有限制且目標 RMS 改善達 0.005 dB 的新增方案。")
    return finish(bands,codes,skipped)
