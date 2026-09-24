"""Planner safeguards that run without calling OpenAI.

Run: .venv/bin/python -m unittest tests.test_planner
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from app import planner  # noqa: E402


class PlannerSafeguardTests(unittest.TestCase):
    def test_unknown_origin_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            planner.check_origin("https://elsewhere.example")
        self.assertEqual(caught.exception.status_code, 403)
        planner.check_origin("http://localhost:5173")
        planner.check_origin(None)  # non-browser clients are still rate limited

    def test_rate_limit_per_client(self):
        limiter = planner.RateLimiter()
        for _ in range(planner.PER_MINUTE):
            limiter.check("1.2.3.4")
        with self.assertRaises(HTTPException) as caught:
            limiter.check("1.2.3.4")
        self.assertEqual(caught.exception.status_code, 429)
        limiter.check("5.6.7.8")

    def test_missing_key_is_503(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(HTTPException) as caught:
                planner.plan({"question": "hello"}, provider=None)
        self.assertEqual(caught.exception.status_code, 503)

    def test_recommendations_need_real_evidence_keys(self):
        evidence = [{"key": "a>b:known", "from": "A", "to": "B",
                     "from_place": {"stop_id": "a", "name": "A", "lat": 1, "lon": 2},
                     "to_place": {"stop_id": "b", "name": "B", "lat": 3, "lon": 4},
                     "stops": [{"stop_id": "a"}, {"stop_id": "m"}, {"stop_id": "b"}], "supported_journeys": 40}]
        items = [
            {"evidence_key": "a>b:known", "action": "increase_frequency", "priority": "high", "title": "T", "rationale": "R"},
            {"evidence_key": "invented", "action": "monitor", "priority": "low", "title": "T", "rationale": "R"},
            {"evidence_key": "a>b:known", "action": "monitor", "priority": "low", "title": "dup", "rationale": "R"},
        ]
        recommendations, overlays = planner.validate_recommendations(items, evidence)
        self.assertEqual([r["evidence_key"] for r in recommendations], ["a>b:known"])
        self.assertEqual(overlays[0]["kind"], "corridor")
        self.assertEqual(overlays[0]["via_points"], [{"stop_id": "m"}])

    def test_single_stop_path_is_drawn_as_a_stop(self):
        place = {"stop_id": "a", "name": "A", "lat": 1, "lon": 2}
        _, overlays = planner.validate_recommendations(
            [{"evidence_key": "a:unknown", "action": "monitor", "priority": "low", "title": "T", "rationale": "R"}],
            [{"key": "a:unknown", "from_place": place, "to_place": place}])
        self.assertEqual(overlays[0]["kind"], "stop")

    def test_model_view_keeps_numbers_and_drops_drawing_data(self):
        result = {"analysis": "journey", "evidence": [{"key": "k", "journeys": 12, "stops": [1], "from_place": {}}],
                  "journey": {"totals": {"shown_volume": 12}, "sankey": {"nodes": []}}}
        view = planner.model_view(result)
        self.assertEqual(view["evidence"], [{"key": "k", "journeys": 12}])
        self.assertEqual(view["journey"], {"totals": {"shown_volume": 12}})
        self.assertIn("sankey", result["journey"])  # the browser still gets it


if __name__ == "__main__":
    unittest.main()
