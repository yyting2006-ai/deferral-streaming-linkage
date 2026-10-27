"""Coverage checks distinguish pre-cutoff deadline replay from a later drain."""
from datetime import datetime, timezone
import unittest

from analysis.round2_prefix_audit import prefix_coverage, scheduling_coverage


class Round2CoverageTests(unittest.TestCase):
    cutoff = datetime(2024, 5, 20, tzinfo=timezone.utc)

    def event(self, at, trigger):
        return {"emitted_at": at, "trigger": trigger,
                "payload": {"due_at": at if trigger == "deadline" else None,
                            "latency_days": 7.0 if trigger == "deadline" else 0.0}}

    def test_count_each_prefix_trigger_and_exclude_late_drain(self):
        early = "2024-05-19T00:00:00+00:00"
        events = [self.event(early, k) for k in ("arrival", "claim", "deadline")]
        events.append(self.event("2024-06-01T00:00:00+00:00", "deadline"))
        result = prefix_coverage(events, self.cutoff)
        self.assertEqual(result["trigger_counts"], {"arrival": 1, "claim": 1, "deadline": 1})
        self.assertEqual(result["deadline_events_at_exact_due"], 1)
        self.assertEqual(result["deadline_wait_days"], [7.0])

    def test_unknown_trigger_fails_instead_of_undercounting(self):
        with self.assertRaises(ValueError):
            prefix_coverage([self.event(self.cutoff.isoformat(), "other")], self.cutoff)

    def rows(self):
        at = "2024-05-19T00:00:00+00:00"
        return [{"at": at, "phase": p, "finishing": False,
                 "last_arrival_at": "2024-05-18T00:00:00+00:00"}
                for p in ("deadline", "deadline", "arrival", "arrival")]

    def test_timer_first_ties_count_even_without_arrival_output(self):
        result = scheduling_coverage(self.rows(), self.cutoff)
        self.assertEqual(result["equal_time_deadline_arrival_timestamps"], 1)
        self.assertEqual(result["equal_time_deadline_calls"], 2)
        self.assertEqual(result["timer_first_violations"], 0)

    def test_late_timer_and_equal_time_visibility_are_detected(self):
        rows = self.rows()[2:] + self.rows()[:2]
        rows[-1]["last_arrival_at"] = rows[-1]["at"]
        result = scheduling_coverage(rows, self.cutoff)
        self.assertEqual(result["timer_first_violations"], 2)
        self.assertEqual(result["deadline_calls_with_equal_or_future_arrival_visible"], 1)

    def test_monthend_finish_is_explicit_not_inferred_from_output_time(self):
        rows = self.rows()
        rows[0]["finishing"] = True
        result = scheduling_coverage(rows, self.cutoff)
        self.assertEqual(result["deadline_calls_during_finish"], 1)


if __name__ == "__main__":
    unittest.main()
