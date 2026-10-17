"""Tests for the daily learning step. Run: python -m unittest tests/test_learn.py"""
import importlib.util
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("learn", Path(__file__).resolve().parents[1] / "tools" / "learn.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def synthetic(bias_at_issue=0.0):
    runs, obs = [], {}
    base = datetime(2026, 10, 3, 0, tzinfo=timezone.utc)
    for d in range(14):
        t0 = base + timedelta(days=d)
        days = [{"iso": (datetime(t0.year, t0.month, t0.day, 4, tzinfo=timezone.utc) + timedelta(days=k)).isoformat().replace("+00:00", ".000Z"),
                 "p5": 90, "p50": 150, "p95": 260, "cams": 185 if k <= 3 else None} for k in range(1, 16)]
        runs.append({"t0": t0.isoformat(), "issued": t0.isoformat(), "learn": {"bias": [bias_at_issue] * 3, "spread": [1, 1, 1]},
                     "regions": {"national": {"now": 160, "days": days}}})
    for d in range(16):
        obs[(base + timedelta(days=d, hours=4)).strftime("%Y-%m-%dT%H")] = {"national": 190}
    return runs, obs


class LearnTest(unittest.TestCase):
    def test_bias_measured_by_lead(self):
        runs, obs = synthetic()
        stats, latest, _ = L.score(runs, obs, datetime(2026, 10, 18).date(), {"bias": [0, 0, 0], "spread": [1, 1, 1]})
        for g in stats:
            self.assertGreater(g["days"], 0)
            self.assertAlmostEqual(g["bias_log_7d"], math.log(190 / 150), places=2)

    def test_corrections_already_made_are_not_counted_twice(self):
        runs, obs = synthetic(bias_at_issue=0.0)
        cur = {"bias": [math.log(190 / 150)] * 3, "spread": [1, 1, 1]}
        stats, _, _ = L.score(runs, obs, datetime(2026, 10, 18).date(), cur)
        for g in stats:
            self.assertAlmostEqual(g["bias_log_7d"], 0.0, places=2)

    def test_no_change_before_three_scored_days(self):
        runs, obs = synthetic()
        obs = dict(list(sorted(obs.items()))[-1:])
        stats, _, _ = L.score(runs, obs, datetime(2026, 10, 18).date(), {"bias": [0, 0, 0], "spread": [1, 1, 1]})
        self.assertTrue(all(g.get("days", 0) < L.MIN_DAYS for g in stats))


class CamsTest(unittest.TestCase):
    def test_cams_scored_on_same_days(self):
        runs, obs = synthetic()
        stats, _, _ = L.score(runs, obs, datetime(2026, 10, 18).date(), {"bias": [0, 0, 0], "spread": [1, 1, 1]})
        g = stats[0]
        self.assertGreaterEqual(g["cams_days"], 5)
        self.assertLess(g["mae_cams"], g["mae_twin_same"])


if __name__ == "__main__":
    unittest.main()
