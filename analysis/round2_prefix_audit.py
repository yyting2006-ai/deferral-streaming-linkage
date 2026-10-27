"""Targeted post-deadline real-stream prefix audit; does not modify the engine.

Freeze the protocol before replay, then execute it using the already fitted GBT
seed 13 scorer and May cache from e37_revision_suite. Only aggregate reports are
public. Full output and lightweight scheduling logs stay in output_dir/private.
"""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import platform
import random
import struct
import sys
import time

import numpy as np

from analysis.revision_audit import rotate_suffix, sha256_json, stream_fingerprint
from audit.compare_prefix import absolute_time, compare
from psid.causal import CausalClusterer
from psid.methods import StreamConfig


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")


def prefix_coverage(events, cutoff):
    """Count independently time-filtered output events by actual engine trigger."""
    rows = [e for e in events if absolute_time(e["emitted_at"]) <= cutoff]
    counts = Counter(e["trigger"] for e in rows)
    if set(counts) - {"arrival", "claim", "deadline"}:
        raise ValueError("unrecognized finalization trigger")
    deadlines = [e for e in rows if e["trigger"] == "deadline"]
    return {
        "output_count": len(rows),
        "trigger_counts": {k: counts[k] for k in ("arrival", "claim", "deadline")},
        "deadline_events_at_exact_due": sum(
            e["payload"].get("due_at") is not None and
            absolute_time(e["payload"]["due_at"]) == absolute_time(e["emitted_at"])
            for e in deadlines),
        "deadline_wait_days": sorted({e["payload"]["latency_days"] for e in deadlines}),
        "first_deadline_utc": min((e["emitted_at"] for e in deadlines), default=None),
        "last_deadline_utc": max((e["emitted_at"] for e in deadlines), default=None),
        "complete_prefix_sha256": sha256_json(rows),
    }


def scheduling_coverage(rows, cutoff):
    """Validate real scoring order, including ties with pending-only arrivals.

    Finalization logs alone can miss an arrival that becomes PENDING. Therefore
    this check uses the phase at every actual _best call, without candidate data.
    """
    rows = [r for r in rows if absolute_time(r["at"]) <= cutoff]
    by_time = defaultdict(list)
    for row in rows:
        by_time[row["at"]].append(row)
    ties = [group for group in by_time.values()
            if {r["phase"] for r in group} == {"arrival", "deadline"}]
    violations = 0
    for group in ties:
        saw_arrival = False
        for row in group:
            if row["phase"] == "arrival":
                saw_arrival = True
            elif saw_arrival:
                violations += 1
    deadlines = [r for r in rows if r["phase"] == "deadline"]
    return {
        "scoring_calls": len(rows),
        "arrival_calls": sum(r["phase"] == "arrival" for r in rows),
        "deadline_rescoring_calls": len(deadlines),
        "deadline_calls_during_finish": sum(r["finishing"] for r in deadlines),
        "equal_time_deadline_arrival_timestamps": len(ties),
        "equal_time_deadline_calls": sum(r["phase"] == "deadline" for g in ties for r in g),
        "timer_first_violations": violations,
        "deadline_calls_with_equal_or_future_arrival_visible": sum(
            r["last_arrival_at"] is not None and
            absolute_time(r["last_arrival_at"]) >= absolute_time(r["at"])
            for r in deadlines),
    }


class SchedulingWitnessClusterer(CausalClusterer):
    """Observe scorer entry; inherit every engine decision and transition."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scheduling_witness = []

    def _best(self, rec, now, phase, exclude=None):
        self.scheduling_witness.append({
            "at": now.isoformat(), "phase": phase, "record_id": rec.rid,
            "finishing": self._finishing,
            "last_arrival_at": (None if self._last_arrival_key is None
                                else self._last_arrival_key[0].isoformat()),
        })
        return super()._best(rec, now, phase, exclude)


def replay(records, model, config, policy, common, private_dir, label):
    random.seed(13)
    np.random.seed(13)
    engine = SchedulingWitnessClusterer(StreamConfig(**config), model, **policy)
    wall, cpu = time.perf_counter(), time.process_time()
    for record in records:
        engine.on_record(deepcopy(record))
    before_drain_count = len(engine.events)
    engine.finish()
    runtime = {"wall_seconds": time.perf_counter() - wall,
               "process_cpu_seconds": time.process_time() - cpu,
               "includes_monthend_drain": True, "candidate_snapshot_writes": False,
               "includes_final_event_and_witness_file_serialization": False}
    envelope = engine.envelope(common_randomness=common)
    save_json(private_dir / f"{label}_events.json", envelope)
    save_json(private_dir / f"{label}_scheduling.json", engine.scheduling_witness)
    save_json(private_dir / f"{label}_summary.json", {
        "runtime": runtime, "summary": engine.summary(),
        "emissions_before_drain": before_drain_count})
    return envelope, engine.scheduling_witness, runtime, engine.summary()


def freeze(source_run, output_dir):
    source_run, output_dir = Path(source_run), Path(output_dir)
    protocol_path = output_dir / "prefix_audit_protocol.json"
    if protocol_path.exists():
        raise FileExistsError("protocol already frozen; use --run, do not overwrite")
    prior = json.loads((source_run / "experiment_protocol.json").read_text(encoding="utf-8"))
    selection = json.loads((source_run / "reports/calibration_selection.json").read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    inputs = {"data_May.pkl": source_run / "private/data_May.pkl",
              "model_gbt_13.pkl": source_run / "private/model_gbt_13.pkl",
              "experiment_protocol.json": source_run / "experiment_protocol.json",
              "calibration_selection.json": source_run / "reports/calibration_selection.json",
              "prior_prefix_audit.json": source_run / "reports/prefix_audit.json",
              "prior_May_gate5_events.pkl": source_run / "private/May_gbt_s13_gate5_events.pkl"}
    sources = ["psid/causal.py", "psid/methods.py", "psid/schema.py",
               "analysis/revision_audit.py", "audit/compare_prefix.py",
               "analysis/round2_prefix_audit.py"]
    protocol = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_item": "Round 2 N01", "scope": "targeted coverage extension; no model refit",
        "window_local": ["2024-05-01", "2024-05-31"],
        "cutoff_utc": "2024-05-20T07:00:00+00:00",
        "cutoff_local": "2024-05-20T00:00:00-07:00",
        "cutoff_selection": "Fixed calendar date before the supplemental replay; no outcome-adaptive selection",
        "timezone": prior["timezone"], "seed": 13, "initial_state": "empty",
        "model": "existing corrected-protocol GBT seed13",
        "config": vars(StreamConfig(t_merge=selection["families"]["gbt"]["tau"],
                    t_pending=0, window_days=60, k_candidates=60, max_cluster=20)),
        "policy": prior["policies"]["gate5"], "policy_name": "gate5",
        "perturbation": "Rotate all strictly later records across suffix arrival timestamps by max(1, floor(suffix_count/3)); preserve complete input prefix and suffix multiset",
        "comparisons": "independently filter both complete emission logs at the same inclusive UTC cutoff; compare length, order, all fields and payloads",
        "coverage_requirements": {"min_stream_span_days": 14, "min_suffix_records": 2,
            "min_deadline_rescoring_calls_before_cutoff": 1,
            "min_equal_time_deadline_arrival_timestamps": 1,
            "deadline_calls_during_finish_before_cutoff": 0,
            "timer_first_violations": 0},
        "runtime": {"device": "CPU", "workers": 1, "threads": 1,
                    "candidate_snapshot_writes": False, "monthend_drain": True},
        "inputs_sha256": {name: file_sha(path) for name, path in inputs.items()},
        "sources_sha256": {name: file_sha(root / name) for name in sources},
        "interpretation": "One targeted post-deadline real-stream probe; prior three probes and future-input positive controls remain unchanged; not a universal nonanticipation proof",
    }
    save_json(protocol_path, protocol)
    print(f"Frozen protocol: {protocol_path}", flush=True)


def execute(source_run, output_dir):
    source_run, output_dir = Path(source_run), Path(output_dir)
    protocol_path = output_dir / "prefix_audit_protocol.json"
    p = json.loads(protocol_path.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    inputs = {"data_May.pkl": source_run / "private/data_May.pkl",
              "model_gbt_13.pkl": source_run / "private/model_gbt_13.pkl",
              "experiment_protocol.json": source_run / "experiment_protocol.json",
              "calibration_selection.json": source_run / "reports/calibration_selection.json",
              "prior_prefix_audit.json": source_run / "reports/prefix_audit.json",
              "prior_May_gate5_events.pkl": source_run / "private/May_gbt_s13_gate5_events.pkl"}
    for name, path in inputs.items():
        if file_sha(path) != p["inputs_sha256"][name]:
            raise RuntimeError(f"frozen input changed: {name}")
    for name, digest in p["sources_sha256"].items():
        if file_sha(root / name) != digest:
            raise RuntimeError(f"frozen source changed: {name}")
    if struct.calcsize("P") * 8 != 64:
        raise RuntimeError("64-bit Python required for the existing caches")
    with inputs["data_May.pkl"].open("rb") as handle:
        records = pickle.load(handle)["records"]
    records = sorted(records, key=lambda r: (r.when, r.rid))
    with inputs["model_gbt_13.pkl"].open("rb") as handle:
        model = pickle.load(handle)
    cutoff = absolute_time(p["cutoff_utc"])
    span = (records[-1].when - records[0].when).total_seconds() / 86400
    suffix_count = sum(r.when > cutoff for r in records)
    if span < 14 or suffix_count < 2:
        raise RuntimeError("coverage requires >=14-day stream and perturbable suffix")
    altered = rotate_suffix(records, cutoff)
    input_prefix = [r.visible_dict() for r in records if r.when <= cutoff]
    altered_prefix = [r.visible_dict() for r in altered if r.when <= cutoff]
    if input_prefix != altered_prefix or stream_fingerprint(records) == stream_fingerprint(altered):
        raise RuntimeError("suffix mutation must change the stream and preserve the full prefix")
    witness_features = np.random.default_rng(13).normal(size=(2, 21))
    witness = np.asarray(model(witness_features), dtype=float).reshape(-1)
    if witness.shape != (2,) or not np.all(np.isfinite(witness)):
        raise RuntimeError("existing fitted scorer failed seeded inference witness")
    private = output_dir / "private/post_deadline_prefix_audit"
    private.mkdir(parents=True, exist_ok=True)
    common = sha256_json({"protocol_sha256": file_sha(protocol_path),
                         "rng_seed": 13, "initial_state": "empty"})
    print(f"Replaying baseline: {len(records)} May records, cutoff {p['cutoff_utc']}, suffix {suffix_count}", flush=True)
    baseline, base_schedule, base_runtime, base_summary = replay(
        records, model, p["config"], p["policy"], common, private, "baseline")
    print("Replaying suffix rotation with identical fitted scorer and empty initial state", flush=True)
    mutated, mutated_schedule, mutated_runtime, mutated_summary = replay(
        altered, model, p["config"], p["policy"], common, private, "rotated")
    comparison = compare(baseline, mutated, p["cutoff_utc"])
    coverage = prefix_coverage(baseline["events"], cutoff)
    other_coverage = prefix_coverage(mutated["events"], cutoff)
    schedule = scheduling_coverage(base_schedule, cutoff)
    other_schedule = scheduling_coverage(mutated_schedule, cutoff)
    with inputs["prior_May_gate5_events.pkl"].open("rb") as handle:
        historical = pickle.load(handle)["events"]
    same_as_previous = baseline["events"] == historical
    requirements = {
        "complete_prefix_equal": comparison["valid_comparison"] and comparison["equal"],
        "actual_deadline_rescoring_in_prefix": schedule["deadline_rescoring_calls"] > 0,
        "deadline_events_match_rescoring_calls": coverage["trigger_counts"]["deadline"] == schedule["deadline_rescoring_calls"],
        "deadline_events_at_exact_seven_day_due": coverage["deadline_events_at_exact_due"] == coverage["trigger_counts"]["deadline"] and coverage["deadline_wait_days"] == [7.0],
        "prefix_deadlines_precede_monthend_drain": schedule["deadline_calls_during_finish"] == 0,
        "actual_equal_time_timer_arrival_coverage": schedule["equal_time_deadline_arrival_timestamps"] > 0,
        "timer_first_order_preserved": schedule["timer_first_violations"] == 0 and other_schedule["timer_first_violations"] == 0,
        "deadline_scorer_has_no_equal_or_future_arrival": schedule["deadline_calls_with_equal_or_future_arrival_visible"] == 0 and other_schedule["deadline_calls_with_equal_or_future_arrival_visible"] == 0,
        "prefix_trigger_and_schedule_counts_equal": coverage == other_coverage and schedule == other_schedule,
        "instrumented_baseline_matches_previous_complete_event_log": same_as_previous,
        "original_audit_unchanged": file_sha(inputs["prior_prefix_audit.json"]) == p["inputs_sha256"]["prior_prefix_audit.json"],
        "both_replays_complete_and_unique": all(s["complete"] and s["unique_finalizations"] for s in (base_summary, mutated_summary)),
    }
    import scipy, sklearn
    report = {
        "review_item": "Round 2 N01", "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": file_sha(protocol_path), "protocol": p,
        "record_count": len(records), "stream_span_days": span,
        "first_arrival_utc": records[0].when.isoformat(), "last_arrival_utc": records[-1].when.isoformat(),
        "cutoff_utc": p["cutoff_utc"], "input_prefix_records": len(input_prefix),
        "perturbable_suffix_records": suffix_count,
        "input_prefix_sha256": sha256_json(input_prefix),
        "input_stream_sha256": stream_fingerprint(records),
        "altered_stream_sha256": stream_fingerprint(altered),
        "common_state_sha256": common, "comparison": comparison,
        "baseline_prefix": coverage, "rotated_prefix": other_coverage,
        "baseline_scheduling_prefix": schedule, "rotated_scheduling_prefix": other_schedule,
        "baseline_complete_output_count": len(baseline["events"]),
        "rotated_complete_output_count": len(mutated["events"]),
        "requirements": requirements, "pass": all(requirements.values()),
        "runtime": {"baseline": base_runtime, "rotated": mutated_runtime,
                    "measurement_note": "One local worker, one library thread; includes lightweight scheduling witness and drain. Not a production throughput benchmark."},
        "environment": {"python": sys.version, "pointer_bits": struct.calcsize('P') * 8,
                        "platform": platform.platform(), "numpy": np.__version__,
                        "scipy": scipy.__version__, "sklearn": sklearn.__version__,
                        "seeded_scorer_witness_sha256": sha256_json(witness.tolist())},
        "private_evidence_sha256": {f.name: file_sha(f) for f in sorted(private.glob("*.json"))},
        "claim_count_definition": "claim counts the pending target's NEW finalization; the claimant has its own arrival or deadline trigger",
        "interpretation": p["interpretation"],
    }
    output = output_dir / "reports/post_deadline_prefix_audit.json"
    save_json(output, report)
    print(json.dumps({"pass": report["pass"], "prefix": coverage,
                      "scheduling": schedule, "report": str(output)}, indent=2), flush=True)
    if not report["pass"]:
        raise RuntimeError("targeted post-deadline audit failed one or more frozen requirements")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--freeze", action="store_true")
    modes.add_argument("--run", action="store_true")
    args = parser.parse_args()
    (freeze if args.freeze else execute)(args.source_run, args.output_dir)


if __name__ == "__main__":
    main()
