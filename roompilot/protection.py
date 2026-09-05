"""Explicit engineering guardrails, evaluated on each original recording.

These are magnitude diagnostics, not acoustic-null detection or listening scores.
All means use the supplied logarithmic grid, before preamp. Missing range is never
extrapolated: the fixed bass guard applies to the available overlap and reports it.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d


GUARD_DEFAULTS = dict(guard_policy="legacy", added_deficit_rms_limit_db=1.,
                      added_deficit_max_limit_db=3., local_cut_limit_db=4.,
                      bass_mean_cut_limit_db=3., local_window_octaves=1 / 3,
                      outside_cut_limit_db=3.)


def added_deficit(ys, response, target):
    """New deficit at every bin; boosts that reduce a deficit incur zero cost."""
    original = np.maximum(float(target) - np.asarray(ys), 0.)
    return np.maximum(0., np.maximum(float(target) - np.asarray(ys) - response, 0.) - original)


class ProtectionModel:
    """Shared batched constraints for search, rounded validation and diagnostics.

    ``response`` has shape (..., bins). ``slacks`` has shape (..., constraints),
    with >= 0 feasible. Limits apply independently to each recording, never to
    their average. With historical policy, slacks has zero constraints while
    diagnostics remain available, explicitly marked as not enforced.
    """
    def __init__(self, grid, ys, target, settings, *, names=None):
        self.grid = np.asarray(grid, dtype=float)
        self.ys = np.atleast_2d(np.asarray(ys, dtype=float))
        if (self.grid.ndim != 1 or len(self.grid) < 2 or self.ys.ndim != 2 or not len(self.ys) or np.any(self.grid <= 0)
                or np.any(np.diff(self.grid) <= 0) or self.ys.shape[1] != len(self.grid)
                or not np.all(np.isfinite(self.grid)) or not np.all(np.isfinite(self.ys))):
            raise ValueError("保護指標需要遞增頻率與完整的有限量測資料。")
        self.s = {**GUARD_DEFAULTS, **settings}
        self.target = float(target)
        if not np.isfinite(self.target):
            raise ValueError("保護指標目標需為有限數值。")
        self.enforced = self.s["guard_policy"] == "v5"
        self.names = [str(n) if not isinstance(n, (tuple, list)) else " · ".join(map(str, n))
                      for n in (names if names is not None else range(1, len(self.ys) + 1))]
        if len(self.names) != len(self.ys):
            raise ValueError("逐筆量測名稱數量不一致。")
        below = self.ys < self.target
        self.below_counts = below.sum(axis=1)
        self.below_weights = below / np.maximum(self.below_counts[:, None], 1)
        self.original_deficit = np.maximum(self.target - self.ys, 0.)
        x = np.log2(self.grid)
        self.window = float(self.s["local_window_octaves"])
        if not np.isfinite(self.window) or self.window <= 0:
            raise ValueError("局部窗口需為有限正數。")
        centers = x[(x - self.window / 2 >= x[0] - 1e-10) &
                    (x + self.window / 2 <= x[-1] + 1e-10)]
        self.local_complete = bool(len(centers))
        if self.local_complete:
            self.starts = np.searchsorted(x, centers - self.window / 2, side="left")
            self.ends = np.searchsorted(x, centers + self.window / 2, side="right")
            self.centers = np.exp2(centers)
        else:
            self.starts, self.ends = np.array([0]), np.array([len(x)])
            self.centers = np.array([np.sqrt(self.grid[0] * self.grid[-1])])
        # Include both boundary-aligned windows, so edge peaks are not omitted.
        if x[-1] - x[0] >= self.window:
            self.local_complete = True
            self.starts = np.r_[0, self.starts, np.searchsorted(x, x[-1] - self.window)]
            self.ends = np.r_[np.searchsorted(x, x[0] + self.window, side="right"), self.ends, len(x)]
            self.centers = np.r_[2 ** (x[0] + self.window / 2), self.centers, 2 ** (x[-1] - self.window / 2)]
        self.bass = (self.grid >= 80.) & (self.grid <= 200.)
        lo, hi = max(80., float(self.grid[0])), min(200., float(self.grid[-1]))
        self.bass_complete = self.grid[0] <= 80. + 1e-9 and self.grid[-1] >= 200. - 1e-9
        self.bass_fraction = float(max(0., np.log2(hi / lo)) / np.log2(200 / 80)) if hi > lo else 0.
        self.sigma = (1 / 3) / max(float(np.median(np.diff(x))), 1e-9) / 2.355
        self.constraint_names = []
        if self.enforced:
            for metric in ("below_target_cut_rms", "added_deficit_rms", "added_deficit_max"):
                self.constraint_names.extend(f"{metric}:{name}" for name in self.names)
            self.constraint_names.append("local_mean_cut")
            if self.bass.any():
                self.constraint_names.append("bass_80_200_mean_cut" if self.bass_complete else "bass_available_overlap_mean_cut")

    def _metrics(self, response):
        response = np.asarray(response, dtype=float)
        if response.shape[-1] != len(self.grid) or not np.all(np.isfinite(response)):
            raise ValueError("保護指標 EQ 曲線需為相同網格上的有限數值。")
        cut = np.maximum(-response, 0.)
        low = np.sqrt(cut ** 2 @ self.below_weights.T)
        # Loop recordings rather than materializing batches x recordings x bins.
        rms, maximum = [], []
        for y, original in zip(self.ys, self.original_deficit):
            added = np.maximum(0., np.maximum(self.target - y - response, 0.) - original)
            rms.append(np.sqrt(np.mean(added ** 2, axis=-1)))
            maximum.append(np.max(added, axis=-1))
        prefix = np.concatenate((np.zeros(cut.shape[:-1] + (1,)), np.cumsum(cut, axis=-1)), axis=-1)
        windows = (prefix[..., self.ends] - prefix[..., self.starts]) / (self.ends - self.starts)
        bass = np.mean(cut[..., self.bass], axis=-1) if self.bass.any() else np.zeros(cut.shape[:-1])
        return low, np.stack(rms, axis=-1), np.stack(maximum, axis=-1), windows, bass

    def slacks(self, response):
        response = np.asarray(response, dtype=float)
        if not self.enforced:
            return np.empty(response.shape[:-1] + (0,))
        low, rms, maximum, windows, bass = self._metrics(response)
        parts = [float(self.s.get("low_cut_limit_db", 1.)) - low,
                 self.s["added_deficit_rms_limit_db"] - rms,
                 self.s["added_deficit_max_limit_db"] - maximum,
                 (self.s["local_cut_limit_db"] - windows.max(axis=-1))[..., None]]
        if self.bass.any():
            parts.append((self.s["bass_mean_cut_limit_db"] - bass)[..., None])
        return np.concatenate(parts, axis=-1)

    def diagnostics(self, response):
        response = np.asarray(response, dtype=float)
        if response.ndim != 1:
            raise ValueError("可讀保護診斷只接受一條完整 EQ 曲線。")
        low, rms, maximum, windows, bass = self._metrics(response)
        records = []
        for i, y in enumerate(self.ys):
            corrected = y + response
            trend_before = gaussian_filter1d(y, self.sigma, mode="nearest")
            trend_after = gaussian_filter1d(corrected, self.sigma, mode="nearest")
            records.append(dict(name=self.names[i], below_target_cut_rms_db=float(low[i]),
                                below_target_bin_count=int(self.below_counts[i]),
                                added_deficit_rms_db=float(rms[i]), added_deficit_max_db=float(maximum[i]),
                                newly_below_bin_count=int(np.sum((y >= self.target) & (corrected < self.target))),
                                broad_trend_before_rms_db=float(np.sqrt(np.mean((trend_before - self.target) ** 2))),
                                broad_trend_after_rms_db=float(np.sqrt(np.mean((trend_after - self.target) ** 2)))))
        wi = int(np.argmax(windows))
        bass_info = dict(mean_db=float(bass) if self.bass.any() else None,
                         requested_min_hz=80., requested_max_hz=200.,
                         actual_min_hz=float(self.grid[self.bass][0]) if self.bass.any() else None,
                         actual_max_hz=float(self.grid[self.bass][-1]) if self.bass.any() else None,
                         coverage_complete=bool(self.bass_complete), coverage_fraction=self.bass_fraction,
                         scope="full_range" if self.bass_complete else "available_overlap" if self.bass.any() else "unavailable")
        slacks = self.slacks(response)
        return dict(policy=self.s["guard_policy"], enforced=self.enforced, basis="individual_recordings",
                    limits={k: self.s[k] for k in GUARD_DEFAULTS if k != "guard_policy"},
                    low_cut_limit_db=float(self.s.get("low_cut_limit_db", 1.)),
                    records=records, record_count=len(records),
                    worst_below_target_cut_rms_db=float(low.max()),
                    worst_added_deficit_rms_db=float(rms.max()), worst_added_deficit_max_db=float(maximum.max()),
                    local_cut=dict(max_mean_db=float(windows[wi]), center_hz=float(self.centers[wi]),
                                   actual_min_hz=float(self.grid[self.starts[wi]]), actual_max_hz=float(self.grid[self.ends[wi] - 1]),
                                   window_octaves=self.window, coverage_complete=self.local_complete),
                    bass_mean_cut=bass_info,
                    constraints=[dict(name=name, slack_db=float(value), passed=bool(value >= -1e-6))
                                 for name, value in zip(self.constraint_names, slacks)],
                    feasible=bool(np.all(slacks >= -1e-6)) if self.enforced else None,
                    broad_trend=dict(smoothing="1/3 octave Gaussian FWHM; actual response", enforced=False,
                                     interpretation="診斷校正後寬頻走勢；不是聽感排名或物理模態辨識"),
                    range=dict(min_hz=float(self.grid[0]), max_hz=float(self.grid[-1]), points=len(self.grid)),
                    definitions=dict(added_deficit="max(0, max(T-y-EQ,0)-max(T-y,0))",
                                     aggregation="逐筆量測各自驗算；每項取最大值；不含前級",
                                     attenuation="mean(max(-EQ,0))；增益不能抵銷其他頻點的削減"))

    def describe_violations(self, response, *, tolerance=1e-6):
        """Concrete failed measurements/limits, including a newly extended window."""
        data = self.diagnostics(response)
        entries = []
        labels = (("below_target_cut_rms", "原低處額外減益 RMS", float(self.s.get("low_cut_limit_db", 1.))),
                  ("added_deficit_rms", "新增低處 RMS", self.s["added_deficit_rms_limit_db"]),
                  ("added_deficit_max", "新增低處最大值", self.s["added_deficit_max_limit_db"]))
        for item in data["constraints"]:
            if item["slack_db"] >= -tolerance:
                continue
            for prefix, label, limit in labels:
                if item["name"].startswith(prefix + ":"):
                    name = item["name"][len(prefix) + 1:]
                    entries.append(f"{name}：{label} {limit - item['slack_db']:.3f} dB，上限 {limit:.3f} dB")
                    break
            else:
                if item["name"] == "local_mean_cut":
                    local = data["local_cut"]
                    entries.append(f"局部 {local['actual_min_hz']:.1f}–{local['actual_max_hz']:.1f} Hz 平均減益 {local['max_mean_db']:.3f} dB，上限 {self.s['local_cut_limit_db']:.3f} dB")
                elif item["name"].startswith("bass_"):
                    bass = data["bass_mean_cut"]
                    label = "80–200 Hz" if bass["coverage_complete"] else f"80–200 Hz 的可用交集 {bass['actual_min_hz']:.1f}–{bass['actual_max_hz']:.1f} Hz"
                    entries.append(f"{label} 平均減益 {bass['mean_db']:.3f} dB，上限 {self.s['bass_mean_cut_limit_db']:.3f} dB")
        return "；".join(entries)

    summary = diagnostics


class CurveFootprint:
    """Constrain the complete EQ curve beyond the requested fitting domain.

    This needs no room SPL and makes no claim about unmeasured acoustic effects.
    Boundary probes participate in maximum attenuation checks but not the local
    means, so adding almost coincident probes cannot overweight a window edge.
    """
    constraint_names = ["outside_range_cut", "full_range_local_mean_cut"]

    def __init__(self, settings):
        self.s = {**GUARD_DEFAULTS, **settings}
        self.enforced = self.s["guard_policy"] == "v5"
        self.outside_cut_limit_db = float(self.s["outside_cut_limit_db"])
        self.local_cut_limit_db = float(self.s["local_cut_limit_db"])
        self.low, self.high = float(settings["f_min"]), float(settings["f_max"])
        nyquist = float(settings["sample_rate"]) / 2
        end = int(np.floor(np.log2(nyquist) * 192))
        base = np.unique(np.r_[1., np.exp2(np.arange(end + 1) / 192), nyquist])
        probes = np.array([edge * scale for edge in (self.low, self.high)
                           for scale in (1 - 1e-7, 1., 1 + 1e-7)])
        self.grid = np.unique(np.r_[base, probes[(probes >= 1.) & (probes <= nyquist)]])
        self.local_indices = np.searchsorted(self.grid, base)
        self.local_grid = base
        self.outside = (self.grid < self.low) | (self.grid > self.high)
        x = np.log2(base)
        self.window = float(self.s["local_window_octaves"])
        centers = x[(x - self.window / 2 >= x[0] - 1e-10) &
                    (x + self.window / 2 <= x[-1] + 1e-10)]
        self.starts = np.r_[0, np.searchsorted(x, centers - self.window / 2, side="left"),
                            np.searchsorted(x, x[-1] - self.window)]
        self.ends = np.r_[np.searchsorted(x, x[0] + self.window, side="right"),
                          np.searchsorted(x, centers + self.window / 2, side="right"), len(x)]
        self.centers = np.exp2(np.r_[x[0] + self.window / 2, centers, x[-1] - self.window / 2])

    def _metrics(self, response):
        response = np.asarray(response, dtype=float)
        if response.shape[-1] != len(self.grid) or not np.all(np.isfinite(response)):
            raise ValueError("完整 EQ 範圍保護需要相同網格的有限數值。")
        cut = np.maximum(-response, 0.)
        outside = np.max(cut[..., self.outside], axis=-1, initial=0.)
        local = cut[..., self.local_indices]
        prefix = np.concatenate((np.zeros(local.shape[:-1] + (1,)), np.cumsum(local, axis=-1)), axis=-1)
        windows = (prefix[..., self.ends] - prefix[..., self.starts]) / (self.ends - self.starts)
        return outside, windows

    def slacks(self, response):
        response = np.asarray(response, dtype=float)
        if not self.enforced:
            return np.empty(response.shape[:-1] + (0,))
        outside, windows = self._metrics(response)
        return np.stack((self.outside_cut_limit_db - outside,
                         self.local_cut_limit_db - windows.max(axis=-1)), axis=-1)

    def diagnostics(self, response):
        response = np.asarray(response, dtype=float)
        if response.ndim != 1:
            raise ValueError("可讀完整 EQ 診斷只接受一條曲線。")
        outside, windows = self._metrics(response)
        wi = int(np.argmax(windows))
        oi = int(np.argmin(response[self.outside])) if self.outside.any() else None
        slacks = self.slacks(response)
        return dict(policy=self.s["guard_policy"], enforced=self.enforced,
                    scope="complete_eq_without_acoustic_inference",
                    outside_cut=dict(max_db=float(outside),
                                     frequency_hz=float(self.grid[self.outside][oi]) if oi is not None else None,
                                     requested_min_hz=self.low, requested_max_hz=self.high,
                                     limit_db=self.outside_cut_limit_db),
                    local_cut=dict(max_mean_db=float(windows[wi]), center_hz=float(self.centers[wi]),
                                   actual_min_hz=float(self.local_grid[self.starts[wi]]),
                                   actual_max_hz=float(self.local_grid[self.ends[wi] - 1]),
                                   window_octaves=self.window, limit_db=self.local_cut_limit_db),
                    range=dict(min_hz=float(self.grid[0]), max_hz=float(self.grid[-1]), points=len(self.grid),
                               spacing="192 points/octave plus endpoints and fitting-boundary probes"),
                    constraints=[dict(name=name, slack_db=float(value), passed=bool(value >= -1e-6))
                                 for name, value in zip(self.constraint_names, slacks)],
                    feasible=bool(np.all(slacks >= -1e-6)) if self.enforced else None,
                    note="檢查濾波器合成減益，含指定頻段之外的尾端；不含前級，不推定未量位置／頻率的聲學反應。")

    def describe_violations(self, response, *, tolerance=1e-6):
        data = self.diagnostics(response)
        entries = []
        for item in data["constraints"]:
            if item["slack_db"] >= -tolerance:
                continue
            if item["name"] == "outside_range_cut":
                value = data["outside_cut"]
                entries.append(f"指定 {self.low:g}–{self.high:g} Hz 以外，{value['frequency_hz']:.2f} Hz 合成減益 {value['max_db']:.3f} dB，上限 {value['limit_db']:.3f} dB")
            else:
                value = data["local_cut"]
                entries.append(f"完整 EQ 的局部 {value['actual_min_hz']:.1f}–{value['actual_max_hz']:.1f} Hz 平均減益 {value['max_mean_db']:.3f} dB，上限 {value['limit_db']:.3f} dB")
        return "；".join(entries)
