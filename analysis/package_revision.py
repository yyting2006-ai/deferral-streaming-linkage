"""Copy a reviewed, explicit aggregate-only allowlist into the release tree.

This local preparation command does not publish or alter Git state. It refuses
partial suites and record-level payloads. Private traces, fitted models, source
records and manuscript files are outside the allowlist.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPORTS = (
    "preparation_manifest.json", "fit_cache_provenance_backfill.json",
    "calibration_selection.json", "run_schedule.json", "results_summary.json",
    "summary_stats.csv", "paired_contrasts.csv", "event_timing_summary.csv",
    "result_manifest.csv", "normalized_results.csv", "stage_manifest.csv",
    "historical_stage_rows.csv", "training_feature_ranges.csv",
    "prefix_audit.json", "hashseed_replay.json",
)
MECHANISM = (
    "arm_denominators.csv", "common_support.csv", "mechanism_analysis.json",
    "merge_operation_breakdown.csv", "positive_pair_changes.csv",
    "standardized_fmp.csv", "stratified_fmp.csv", "trigger_breakdown.csv",
    "wrong_merge_changes.csv",
)
FIGURES = ("fig1_workflow", "fig2_main_comparison", "fig3_risk_recall", "fig4_mechanism")
RECORD_KEYS = {"record_id", "query_id", "source_id", "request_id", "member_ids",
               "service_request_id", "street_address", "public_description",
               "assignments", "merge_log", "candidate_snapshots", "events"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_aggregate(value, source):
    if isinstance(value, dict):
        forbidden = RECORD_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"Record-level keys in {source}: {sorted(forbidden)}")
        for item in value.values():
            check_aggregate(item, source)
    elif isinstance(value, list):
        for item in value:
            check_aggregate(item, source)


def package(run, destination):
    run, destination = Path(run).resolve(), Path(destination).resolve()
    if run == destination or run in destination.parents or destination in run.parents:
        raise ValueError("Source and destination must be separate directory trees")
    protocol = read_json(run / "experiment_protocol.json")
    summary = read_json(run / "reports/results_summary.json")
    if not summary["complete_core"] or summary["missing_core"]:
        raise ValueError("Cannot package an incomplete matched-seed core")
    schedule = read_json(run / "reports/run_schedule.json")
    expected = {job["run_id"] for job in schedule["jobs"]}
    for family in ("gbt", "logit"):
        for tau in protocol["threshold"]["grid"]:
            expected.add(f"March_{family}_s13_tau{tau:g}")
    actual = {row["run_id"] for row in summary["rows"]}
    if actual != expected or len(expected) != 120:
        raise ValueError(f"Suite mismatch: missing={sorted(expected-actual)}, unexpected={sorted(actual-expected)}")
    files = [Path("experiment_protocol.json")]
    files.extend(Path("results") / f"{name}.json" for name in sorted(expected))
    files.extend(Path("results") / f"fit_gbt_{seed}.json" for seed in protocol["training"]["gbt_seeds"])
    files.append(Path("results/fit_logit_13.json"))
    files.extend(Path("reports") / name for name in REPORTS)
    candidate_reports = []
    for window in ("May", "July"):
        files.extend(Path("reports") / f"mechanism_{window}" / name for name in MECHANISM)
        for arm in protocol["core"]["arms"]:
            name = Path("reports") / f"candidate_{window}_gbt_s13_{arm}.json"
            files.append(name)
            candidate_reports.append(read_json(run / name))
    for name in FIGURES:
        files.extend(Path("figures") / (name + suffix) for suffix in
                     (".pdf", ".svg", ".png", ".source_data.csv"))
    # Validate everything before writing. Publication requires independent
    # review even though these checks catch common accidental inclusions.
    for relative in files:
        source = run / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix == ".json":
            check_aggregate(read_json(source), relative)
        elif source.suffix == ".csv":
            with source.open(encoding="utf-8-sig", newline="") as handle:
                columns = next(csv.reader(handle), [])
            if RECORD_KEYS.intersection(columns):
                raise ValueError(f"Record-level columns in {relative}")
    destination.mkdir(parents=True, exist_ok=True)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run / relative, target)
    (destination / "reports/candidate_analysis.json").write_text(
        json.dumps({"scope": "Twelve completed seed-13 corrected policy traces",
                    "runs": candidate_reports}, indent=2) + "\n", encoding="utf-8")
    report = {"scope": "Explicit aggregate and figure allowlist; no private artifacts or manuscript",
              "completed_replays": len(expected), "fit_reports": 6,
              "candidate_reports": len(candidate_reports),
              "files": {p.as_posix(): {"bytes": (run/p).stat().st_size,
                                      "sha256": digest(run/p)} for p in files}}
    (destination / "package_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "reproductions/revision_v2")
    args = parser.parse_args()
    report = package(args.run_dir, args.out)
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, indent=2))


if __name__ == "__main__":
    main()
