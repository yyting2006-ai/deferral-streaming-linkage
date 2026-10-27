"""Candidate-graph diagnostics, with explicit bounds rather than a false ceiling.

Input snapshots and stable record IDs are PRIVATE; this module returns aggregates.
An undirected edge joins each query to the representative actually scored. The
expanded graph additionally joins it to members of that candidate issue. Neither
graph is a counterfactual replay: its edges are conditional on the observed policy.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import hashlib
import pickle
from pathlib import Path


class CandidateTraceWriter:
    """Drain per-event engine snapshots to a PRIVATE JSONL file incrementally.

    Call drain(engine) after each on_record(), and once more after finish(). Only
    one arrival's snapshots are retained in memory. The writer never puts raw
    identities into an aggregate result. Do not publish its output file.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8", newline="\n")
        self.rows = 0
        self.candidate_instances = 0

    def drain(self, engine):
        for row in engine.candidate_snapshots:
            self.handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            self.rows += 1
            self.candidate_instances += len(row["candidates"])
        engine.candidate_snapshots.clear()

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def candidate_trace_rows(path):
    """Read a PRIVATE candidate JSONL trace without loading it all into memory."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


class DSU:
    def __init__(self, nodes=()):
        self.parent = {x: x for x in nodes}
        self.size = {x: 1 for x in nodes}

    def find(self, x):
        if x not in self.parent:
            self.parent[x], self.size[x] = x, 1
        while x != self.parent[x]:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, cap=None):
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        if cap is not None and self.size[a] + self.size[b] > cap:
            return False
        if (self.size[a], a) < (self.size[b], b):
            a, b = b, a
        self.parent[b] = a
        self.size[a] += self.size[b]
        return True


def choose2(n):
    return n * (n - 1) // 2


def pairs_within_labels(component, universe):
    """Workflow-positive denominator, restricted to the evaluated record universe."""
    sizes = Counter(component[x] for x in universe if x in component)
    return sum(choose2(n) for n in sizes.values())


def _partition_metrics(dsu, component, universe):
    known = [x for x in universe if x in component]
    predicted = Counter(dsu.find(x) for x in known)
    joint = Counter((dsu.find(x), component[x]) for x in known)
    tp = sum(choose2(n) for n in joint.values())
    n_pred = sum(choose2(n) for n in predicted.values())
    n_gold = pairs_within_labels(component, universe)
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_gold if n_gold else 0.0
    return {"true_positive_pairs": tp, "predicted_known_pairs": n_pred,
            "gold_positive_pairs": n_gold, "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall)
            if precision + recall else 0.0}


def candidate_graph_report(snapshots, component, record_ids, cap=20):
    """Compute direct coverage, unconstrained reachability, and capped bounds.

    The capped upper bound partitions each gold-label count within each connected
    component into bins of at most cap. It ignores connectivity, time ordering,
    false-positive penalties, and required intervening nodes; it is an upper
    bound, NOT an exact optimum. A deterministic greedy edge construction joins
    only same-gold components while respecting cap and supplies a feasible graph
    construction. It is NOT claimed feasible under online arrival/commit order.
    """
    if cap < 2:
        raise ValueError("cap must be at least 2")
    # Reuse one string object per record across millions of edge endpoints.
    canonical = {str(x): str(x) for x in record_ids}
    universe = set(canonical)
    direct, expanded = set(), set()
    n_calls = n_scored = 0
    phase_calls, phase_instances, phase_positive = Counter(), Counter(), {}
    for row in snapshots:
        a = canonical.get(str(row["query_id"]))
        if a is None:
            continue
        n_calls += 1
        phase = row.get("phase", "unspecified")
        phase_calls[phase] += 1
        phase_positive.setdefault(phase, set())
        for candidate in row["candidates"]:
            b = canonical.get(str(candidate["record_id"]))
            if b is not None and a != b:
                direct.add(tuple(sorted((a, b))))
                n_scored += 1
                phase_instances[phase] += 1
                if a in component and b in component and component[a] == component[b]:
                    phase_positive[phase].add(tuple(sorted((a, b))))
            for b in candidate.get("member_ids", [b]):
                b = canonical.get(str(b))
                if b is not None and a != b:
                    expanded.add(tuple(sorted((a, b))))
    expanded |= direct
    n_gold = pairs_within_labels(component, universe)
    direct_positive = sum(a in component and b in component and
                          component[a] == component[b] for a, b in direct)

    def graph_bounds(edges):
        graph = DSU(universe)
        for a, b in sorted(edges):
            graph.union(a, b)
        joint = Counter((graph.find(x), component[x]) for x in universe
                        if x in component)
        reachable = sum(choose2(n) for n in joint.values())
        capped_upper = sum((n // cap) * choose2(cap) + choose2(n % cap)
                           for n in joint.values())
        feasible = DSU(universe)
        # Lexicographic deterministic spanning forest; each accepted cluster has
        # a real path through selected edges and contains <= cap original records.
        for a, b in sorted(edges):
            if a in component and b in component and component[a] == component[b]:
                feasible.union(a, b, cap)
        lower = _partition_metrics(feasible, component, universe)["true_positive_pairs"]
        if not (lower <= capped_upper <= reachable <= n_gold):
            raise AssertionError("invalid graph-bound ordering")
        return {"edges": len(edges), "connected_components":
                len({graph.find(x) for x in universe}),
                "unconstrained_reachable_positive_pairs": reachable,
                "unconstrained_recall_upper_bound": reachable / n_gold if n_gold else 0,
                "cap_aware_positive_pair_upper_bound": capped_upper,
                "cap_aware_recall_upper_bound": capped_upper / n_gold if n_gold else 0,
                "feasible_graph_greedy_positive_pairs": lower,
                "feasible_graph_greedy_recall": lower / n_gold if n_gold else 0,
                "greedy_equals_upper_bound": lower == capped_upper}

    return {"record_count": len(universe), "candidate_calls": n_calls,
            "scored_pairs_with_repeats": n_scored,
            "gold_positive_pairs": n_gold,
            "direct_positive_pairs": direct_positive,
            "direct_positive_pair_coverage": direct_positive / n_gold if n_gold else 0,
            "phases": {phase: {"candidate_calls": phase_calls[phase],
                               "scored_pairs_with_repeats": phase_instances[phase],
                               "unique_direct_positive_pairs": len(phase_positive[phase])}
                       for phase in sorted(phase_calls)},
            "deadline_new_direct_positive_pairs": len(phase_positive.get("deadline", set()) -
                                                       phase_positive.get("arrival", set())),
            "cluster_cap": cap,
            "representative_graph": graph_bounds(direct),
            "membership_expanded_graph": graph_bounds(expanded),
            "interpretation": {
                "direct": "Unique query--representative pairs actually scored by this policy.",
                "reachable": "Transitive graph upper bound, ignoring online timing and cluster cap.",
                "capped_upper": "Connectivity-relaxed combinatorial bound; not an exact oracle.",
                "feasible": "Gold-aware static graph construction; not an online-policy guarantee.",
                "scope": "All edges conditional on this policy's realized candidate snapshots."}}


def frozen_candidate_comparison(snapshots, component, record_ids, threshold,
                                cutoff_days=10, cap=20):
    """Post-process ONE frozen arrival-candidate trace under two edge selectors.

    This deliberately does not emulate deferral or claim a common-candidate
    end-to-end comparison. The index and candidate scores never respond to the
    new partition. Labels are used only for reporting, not edge selection.
    """
    universe = set(map(str, record_ids))
    arms = [{"name": name, "age_cut": age_cut, "dsu": DSU(universe), "accepted": 0}
            for name, age_cut in (("threshold", None), ("threshold_and_age_cutoff", cutoff_days))]
    n_rows = 0
    for row in snapshots:
        if row["phase"] != "arrival":
            continue
        a = str(row["query_id"])
        # Match the graph analysis' evaluated universe: an out-of-window query
        # must not become a bridge or consume a member slot in a capped cluster.
        if a not in universe:
            continue
        n_rows += 1
        for arm in arms:
            dsu, age_cut = arm["dsu"], arm["age_cut"]
            candidates = []
            for candidate in row["candidates"]:
                b = str(candidate["record_id"])
                if b not in universe or a == b:
                    continue
                if age_cut is not None:
                    if "age_days" not in candidate:
                        raise ValueError("age_days required for frozen age-cutoff comparison")
                    if candidate["age_days"] > age_cut:
                        continue
                if candidate["score"] >= threshold:
                    candidates.append(candidate)
            candidates.sort(key=lambda c: (-c["score"], str(c["record_id"]), str(c["issue_id"])))
            if candidates:
                arm["accepted"] += dsu.union(a, str(candidates[0]["record_id"]), cap)
    output = [{"selector": arm["name"], "threshold": threshold,
               "cutoff_days": arm["age_cut"], "accepted_edge_unions": arm["accepted"],
               **_partition_metrics(arm["dsu"], component, universe)} for arm in arms]
    return {"design": "Conditional static post-processing of one frozen candidate timeline",
            "candidate_trace_rows": n_rows, "cluster_cap": cap, "arms": output,
            "limitation": "No retrieval feedback, pending-state or waiting counterfactual; not an end-to-end policy result."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, nargs="?", help="PRIVATE JSON with snapshots, component, record_ids")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cap", type=int, default=20)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--run-dir", type=Path, help="Analyze completed traced e37 runs from PRIVATE JSONL files")
    parser.add_argument("--run-id", action="append", help="Restrict e37 analysis to these completed run IDs")
    args = parser.parse_args()
    if args.run_dir is not None:
        jobs = []
        for file in sorted((args.run_dir / "results").glob("*.json")):
            record = json.loads(file.read_text(encoding="utf-8"))
            job = record.get("job", {})
            if not job.get("trace"):
                continue
            if args.run_id and job["run_id"] not in args.run_id:
                continue
            jobs.append(job)
        if not jobs:
            parser.error("no completed traced runs found")
        window, data = None, None
        summaries = []
        for job in jobs:
            if job["window"] != window:
                with (args.run_dir / "private" / f"data_{job['window']}.pkl").open("rb") as handle:
                    data = pickle.load(handle)
                window = job["window"]
            trace = args.run_dir / "private" / f"{job['run_id']}_candidates.jsonl"
            record_ids = [r.rid for r in data["records"]]
            graph = candidate_graph_report(candidate_trace_rows(trace), data["gold"].component,
                                           record_ids, cap=args.cap)
            frozen = frozen_candidate_comparison(candidate_trace_rows(trace), data["gold"].component,
                                                 record_ids, job["tau"], cap=args.cap)
            digest = hashlib.sha256()
            with trace.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            summary = {"job": job, "trace_sha256": digest.hexdigest(),
                       "candidate_graph": graph, "frozen_candidate_comparison": frozen}
            destination = args.run_dir / "reports" / f"candidate_{job['run_id']}.json"
            destination.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            summaries.append(summary)
            print(json.dumps({"run_id": job["run_id"], "direct_coverage": graph["direct_positive_pair_coverage"],
                              "graph_upper": graph["representative_graph"]["unconstrained_recall_upper_bound"],
                              "cap_upper": graph["representative_graph"]["cap_aware_recall_upper_bound"],
                              "cap_feasible_lower": graph["representative_graph"]["feasible_graph_greedy_recall"]}), flush=True)
        output = args.out or args.run_dir / "reports" / "candidate_analysis.json"
        output.write_text(json.dumps({"scope": "Completed corrected-protocol traced runs", "runs": summaries},
                                     indent=2) + "\n", encoding="utf-8")
        raise SystemExit(0)
    if args.input is None or args.out is None:
        parser.error("provide input and --out, or --run-dir")
    source = json.loads(args.input.read_text(encoding="utf-8"))
    result = candidate_graph_report(source["snapshots"], source["component"],
                                    source["record_ids"], args.cap)
    if args.threshold is not None:
        result["frozen_candidate_comparison"] = frozen_candidate_comparison(
            source["snapshots"], source["component"], source["record_ids"], args.threshold,
            cap=args.cap)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
