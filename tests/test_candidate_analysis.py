import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from analysis.candidate_analysis import (CandidateTraceWriter, candidate_graph_report,
                                         candidate_trace_rows, frozen_candidate_comparison)


def snapshot(query, candidates, scores=None):
    scores = scores or [0.9] * len(candidates)
    return {"query_id": query, "query_at": "2024-05-01T00:00:00+00:00", "phase": "arrival",
            "candidates": [{"record_id": r, "issue_id": r, "member_ids": [r],
                            "score": s, "age_days": 1} for r, s in zip(candidates, scores)]}


class CandidateBoundsTests(unittest.TestCase):
    def test_direct_is_not_transitive_ceiling(self):
        rows = [snapshot("b", ["a"]), snapshot("c", ["b"])]
        result = candidate_graph_report(rows, dict.fromkeys("abc", "gold"), "abc")
        self.assertEqual(result["direct_positive_pairs"], 2)
        self.assertAlmostEqual(result["direct_positive_pair_coverage"], 2/3)
        self.assertEqual(result["representative_graph"]["unconstrained_reachable_positive_pairs"], 3)

    def test_cluster_cap_changes_upper_bound(self):
        rows = [snapshot("b", ["a"]), snapshot("c", ["b"]), snapshot("d", ["c"])]
        result = candidate_graph_report(rows, dict.fromkeys("abcd", "gold"), "abcd", cap=3)
        graph = result["representative_graph"]
        self.assertEqual(graph["unconstrained_reachable_positive_pairs"], 6)
        self.assertEqual(graph["cap_aware_positive_pair_upper_bound"], 3)
        self.assertEqual(graph["feasible_graph_greedy_positive_pairs"], 3)

    def test_foreign_bridge_shows_relaxation(self):
        rows = [snapshot("b", ["a"]), snapshot("c", ["b"])]
        graph = candidate_graph_report(rows, {"a": "x", "b": "y", "c": "x"}, "abc")["representative_graph"]
        self.assertEqual(graph["unconstrained_reachable_positive_pairs"], 1)
        self.assertEqual(graph["feasible_graph_greedy_positive_pairs"], 0)
        self.assertFalse(graph["greedy_equals_upper_bound"])

    def test_private_ids_not_returned(self):
        result = candidate_graph_report([snapshot("PRIVATE-B", ["PRIVATE-A"])],
                                        {"PRIVATE-A": "PRIVATE-GOLD", "PRIVATE-B": "PRIVATE-GOLD"},
                                        ["PRIVATE-A", "PRIVATE-B"])
        self.assertNotIn("PRIVATE", str(result))

    def test_incremental_trace_drains_memory_and_roundtrips(self):
        row = snapshot("b", ["a"])
        engine = SimpleNamespace(candidate_snapshots=[row])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private_trace.jsonl"
            with CandidateTraceWriter(path) as writer:
                writer.drain(engine)
                self.assertEqual(engine.candidate_snapshots, [])
                engine.candidate_snapshots.append(snapshot("c", ["b"]))
                writer.drain(engine)
                self.assertEqual(writer.rows, 2)
            rows = list(candidate_trace_rows(path))
            self.assertEqual(rows[0], row)
            self.assertEqual(len(rows), 2)

    def test_common_candidate_static_control(self):
        rows = [snapshot("b", ["a"]), snapshot("c", ["b"])]
        rows[1]["candidates"][0]["age_days"] = 20
        result = frozen_candidate_comparison(rows, dict.fromkeys("abc", "g"), "abc", .5)
        self.assertEqual(result["arms"][0]["true_positive_pairs"], 3)
        self.assertEqual(result["arms"][1]["true_positive_pairs"], 1)
        self.assertIn("not an end-to-end", result["limitation"])

    def test_frozen_comparison_ignores_queries_outside_evaluation_universe(self):
        rows = [snapshot("external", ["a"]), snapshot("b", ["a"])]
        result = frozen_candidate_comparison(rows, dict.fromkeys("ab", "g"),
                                             "ab", .5, cap=2)
        self.assertEqual(result["candidate_trace_rows"], 1)
        for arm in result["arms"]:
            self.assertEqual(arm["accepted_edge_unions"], 1)
            self.assertEqual(arm["true_positive_pairs"], 1)
            self.assertEqual(arm["recall"], 1)


if __name__ == "__main__":
    unittest.main()
