"""Protocol regression tests using synthetic, non-municipal records only."""
import copy
import random
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from psid.causal import CausalClusterer
from psid.methods import Issue, PrefixIndex, StreamConfig
from psid.schema import Record

UTC = timezone.utc
START = datetime(2024, 1, 1, 12, 34, 56, 123456, tzinfo=UTC)


def record(rid, days=0, **kwargs):
    return Record(rid, "synthetic", START + timedelta(days=days),
                  "test service", "", "100 EXAMPLE ST", "", "synthetic record", **kwargs)


def scorer(X):
    return np.full(len(X), .6)


def config(cap=20):
    return StreamConfig(t_merge=.5, max_cluster=cap, window_days=60, k_candidates=60)


class Scripted(CausalClusterer):
    """Isolate state transitions with prescribed visible targets and scores."""
    def __init__(self, plans, **kwargs):
        super().__init__(config(), scorer, **kwargs)
        self.plans = plans

    def _best(self, rec, now, phase, exclude=None):
        target_id, score = self.plans.get((rec.rid, phase), (None, -1.0))
        target = self.index.issues.get(self._iid(target_id)) if target_id else None
        if target is not None and target.iid == exclude:
            raise AssertionError("fixture requested self target")
        return target, score if target is not None else -1.0


class CausalProtocolTests(unittest.TestCase):
    def assert_valid(self, cl, n):
        summary = cl.summary()
        self.assertEqual(summary["n_events"], n)
        self.assertTrue(summary["unique_finalizations"])
        self.assertTrue(summary["complete"])
        self.assertLessEqual(summary["all_record_latency_days"]["max"], cl.D)
        self.assertLessEqual(summary["max_cluster_size"], cl.cfg.max_cluster)
        self.assertEqual(len(cl.partition()), n)
        self.assertEqual(sum(len(i.members) for i in cl.issue_of.values()), n)
        self.assertTrue(all(i.n == len(i.members) for i in cl.issue_of.values()))
        times = [e["emitted_at"] for e in cl.events]
        self.assertEqual(times, sorted(times))

    def test_issue_size_starts_empty_and_counts_one_record_once(self):
        issue = Issue("x", 0, 0)
        self.assertEqual(issue.n, 0)
        issue.absorb(record("a"))
        self.assertEqual(issue.n, 1)
        self.assertEqual(issue.members, ["a"])

    def test_arrival_exactly_one_final_event_per_record(self):
        cl = CausalClusterer(config(), scorer, delta=0, D=0)
        cl.on_record(record("a"))
        cl.on_record(record("b", .25))
        cl.finish()
        self.assertEqual([e["action"] for e in cl.events], ["NEW", "MERGE"])
        self.assertEqual(cl.merge_log, [("b", "a")])
        self.assert_valid(cl, 2)

    def test_high_confidence_claim_finalizes_target_then_claimant(self):
        cl = Scripted({("b", "arrival"): ("a", .6),
                       ("c", "arrival"): ("b", .9)}, delta=.3, D=7)
        for rec in (record("a"), record("b", 1), record("c", 2)):
            cl.on_record(rec)
        self.assertEqual([(e["object_id"], e["action"], e["trigger"]) for e in cl.events],
                         [("a", "NEW", "arrival"), ("b", "NEW", "claim"),
                          ("c", "MERGE", "arrival")])
        self.assertEqual(cl.events[1]["emitted_at"], cl.events[2]["emitted_at"])
        saved = copy.deepcopy(cl.events)
        cl.finish()
        self.assertEqual(saved, cl.events)
        self.assertEqual(cl.summary()["deadline_resolutions"], 0)
        self.assert_valid(cl, 3)

    def test_gate_bypass_also_claims_and_cancels_target_timer(self):
        cl = Scripted({("b", "arrival"): ("a", .6),
                       ("c", "arrival"): ("b", .6)},
                      delta=.3, D=7, defer_rule="gate", stale_min=5)
        for rec in (record("a"), record("b", 6), record("c", 7)):
            cl.on_record(rec)
        self.assertEqual(cl.n_deferred, 1)
        self.assertEqual(cl.summary()["claim_finalizations"], 1)
        cl.finish()
        self.assertEqual(cl.partition(), {"a": "I:a", "b": "I:b", "c": "I:b"})
        self.assert_valid(cl, 3)

    def test_deadline_claims_other_pending_then_merges_source(self):
        cl = Scripted({("b", "arrival"): ("a", .6),
                       ("c", "arrival"): ("a", .6),
                       ("b", "deadline"): ("c", .7)}, delta=.3, D=7)
        for rec in (record("a"), record("b", 1), record("c", 2)):
            cl.on_record(rec)
        cl.finish()
        self.assertEqual([(e["object_id"], e["trigger"]) for e in cl.events],
                         [("a", "arrival"), ("c", "claim"), ("b", "deadline")])
        self.assertEqual(cl.partition()["b"], cl.partition()["c"])
        self.assertEqual(cl.summary()["monthend_finalizations"], 2)
        self.assert_valid(cl, 3)

    def test_deadline_fires_at_due_even_without_new_arrivals(self):
        cl = CausalClusterer(config(), scorer, delta=.3, D=7)
        a, b = record("a"), record("b", .123456)
        cl.on_record(a)
        cl.on_record(b)
        cl.finish()
        e = next(e for e in cl.events if e["object_id"] == "b")
        self.assertEqual(e["emitted_at"], (b.when + timedelta(days=7)).isoformat())
        self.assertEqual(e["payload"]["latency_days"], 7.0)
        self.assert_valid(cl, 2)

    def test_long_empty_gap_does_not_delay_deadline(self):
        cl = CausalClusterer(config(), scorer, delta=.3, D=7)
        cl.on_record(record("a"))
        cl.on_record(record("b", 1))
        cl.on_record(record("c", 30))
        e = next(e for e in cl.events if e["object_id"] == "b")
        self.assertEqual(e["emitted_at"], record("b", 8).when.isoformat())
        cl.finish()
        self.assert_valid(cl, 3)

    def test_due_at_arrival_timestamp_happens_before_arrival_scoring(self):
        cl = CausalClusterer(config(), scorer, delta=.3, D=7, trace_candidates=True)
        for rec in (record("a"), record("b", 1), record("c", 8)):
            cl.on_record(rec)
        trace = next(t for t in cl.candidate_snapshots if t["query_id"] == "c")
        self.assertTrue(all(not c["is_pending"] for c in trace["candidates"]))
        self.assertEqual(cl.events[1]["object_id"], "b")
        cl.finish()
        self.assert_valid(cl, 3)

    def test_self_exclusion_applies_to_deadline_candidates(self):
        cl = CausalClusterer(config(), scorer, delta=.3, D=7, trace_candidates=True)
        cl.on_record(record("a"))
        cl.on_record(record("b", 1))
        cl.finish()
        for trace in cl.candidate_snapshots:
            self.assertTrue(all(trace["query_id"] not in c["member_ids"]
                                for c in trace["candidates"]))
        self.assert_valid(cl, 2)

    def test_exact_cluster_cap_allows_twenty_actual_records(self):
        cl = CausalClusterer(config(cap=20), scorer, delta=0, D=0)
        for i in range(25):
            cl.on_record(record(f"r{i:02d}", i / 100))
        cl.finish()
        self.assertEqual(sorted(i.n for i in cl.issue_of.values()), [5, 20])
        self.assert_valid(cl, 25)

    def test_invisible_pending_not_retrievable_then_visible_after_new(self):
        cl = Scripted({("b", "arrival"): ("a", .6),
                       ("c", "arrival"): ("b", .9),
                       ("d", "arrival"): ("b", .9)}, delta=.3, D=7, visible=False)
        cl.on_record(record("a"))
        cl.on_record(record("b", 1))
        self.assertNotIn("I:b", cl.index.issues)
        self.assertEqual(cl.on_record(record("c", 2)), "NEW")
        cl.advance(START + timedelta(days=8))
        self.assertIn("I:b", cl.index.issues)
        self.assertEqual(cl.on_record(record("d", 9)), "MERGE")
        cl.finish()
        self.assert_valid(cl, 4)

    def test_committed_assignments_and_events_never_change(self):
        cl = Scripted({("b", "arrival"): ("a", .9),
                       ("c", "arrival"): ("a", .6),
                       ("d", "arrival"): ("c", .9),
                       ("c", "deadline"): ("a", .9)}, delta=.3, D=7)
        cl.on_record(record("a"))
        cl.on_record(record("b", 1))
        old_assign, old_events = cl.partition(), copy.deepcopy(cl.events)
        cl.on_record(record("c", 2))
        cl.on_record(record("d", 3))
        cl.finish()
        for rid, iid in old_assign.items():
            self.assertEqual(cl.partition()[rid], iid)
        self.assertEqual(cl.events[:len(old_events)], old_events)
        self.assertNotEqual(cl.partition()["a"], cl.partition()["c"])
        self.assert_valid(cl, 4)

    def test_equal_time_arrivals_require_lexical_rid_order(self):
        cl = CausalClusterer(config(), scorer)
        cl.on_record(record("b"))
        with self.assertRaises(ValueError):
            cl.on_record(record("a"))
        cl2 = CausalClusterer(config(), scorer)
        cl2.on_record(record("a"))
        cl2.on_record(record("b"))
        cl2.finish()
        self.assert_valid(cl2, 2)

    def test_stable_score_ties_choose_lexical_issue_id(self):
        # Cap=1 first creates two committed identities without merging. Increase
        # it only for this isolated tie test so both become eligible equally.
        cl = CausalClusterer(config(cap=1), scorer, D=0)
        cl.on_record(record("z"))
        cl.on_record(record("a", 1))
        cl.cfg.max_cluster = 20
        cl.on_record(record("q", 2))
        cl.finish()
        self.assertEqual(cl.partition()["q"], "I:a")
        self.assert_valid(cl, 3)

    def test_equal_idf_top_tokens_have_lexical_secondary_key(self):
        index = PrefixIndex(60)
        tokens = [f"token{i:02d}" for i in range(9)]
        index.n_docs = 9
        index.df = {token: 1 for token in tokens}
        index.postings = {token: [(f"I:{token}", record("q").day)] for token in tokens}
        candidates = index.candidates(record("q"), list(reversed(tokens)), k=60)
        self.assertEqual(candidates, [f"I:{token}" for token in tokens[:8]])

    def test_max_lag_filters_using_elapsed_time(self):
        cl = CausalClusterer(config(), scorer, delta=0, D=0, max_lag=10)
        cl.on_record(record("a"))
        cl.on_record(record("b", 10.001))
        cl.finish()
        self.assertNotEqual(cl.partition()["a"], cl.partition()["b"])
        self.assert_valid(cl, 2)

    def test_finish_is_idempotent_and_rejects_new_arrivals(self):
        cl = CausalClusterer(config(), scorer)
        cl.on_record(record("a"))
        cl.finish()
        before = copy.deepcopy(cl.events)
        cl.finish()
        self.assertEqual(cl.events, before)
        with self.assertRaises(RuntimeError):
            cl.on_record(record("b", 1))

    def test_duplicate_finalization_and_duplicate_arrival_rejected(self):
        cl = CausalClusterer(config(), scorer)
        a = record("a")
        cl.on_record(a)
        with self.assertRaises(RuntimeError):
            cl._finalize_new(cl.issue_of["I:a"], a.when, "arrival", 0)
        with self.assertRaises(ValueError):
            cl.on_record(record("a", 1))

    def test_timezone_is_explicit_and_utc_normalized(self):
        cl = CausalClusterer(config(), scorer)
        naive = record("a")
        naive.when = naive.when.replace(tzinfo=None)
        with self.assertRaises(ValueError):
            cl.on_record(naive)
        rec = record("b")
        rec.when = rec.when.astimezone(timezone(timedelta(hours=-8)))
        cl.on_record(rec)
        self.assertTrue(cl.events[0]["emitted_at"].endswith("+00:00"))
        cl.finish()
        self.assert_valid(cl, 1)

    def test_empty_stream_finishes(self):
        cl = CausalClusterer(config(), scorer)
        cl.finish()
        self.assert_valid(cl, 0)

    def test_snapshot_is_immutable_and_contains_actual_scored_members(self):
        cl = CausalClusterer(config(), scorer, D=0, trace_candidates=True)
        cl.on_record(record("a"))
        cl.on_record(record("b", 1))
        saved = copy.deepcopy(cl.candidate_snapshots)
        cl.on_record(record("c", 2))
        self.assertEqual(saved, cl.candidate_snapshots[:len(saved)])
        last = cl.candidate_snapshots[-1]["candidates"][0]
        self.assertEqual(last["member_ids"], ["a", "b"])
        self.assertEqual(last["age_days"], 2)
        cl.finish()
        self.assert_valid(cl, 3)

    def test_prefix_outputs_equal_after_truncating_arrivals_and_advancing_clock(self):
        records = [record("a"), record("b", 1), record("c", 2), record("d", 12)]
        full = CausalClusterer(config(), scorer, delta=.3, D=7)
        prefix = CausalClusterer(config(), scorer, delta=.3, D=7)
        for r in records:
            full.on_record(r)
        full.finish()
        for r in records[:3]:
            prefix.on_record(r)
        cutoff = START + timedelta(days=10)
        prefix.advance(cutoff)
        expected = [e for e in full.events if datetime.fromisoformat(e["emitted_at"]) <= cutoff]
        self.assertEqual(prefix.events, expected)

    def test_seeded_multiphase_prefixes_and_state_invariants(self):
        rng = random.Random(219)
        records = []
        clock = START
        for i in range(65):
            clock += timedelta(hours=rng.choice([0, 1, 24, 72]))
            rec = record(f"r{i:03d}")
            rec.when = clock
            rec.address_raw = f"{100 + rng.randrange(10)} EXAMPLE ST"
            records.append(rec)

        def varied_scores(X):
            # Distance and same-address fields exercise confident, held, and
            # below-threshold paths, without reading record ids or future data.
            return np.where(X[:, 0] > .5, .95, np.where(X[:, 14] < .05, .65, .35))

        policies = [dict(delta=0, D=0), dict(delta=.3, D=2),
                    dict(delta=.3, D=2, visible=False),
                    dict(delta=.3, D=2, defer_rule="gate", stale_min=1),
                    dict(delta=0, D=0, max_lag=3)]
        for policy in policies:
            with self.subTest(policy=policy):
                full = CausalClusterer(config(cap=4), varied_scores, **policy)
                for rec in records:
                    previous = full.partition()
                    full.on_record(rec)
                    self.assertTrue(all(full.assign[rid] == iid for rid, iid in previous.items()))
                    self.assertTrue(all(len(full.issue_of[iid].members) == 1 for iid in full.pending))
                full.finish()
                self.assert_valid(full, len(records))
                for idx in (10, 30, 50):
                    cutoff = records[idx].when
                    prefix = CausalClusterer(config(cap=4), varied_scores, **policy)
                    for rec in records:
                        if rec.when <= cutoff:
                            prefix.on_record(rec)
                    prefix.advance(cutoff)
                    expected = [e for e in full.events
                                if datetime.fromisoformat(e["emitted_at"]) <= cutoff]
                    self.assertEqual(prefix.events, expected)


if __name__ == "__main__":
    unittest.main()
