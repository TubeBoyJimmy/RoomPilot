from copy import deepcopy

import numpy as np
import pytest

from roompilot.analysis import _settings, evaluate_peq
from roompilot.reference import choose_incumbent, compare_reference
from test_analysis import measurement, peak


def fixture():
    ms = [measurement(ch, [peak(90, 5, 4)]) for ch in ("L", "R")]
    s = _settings(dict(objective_mode="peak", guard_policy="v5", target_level=75., max_q=9., bands=3))
    q = evaluate_peq(ms, s, {ch: [peak(90, -2, 4)] for ch in ("L", "R")}, 75.)
    q.update(id="reference", name="PEQ v1", variant_title="參考策略")
    return ms, s, q


def test_reference_is_exact_feasible_seed_not_target_override():
    ms, s, q = fixture()
    before = deepcopy(q)
    selected, report = choose_incumbent(ms, s, 75., [q])
    assert selected["filters"] == q["filters"]
    assert report["checks"][0]["eligible"]
    selected["filters"]["L"][0]["gain"] = 0.
    assert q == before
    selected, report = choose_incumbent(ms, s, 74., [q])
    assert selected is None and "目標不同" in report["checks"][0]["reason"]


def test_infeasible_reference_does_not_disable_new_guards():
    ms, s, q = fixture()
    selected, report = choose_incumbent(ms, {**s, "bass_mean_cut_limit_db": 0.}, 75., [q])
    assert selected is None and not report["checks"][0]["eligible"]


def test_reference_uses_complete_response_and_reports_coverage():
    ms, s, q = fixture()
    same = compare_reference(ms, s, q["filters"], q)
    assert same["coverage_complete"]
    assert all(ch["max_local_mean_difference_db"] == pytest.approx(0) for ch in same["channels"])
    deeper = {ch: [peak(90, -3, 4)] for ch in ("L", "R")}
    changed = compare_reference(ms, s, deeper, q)
    assert all(ch["mean_gain_difference_db"] < 0 for ch in changed["channels"])
    truncated = deepcopy(ms)
    for m in truncated:
        mask = np.asarray(m["frequency"]) >= 100
        m["frequency"] = np.asarray(m["frequency"])[mask].tolist()
        m["spl"] = np.asarray(m["spl"])[mask].tolist()
    assert not compare_reference(truncated, s, deeper, q)["coverage_complete"]
