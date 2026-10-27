from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
import tempfile

import numpy as np

from audit.compare_prefix import compare
from analysis.revision_audit import FutureContextScorer, probe_prefixes, rotate_suffix
from psid.causal import CausalClusterer
from psid.methods import StreamConfig
from psid.schema import Record


def event(time, rid="a"):
    return {"emitted_at": time, "object_id": rid, "action": "NEW", "target_id": None}


class CompletePrefixFaultTests(unittest.TestCase):
    def setUp(self):
        self.base = {"common_randomness": "fixed-model-and-rng",
                     "events": [event("2024-05-01T00:00:00+00:00"),
                                event("2024-05-02T00:00:00+00:00", "b")]}
        self.cutoff = "2024-05-01T12:00:00+00:00"

    def test_extra_emission_after_preserved_first_k_is_detected(self):
        altered = deepcopy(self.base)
        altered["events"].insert(1, event("2024-05-01T10:00:00+00:00", "extra"))
        self.assertFalse(compare(self.base, altered, self.cutoff)["equal"])

    def test_missing_emission_is_detected(self):
        altered = deepcopy(self.base)
        altered["events"].pop(0)
        self.assertFalse(compare(self.base, altered, self.cutoff)["equal"])

    def test_same_content_earlier_emission_is_detected(self):
        altered = deepcopy(self.base)
        altered["events"][0]["emitted_at"] = "2024-04-30T23:59:00+00:00"
        self.assertFalse(compare(self.base, altered, self.cutoff)["equal"])

    def test_future_only_change_passes(self):
        altered = deepcopy(self.base)
        altered["events"][1]["object_id"] = "changed-future"
        self.assertTrue(compare(self.base, altered, self.cutoff)["equal"])

    def test_rng_mismatch_is_invalid_not_pass(self):
        altered = deepcopy(self.base)
        altered["common_randomness"] = "different"
        self.assertFalse(compare(self.base, altered, self.cutoff)["valid_comparison"])


class FutureInputFaultTests(unittest.TestCase):
    def records(self):
        start = datetime(2024, 5, 1, tzinfo=timezone.utc)
        return [Record(str(i), "test", start + timedelta(days=i), "s", "", "", "", "text " * (i+1))
                for i in range(5)]

    def test_rotation_preserves_prefix_and_future_multiset(self):
        source = self.records()
        changed = rotate_suffix(source, source[1].when, shift=1)
        self.assertEqual([r.visible_dict() for r in source[:2]],
                         [r.visible_dict() for r in changed[:2]])
        self.assertEqual(sorted(r.rid for r in source), sorted(r.rid for r in changed))
        self.assertNotEqual([r.rid for r in source], [r.rid for r in changed])

    def test_positive_control_changes_scores_before_engine(self):
        source = self.records()
        changed = rotate_suffix(source, source[1].when, shift=1)
        calls = []
        def base(X):
            calls.append(len(X))
            return np.full(len(X), .2)
        a, b = FutureContextScorer(base, source), FutureContextScorer(base, changed)
        X = np.zeros((2, 21))
        X[:, 7] = [.1, .9]
        self.assertFalse(np.array_equal(a(X), b(X)))
        self.assertEqual(calls, [2, 2])

    def test_probe_runs_actual_engine_and_preserves_normal_prefixes(self):
        start = datetime(2024, 5, 1, tzinfo=timezone.utc)
        records = [Record(str(i).zfill(3), "test", start + timedelta(hours=i),
                          "road", "", "10 Main Street", "", "road road " + "hole " * (i+1))
                   for i in range(20)]
        scorer = lambda X: np.full(len(X), .5)
        cfg = StreamConfig(t_merge=.4, t_pending=0, max_cluster=20)
        def factory(context):
            return CausalClusterer(cfg, scorer, delta=.2, D=.125)
        def leaky_factory(context):
            return CausalClusterer(cfg, FutureContextScorer(scorer, context), delta=.2, D=.125)
        with tempfile.TemporaryDirectory() as directory:
            report = probe_prefixes(records, factory, leaky_factory,
                                    {"test": "synthetic integration only"}, directory)
        self.assertEqual(report["normal_pass_count"], 3)
        self.assertGreaterEqual(report["leaky_detected_count"], 1)


if __name__ == "__main__":
    unittest.main()
