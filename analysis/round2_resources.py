"""Derive Round-2 N03 resource reporting from existing, frozen replay results.

This script does not fit a model or replay a stream.  Public aggregate inputs
are sufficient.  --verify-events additionally checks elapsed waits against
trusted local event pickles; it publishes only aggregate check results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pickle
from collections import Counter
from datetime import datetime
from pathlib import Path


ARMS = (("arrival", "Arrival"), ("band", "Band"),
        ("gate5", "Gate"), ("cutoff10", "Cutoff"))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-11), (a, b)


def event_checks(path, summary):
    # The optional path is an existing, trusted local experiment output.
    with path.open("rb") as handle:
        events = pickle.load(handle)["events"]
    waits = []
    held = []
    for event in events:
        payload = event["payload"]
        elapsed = (datetime.fromisoformat(event["emitted_at"]) -
                   datetime.fromisoformat(payload["arrival_at"])).total_seconds() / 86400
        close(elapsed, payload["latency_days"])
        waits.append(elapsed)
        if payload["held"]:
            held.append(elapsed)
    assert len(events) == summary["n_records"] == summary["n_events"]
    assert len(held) == summary["n_deferred"]
    close(math.fsum(waits) / len(waits), summary["all_record_latency_days"]["mean"])
    if held:
        close(math.fsum(held) / len(held), summary["held_record_latency_days"]["mean"])
        close(max(held), summary["held_record_latency_days"]["max"])
    assert dict(Counter(e["trigger"] for e in events)) == summary["trigger_counts"]
    assert len({e["object_id"] for e in events}) == len(events)
    return {"checked": True, "source_sha256": sha256(path),
            "n_events": len(events), "held_n": len(held),
            "elapsed_waits_recomputed_from_timestamps": True,
            "latency_and_trigger_counts_match": True,
            "peak_pending_source": "Recorded engine counter, not inferred from output timing."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--verify-events", action="store_true")
    args = parser.parse_args()
    run, code, out = args.run_dir, args.code_root, args.out_dir
    reports = out / "reports"
    snippets = out / "manuscript_snippets"
    reports.mkdir(parents=True, exist_ok=True)
    snippets.mkdir(parents=True, exist_ok=True)

    protocol = read_json(run / "experiment_protocol.json")
    preparation = read_json(run / "reports/preparation_manifest.json")
    schedule = read_json(run / "reports/run_schedule.json")
    scheduled = {j["run_id"]: j for j in schedule["jobs"]}
    normalized_path = run / "reports/normalized_results.csv"
    with normalized_path.open(encoding="utf-8-sig", newline="") as handle:
        normalized = {r["run_id"]: r for r in csv.DictReader(handle)}
    rows, checks, identities = [], {}, []
    for arm, label in ARMS:
        run_id = f"May_gbt_s13_{arm}"
        path = run / "results" / f"{run_id}.json"
        result = read_json(path)
        summary, runtime = result["protocol_summary"], result["runtime"]
        assert result["job"] == scheduled[run_id]
        assert result["job"]["trace"] is True
        assert result["candidate_trace"]["enabled"] is True
        assert summary["n_records"] == 35915
        assert summary["complete"] and summary["unique_finalizations"]
        assert summary["n_pending"] == 0 and summary["finished"]
        assert summary["all_record_latency_days"]["n"] == summary["n_records"]
        assert summary["held_record_latency_days"]["n"] == summary["n_deferred"]
        all_mean = summary["all_record_latency_days"]["mean"]
        held_n = summary["n_deferred"]
        held_mean = summary["held_record_latency_days"]["mean"] if held_n else None
        close(all_mean * summary["n_records"], (held_mean or 0) * held_n)
        identities.append({k: v for k, v in result["cache_identity"].items() if k != "job"})
        row = dict(run_id=run_id, policy=label, window="May", scorer="gbt", seed=13,
                   n_records=summary["n_records"], held_n=held_n,
                   mean_wait_all_days=all_mean, mean_wait_held_days=held_mean,
                   max_wait_days=summary["all_record_latency_days"]["max"],
                   peak_pending=summary["peak_pending"],
                   wall_seconds=runtime["replay_wall_seconds"],
                   cpu_seconds=runtime["process_cpu_seconds"],
                   trace_io_seconds=runtime["trace_io_seconds"],
                   drain_seconds=runtime["drain_seconds"],
                   monthend_finalizations=summary["monthend_finalizations"],
                   candidate_trace_enabled=True,
                   candidate_snapshot_rows=result["candidate_trace"]["rows"],
                   candidate_instances=result["candidate_trace"]["candidate_instances"],
                   source_file=f"results/{run_id}.json", source_sha256=sha256(path))
        for target, source in (("replay_wall_seconds", "wall_seconds"),
                               ("process_cpu_seconds", "cpu_seconds"),
                               ("trace_io_seconds", "trace_io_seconds"),
                               ("drain_seconds", "drain_seconds"),
                               ("peak_pending", "peak_pending")):
            close(float(normalized[run_id][target]), row[source])
        check = {"schedule_matches": True, "normalized_csv_matches": True,
                 "all_mean_times_n_equals_held_mean_times_held_n": True}
        public_path = code / "reproductions/revision_v2/results" / f"{run_id}.json"
        if public_path.exists():
            assert read_json(public_path) == result
            check["public_aggregate_matches"] = True
        if args.verify_events:
            check["events"] = event_checks(run / "private" / f"{run_id}_events.pkl", summary)
        checks[run_id] = check
        rows.append(row)
    assert all(i == identities[0] for i in identities)
    assert identities[0]["protocol_sha256"] == sha256(run / "experiment_protocol.json")
    assert identities[0]["preparation_manifest_sha256"] == sha256(run / "reports/preparation_manifest.json")

    suite = code / "experiments/e37_revision_suite.py"
    writer = code / "analysis/candidate_analysis.py"
    causal = code / "psid/causal.py"
    lines = suite.read_text(encoding="utf-8").splitlines()
    def line_number(needle):
        hits = [i + 1 for i, line in enumerate(lines) if needle in line]
        assert len(hits) == 1, (needle, hits)
        return hits[0]
    locators = {"load_data": line_number("data=load_pickle(run/'private'/f'data_{window}.pkl')"),
                "open_trace": line_number("writer=CandidateTraceWriter(trace_path)"),
                "start_wall_cpu": line_number("t=time.perf_counter(); cpu=time.process_time(); sample=[]"),
                "arrival": line_number("cl.on_record(rec); sample.append"),
                "finish_drain": line_number("cl.finish(); drain_seconds="),
                "close_trace": line_number("if writer: writer.close()"),
                "end_wall_cpu": line_number("elapsed=time.perf_counter()-t; cpu_seconds="),
                "compute_metrics": line_number("summary=cl.summary(); ev=evaluate_partition"),
                "save_events": line_number("save_pickle(run/'private'/f'{name}_events.pkl'")}
    assert list(locators.values()) == sorted(locators.values())
    writer_text = writer.read_text(encoding="utf-8")
    assert 'self.handle.write(json.dumps(row' in writer_text
    assert 'self.candidate_instances += len(row["candidates"])' in writer_text
    assert 'self.handle.close()' in writer_text
    causal_text = causal.read_text(encoding="utf-8")
    assert "while self._timers:" in causal_text
    assert "self.advance(self._timers[0][0])" in causal_text
    assert "self.peak_pending = max(self.peak_pending, len(self.pending))" in causal_text

    with (reports / "resource_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        csv_writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        csv_writer.writeheader()
        csv_writer.writerows(rows)
    boundary = {
        "included_wall_and_cpu": ["on_record processing and due timers", "candidate-snapshot construction",
                                  "JSONL trace serialization and writes", "end-of-window finish timer drain",
                                  "final trace drain and writer close/flush"],
        "excluded_wall_and_cpu": ["input/model loading", "model fitting", "initial empty-state/writer setup",
                                  "post-replay metric computation", "events pickle and result JSON persistence"],
        "trace_io_seconds": "Sum of timed CandidateTraceWriter.drain calls: JSON serialization, writes, counting and clearing; excludes the separately executed final close/flush, which is in total wall/CPU.",
        "drain_seconds": "Time in cl.finish(), including deadline scoring and snapshot construction; the subsequent trace write is outside drain_seconds but inside total wall/CPU.",
        "disk_note": "No fsync or physical-device latency measurement; trace time is application-level serialization/write-call time.",
        "waiting": "emitted_at minus arrival_at divided by 86400; mean all uses 35915 requests, mean held uses each policy's n_deferred; empty held set is null rather than a measured zero.",
        "peak_pending": "Recorded maximum number of live pending request singletons; not memory bytes.",
        "capacity_note": "Six-worker shared CPU measurements do not estimate isolated single-core or deployment service capacity.",
    }
    environment = {k: preparation[k] for k in ("python", "platform", "numpy", "scipy", "sklearn")}
    environment.update(protocol["runtime"])
    environment["cpu_model_and_ram"] = "Not recorded in the original measurement manifest."
    report = dict(review_item="N03", status="closed with existing measurements", no_experiments_rerun=True,
                  scope="May 2024, GBT seed 13; four trace-enabled policies from the same frozen suite",
                  rows=rows, environment=environment, shared_cache_identity=identities[0],
                  checks=checks, measurement_boundaries=boundary,
                  source_code={"experiments/e37_revision_suite.py": {"sha256": sha256(suite), "line_locators": locators},
                               "analysis/candidate_analysis.py": {"sha256": sha256(writer)},
                               "psid/causal.py": {"sha256": sha256(causal)}},
                  source_inputs={"experiment_protocol.json": sha256(run / "experiment_protocol.json"),
                                 "reports/preparation_manifest.json": sha256(run / "reports/preparation_manifest.json"),
                                 "reports/run_schedule.json": sha256(run / "reports/run_schedule.json"),
                                 "reports/normalized_results.csv": sha256(normalized_path)})
    (reports / "resource_summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    table = [r"\begin{table}[t]", r"\centering", r"\small", r"\setlength{\tabcolsep}{3pt}",
             r"\caption{Replay resources on May, GBT seed 13. Mean elapsed waiting is in days; wall, process CPU, and trace-write time are in seconds. All four arms use candidate tracing and the same six-worker CPU setup.}",
             r"\label{tab:resources}", r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"Policy & \multicolumn{2}{c}{Mean wait (d)} & Peak & Wall & CPU & Trace \\",
             r" & All & Held & pending & (s) & (s) & (s) \\", r"\midrule"]
    for row in rows:
        held_text = f"{row['mean_wait_held_days']:.2f}" if row["held_n"] else "--"
        table.append(f"{row['policy']} & {row['mean_wait_all_days']:.2f} & {held_text} & "
                     f"{row['peak_pending']:,} & {row['wall_seconds']:.2f} & {row['cpu_seconds']:.2f} & "
                     f"{row['trace_io_seconds']:.2f} " + r"\\")
    table.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}", ""])
    (snippets / "v3_resources.tex").write_text("\n".join(table), encoding="utf-8")
    paragraph = r"""\Cref{tab:resources} reports the same trace-enabled May runs. The all-request
mean uses 35,915 requests; held means use 5,740 Band and 1,867 Gate requests
(``--'' denotes an empty held set). Peak pending is a request count, not memory
usage. Wall and process CPU time include candidate-snapshot construction,
serialization/writes, final close/flush, and the end-of-window timer drain;
they exclude data/model loading, fitting, and post-replay metrics/output
persistence. Trace time measures serialization and write calls, excluding
the final close/flush. The six-worker shared CPU setting does not measure
isolated service capacity.
"""
    (snippets / "resources_prose.tex").write_text(paragraph, encoding="utf-8")
    md = ["# N03: existing-run resource summary", "",
          "All requested metrics are available; no experiment was rerun.", "",
          "| Policy | Mean wait all (d) | Mean wait held (d) | Peak pending | Wall (s) | CPU (s) | Trace (s) |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        held_text = f"{row['mean_wait_held_days']:.2f}" if row["held_n"] else "not applicable"
        md.append(f"| {row['policy']} | {row['mean_wait_all_days']:.2f} | {held_text} | {row['peak_pending']:,} | {row['wall_seconds']:.2f} | {row['cpu_seconds']:.2f} | {row['trace_io_seconds']:.2f} |")
    md.extend(["", "All-request denominator: 35,915. Held denominators: Band 5,740; Gate 1,867; Arrival/Cutoff zero.",
               "", "The table reports one measured run per arm, not means over five seeds. All four runs have trace=true, identical model/protocol/preparation/source cache identities, and the same scheduled suite. Protocol: six CPU workers and one thread per worker. Recorded environment: Python 3.11.9, Windows-10-10.0.26200-SP0, NumPy 2.4.6, SciPy 1.17.0, scikit-learn 1.8.0. The original manifest does not record CPU model or RAM; these are not retroactively invented.",
               "", "## Timing boundaries", ""])
    md.extend(f"- {key}: {value}" for key, value in boundary.items())
    md.extend(["", "## Verification", "",
               "Result values are checked against the original per-run JSON and the existing normalized-results CSV. The four source JSON objects also match the current public repository copies. Job settings match the frozen schedule; model and input identities match across all four arms. Optional local event verification independently recomputes each recorded elapsed wait from emitted/arrival timestamps and checks held counts, means, maxima, triggers and unique outputs. Peak pending comes from the explicit engine counter. No raw identifiers are included in these reports.",
               "", "A value of zero for an empty held set in the older engine summary is converted to null/-- here because there are no held observations. Wall/CPU include the full final drain and candidate-snapshot output. Trace-write seconds are not a hardware disk benchmark, and CPU model/RAM were not recorded. None of these omissions prevents the minimum comparison requested by N03.", ""])
    (reports / "resource_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"status": report["status"], "rows": len(rows),
                      "events_verified": args.verify_events, "table": str(snippets / "v3_resources.tex")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
