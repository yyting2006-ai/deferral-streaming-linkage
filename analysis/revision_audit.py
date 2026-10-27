"""Real-stream, complete-emission prefix probes for the corrected replay engine.

The normal scorer sees no future records. The separately labelled positive
control uses a deliberately inadmissible future-context feature DURING scoring,
then replays the actual engine. It never edits a produced output log. Reports
contain aggregate counts; full logs and record identities stay in private_dir.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import argparse
import pickle
import os
import subprocess
import sys

import numpy as np

from audit.compare_prefix import absolute_time, compare


def sha256_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def stream_fingerprint(records):
    return sha256_json([r.visible_dict() for r in records])


def rotate_suffix(records, cutoff, shift=None):
    """Rotate future record contents/IDs among future timestamps; preserve prefix.

    No record with arrival <= cutoff is changed. The future record multiset is
    retained, while which future object arrives at each time changes. Sorting is
    by exact arrival then stable ID, matching the replay tie rule.
    """
    prefix = [deepcopy(r) for r in records if r.when <= cutoff]
    suffix = [deepcopy(r) for r in records if r.when > cutoff]
    if len(suffix) < 2:
        raise ValueError("a suffix perturbation requires at least two future records")
    times = [r.when for r in suffix]
    shift = max(1, len(suffix) // 3) if shift is None else shift % len(suffix)
    if shift == 0:
        raise ValueError("rotation must change suffix positions")
    rotated = suffix[shift:] + suffix[:shift]
    for record, time in zip(rotated, times):
        record.when = time
    return sorted(prefix + rotated, key=lambda r: (r.when, r.rid))


class FutureContextScorer:
    """Deliberately leaky scorer used only as a positive-control fault injection.

    The full stream's time-weighted mean description length, including records
    that have not arrived, adjusts the weight of the text-similarity feature.
    This affects real candidate scores, rankings and threshold decisions. It is
    an input-boundary fault control, not evidence of a natural leakage rate.
    The underlying fitted model is unchanged and is still called on every batch.
    """
    def __init__(self, scorer, records):
        self.scorer = scorer
        start = min(r.when for r in records)
        weights = np.asarray([(r.when - start).total_seconds() + 1 for r in records])
        lengths = np.asarray([len(r.description.split()) for r in records], dtype=float)
        self.future_statistics = {
            "time_weighted_mean_description_words": float(np.dot(weights, lengths) / weights.sum()),
            "records_used": len(records),
            "weight_definition": "1 + seconds since first stream arrival"}
        self.future_text_weight = 0.2 * np.tanh(
            self.future_statistics["time_weighted_mean_description_words"] / 10.0)

    def __call__(self, X):
        values = np.asarray(self.scorer(X), dtype=float).reshape(-1)
        # Feature 7 is cosine text similarity in the documented 21-feature model.
        # This is calculated BEFORE the engine makes a decision; no log mutation.
        features = np.asarray(X, dtype=float)
        return np.clip(values + self.future_text_weight * (features[:, 7] - 0.5), 0, 1)


def run_replay(records, engine_factory, context_records, fingerprint, seed):
    """Reset common randomness and instantiate all mutable state for EACH replay."""
    random.seed(seed)
    np.random.seed(seed)
    engine = engine_factory(context_records)
    for record in records:
        engine.on_record(deepcopy(record))
    engine.finish()
    envelope = engine.envelope(common_randomness=fingerprint)
    if "events" not in envelope:
        raise ValueError("engine.envelope() must return a complete emission log")
    if hasattr(engine.scorer, "future_statistics"):
        envelope["deliberate_future_input"] = engine.scorer.future_statistics
    return envelope


def decision_prefix_difference(a, b, cutoff):
    """Report whether emitted decisions change even after ignoring score payloads."""
    def core(run):
        return [(absolute_time(e["emitted_at"]), e["object_id"], e["action"], e["target_id"])
                for e in run["events"] if absolute_time(e["emitted_at"]) <= cutoff]
    left, right = core(a), core(b)
    return {"equal_time_object_action_target": left == right,
            "different_aligned_rows": sum(x != y for x, y in zip(left, right)),
            "length_difference": len(right) - len(left)}


def probe_prefixes(records, normal_factory, leaky_factory, immutable_state,
                   private_dir, cutoffs=None, seed=13):
    """Perform three or more full-log comparisons on a real supplied stream.

    immutable_state must identify actual frozen model/config/data hashes. Factory
    calls must return fresh empty-state engines; normal_factory MUST ignore its
    future-context argument. Caller owns timestamp normalization and records that
    assumption in immutable_state. Replays share fitted parameters, initial state,
    configuration and Python/NumPy RNG states.
    """
    if len(records) < 4:
        raise ValueError("too few records for real-stream prefix probes")
    records = sorted(records, key=lambda r: (r.when, r.rid))
    if any(r.when.tzinfo is None for r in records):
        raise ValueError("real-stream probes require timezone-aware arrival timestamps")
    if len({r.rid for r in records}) != len(records):
        raise ValueError("record IDs must be unique in the replay universe")
    if cutoffs is None:
        cutoffs = sorted({records[int((len(records) - 1) * p)].when
                          for p in (0.25, 0.50, 0.75)})
    if len(cutoffs) < 3:
        raise ValueError("at least three distinct absolute-time cutoffs are required")
    private_dir = Path(private_dir)
    private_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256_json({"immutable_state": immutable_state,
                               "python_rng": random.Random(seed).getstate(),
                               "numpy_seed": seed, "initial_state": "empty"})
    baseline = run_replay(records, normal_factory, records, fingerprint, seed)
    leaky_baseline = run_replay(records, leaky_factory, records, fingerprint, seed)
    (private_dir / "baseline_events.json").write_text(json.dumps(baseline) + "\n", encoding="utf-8")
    (private_dir / "leaky_baseline_events.json").write_text(json.dumps(leaky_baseline) + "\n", encoding="utf-8")
    probes = []
    for i, cutoff in enumerate(cutoffs):
        altered = rotate_suffix(records, cutoff)
        unchanged = [r.visible_dict() for r in records if r.when <= cutoff]
        altered_prefix = [r.visible_dict() for r in altered if r.when <= cutoff]
        if unchanged != altered_prefix:
            raise AssertionError("suffix perturbation changed the preserved input prefix")
        normal = run_replay(altered, normal_factory, altered, fingerprint, seed)
        leaky = run_replay(altered, leaky_factory, altered, fingerprint, seed)
        (private_dir / f"normal_rotated_{i}.json").write_text(json.dumps(normal) + "\n", encoding="utf-8")
        (private_dir / f"leaky_rotated_{i}.json").write_text(json.dumps(leaky) + "\n", encoding="utf-8")
        probes.append({"cutoff_utc": cutoff.astimezone(timezone.utc).isoformat(),
                       "input_prefix_records": len(unchanged),
                       "normal": compare(baseline, normal, cutoff.isoformat()),
                       "leaky_positive_control": compare(leaky_baseline, leaky, cutoff.isoformat()),
                       "leaky_decision_difference": decision_prefix_difference(leaky_baseline, leaky, cutoff),
                       "leaky_future_statistics": leaky.get("deliberate_future_input"),
                       "altered_stream_sha256": stream_fingerprint(altered)})
    return {"design": "Complete independently time-filtered emission-prefix suffix-rotation probe",
            "record_count": len(records), "cutoff_count": len(probes),
            "normal_pass_count": sum(p["normal"]["equal"] is True for p in probes),
            "leaky_detected_count": sum(p["leaky_positive_control"]["equal"] is False for p in probes),
            "baseline_emissions": len(baseline["events"]),
            "leaky_baseline_emissions": len(leaky_baseline["events"]),
            "common_state_sha256": fingerprint,
            "input_stream_sha256": stream_fingerprint(records),
            "immutable_state": immutable_state,
            "leaky_baseline_future_statistics": leaky_baseline.get("deliberate_future_input"),
            "positive_control": "Full-stream time-weighted mean description length adjusts text-similarity weight during actual scorer calls; complete engine replay, no output-log edits.",
            "interpretation": "Three nested temporal probes are not independent decisions and do not constitute a universal proof.",
            "probes": probes}


def main():
    """CLI for the private cache produced by experiments/e37_revision_suite.py."""
    from psid.causal import CausalClusterer
    from psid.methods import StreamConfig
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--tau", type=float, help="Otherwise read the frozen March calibration selection")
    parser.add_argument("--policy", default="gate5", choices=["arrival", "band", "gate5"])
    parser.add_argument("--mode", default="probe", choices=["probe", "determinism", "hash-replay"])
    parser.add_argument("--hash-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    run = args.run_dir
    protocol = json.loads((run / "experiment_protocol.json").read_text(encoding="utf-8"))
    tau = args.tau
    if tau is None:
        selection = json.loads((run / "reports" / "calibration_selection.json").read_text(encoding="utf-8"))
        tau = selection["families"]["gbt"]["tau"]
    data_path = run / "private" / "data_audit.pkl"
    model_path = run / "private" / "model_gbt_13.pkl"
    with data_path.open("rb") as handle:
        data = pickle.load(handle)
    with model_path.open("rb") as handle:
        model = pickle.load(handle)
    policy = protocol["policies"][args.policy]
    cfg = StreamConfig(t_merge=tau, t_pending=0, window_days=60, k_candidates=60, max_cluster=20)
    def normal_factory(context):
        return CausalClusterer(cfg, model, **policy)
    def leaky_factory(context):
        return CausalClusterer(cfg, FutureContextScorer(model, context), **policy)
    def file_sha(path):
        h = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    state = {"model_sha256": file_sha(model_path),
             "data_pickle_sha256": file_sha(data_path),
             "protocol_sha256": file_sha(run / "experiment_protocol.json"),
             "engine_sha256": file_sha(Path(__file__).resolve().parents[1] / "psid" / "causal.py"),
             "probe_sha256": file_sha(__file__),
             "timezone": protocol["timezone"], "seed": 13,
             "config": vars(cfg), "policy": policy, "policy_name": args.policy}
    if args.mode == "hash-replay":
        if args.hash_output is None:
            parser.error("hash-replay requires --hash-output")
        events = run_replay(data["records"], normal_factory, data["records"],
                            sha256_json(state), 13)
        summary = {"pythonhashseed": os.environ.get("PYTHONHASHSEED"),
                   "record_count": len(data["records"]), "emission_count": len(events["events"]),
                   "event_sha256": sha256_json(events["events"]),
                   "immutable_state": state}
        args.hash_output.parent.mkdir(parents=True, exist_ok=True)
        args.hash_output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: summary[k] for k in ("pythonhashseed", "record_count", "emission_count", "event_sha256")}))
        return
    if args.mode == "determinism":
        runs = []
        root = Path(__file__).resolve().parents[1]
        for hashseed in (0, 1):
            env = dict(os.environ, PYTHONHASHSEED=str(hashseed), OMP_NUM_THREADS="1",
                       OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
            output = run / "private" / "prefix_audit" / f"hashseed_{hashseed}.json"
            command = [sys.executable, "-m", "analysis.revision_audit", "--mode", "hash-replay",
                       "--run-dir", str(run), "--policy", args.policy, "--tau", str(tau),
                       "--hash-output", str(output)]
            print(f"Replaying independent process with PYTHONHASHSEED={hashseed}", flush=True)
            completed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            print(completed.stdout.strip(), flush=True)
            runs.append(json.loads(output.read_text(encoding="utf-8")))
        report = {"design": "Same immutable model/config/data in two independent Python processes",
                  "pythonhashseeds": [0, 1], "policy_name": args.policy,
                  "identical_complete_event_log": runs[0]["event_sha256"] == runs[1]["event_sha256"],
                  "compared_fields": "entire ordered event JSON including timestamps, action, target, score and payload",
                  "record_count": runs[0]["record_count"], "runs": runs,
                  "scope": "One fixed model and real weekly stream; not a cross-platform numerical equivalence claim."}
        output = run / "reports" / "hashseed_replay.json"
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Complete-event identity: {report['identical_complete_event_log']}; {output}")
        return
    print(f"Starting full-prefix audit: {len(data['records'])} records, policy={args.policy}, tau={tau}", flush=True)
    report = probe_prefixes(data["records"], normal_factory, leaky_factory, state,
                            run / "private" / "prefix_audit")
    output = run / "reports" / "prefix_audit.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("record_count", "cutoff_count", "normal_pass_count", "leaky_detected_count")}))
    print(f"Aggregate report: {output}")


if __name__ == "__main__":
    main()
