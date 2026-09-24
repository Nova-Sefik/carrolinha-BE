import unittest

from app.duckdb_provider import _golden_derived


class GoldenDerivedTests(unittest.TestCase):
    def test_direction_headway_and_percentile_are_backend_values(self):
        result = _golden_derived(468.0, 0.253, 2, 3.77)
        self.assertEqual(result["from_to_per_day"], 118.4)
        self.assertEqual(result["to_from_per_day"], 349.6)
        self.assertEqual(result["share_b_to_a"], 0.747)
        self.assertEqual(result["peak_headway_min"], 30)
        self.assertEqual(result["demand_top_percent"], 1)

    def test_missing_direction_and_service_data_remain_unknown(self):
        result = _golden_derived(100.0, None, None, 2.5)
        self.assertIsNone(result["from_to_per_day"])
        self.assertIsNone(result["to_from_per_day"])
        self.assertIsNone(result["share_b_to_a"])
        self.assertIsNone(result["peak_headway_min"])


if __name__ == "__main__":
    unittest.main()
