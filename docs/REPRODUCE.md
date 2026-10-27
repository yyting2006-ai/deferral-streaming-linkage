# Reproducing the corrected event-time study

Run from the repository root with Python 3.11. The new primary entry point is `experiments/e37_revision_suite.py`, not E26. [causal_protocol.md](causal_protocol.md) specifies the method, configuration, label assumptions and event schema.

## 1. Environment and local checks

```text
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` in a POSIX shell, then:

```text
python -m pip install -r requirements.txt
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('America/Los_Angeles'))"
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest discover -s audit -p "test_*.py" -v
python -m analysis.reproduce_louisville --verify --out build/louisville_reanalysis.json
python -m analysis.verify_manifest
```

The stack is NumPy, SciPy, scikit-learn, joblib and threadpoolctl. `tzdata` supplies the named timezone database on Windows. The corrected suite uses CPU only and no network model services. These pins record the observed environment; imports alone do not validate an independent replay.

Only retained external-baseline entry points need `requirements-baselines.txt`; corrected and historical figure generation use `requirements-figures.txt`. No Excel library is required for the released Louisville derivative.

## 2. Obtain the local source snapshot

Follow [DATA.md](DATA.md). With default paths:

```text
python fetch_data.py san_diego_2024 --source release
python fetch_data.py san_diego_2024 --verify-only
```

The default `--source release` uses the authorized `v0.2.0` GitHub Release ZIP. Archive and extracted CSV digests must both match; only the exact expected regular member is accepted, and no archive path is passed to unrestricted extraction. `--source portal` explicitly uses the original mutable portal instead. A portal 403 or changed export is not a successful verification. When overriding the input directory, give the same path to the fetcher's `--data-dir` and the loader's `PSID_DATA_DIR` environment variable; the fetcher does not read that variable itself. The fetcher never overwrites an existing input. If a local file differs, preserve it separately before requesting the archived version.

## 3. Start a fresh run

Choose a **new absolute directory outside the repository**. Copy only the frozen protocol, not released results or private pickles. These examples stop if the requested directory already exists.

PowerShell:

```powershell
$runDir = 'D:/research-replays/deferral-v2-my-run'
if (Test-Path -LiteralPath $runDir) { throw 'Choose a new run directory.' }
New-Item -ItemType Directory -Path $runDir | Out-Null
Copy-Item -LiteralPath 'reproductions/revision_v2/experiment_protocol.json' -Destination "$runDir/experiment_protocol.json"
$env:PSID_DATA_DIR = 'D:/municipal-inputs'
$env:PYTHONHASHSEED = '0'
```

POSIX shell:

```bash
RUN_DIR=/absolute/path/to/deferral-v2-my-run
test ! -e "$RUN_DIR" || { echo 'Choose a new run directory.'; exit 1; }
mkdir -p "$RUN_DIR"
cp reproductions/revision_v2/experiment_protocol.json "$RUN_DIR/experiment_protocol.json"
export PSID_DATA_DIR=/absolute/path/to/municipal-inputs
export PYTHONHASHSEED=0
```

Replace `/absolute/path/to/new-run` below with that directory, or use `"$runDir"` in PowerShell / `"$RUN_DIR"` in a POSIX shell. Worker count may be reduced to fit RAM/CPU. Each worker can load the annual-label cache, so six workers consume more memory than one. No fixed completion time or memory requirement is claimed here.

## 4. Prepare, fit, calibrate, replay

```text
python experiments/e37_revision_suite.py prepare --run-dir /absolute/path/to/new-run
python experiments/e37_revision_suite.py fit --run-dir /absolute/path/to/new-run --workers 6
python experiments/e37_revision_suite.py calibrate --run-dir /absolute/path/to/new-run --workers 6
python experiments/e37_revision_suite.py suite --run-dir /absolute/path/to/new-run --workers 6
```

Preparation interprets source-local timestamps, selects month membership by original local date, converts to UTC, and sorts by `(timestamp, id)`. It builds training pairs from the first 22,000 January--February arrivals. Five GBT fits use seeds 1, 2, 3, 4, 13; the logistic model uses seed 13. All share pair-sampling seed 13. Corrected issue counting and deterministic ties require rebuilt features and newly fitted models; historical models must not be substituted.

Calibration independently replays the fixed March threshold grid per model family. It records risk/volume eligibility, the chosen threshold and selection time before the main suite. The suite contains matched May/July policies and frozen secondary sensitivities. Band, gate and cutoff settings were inherited from already viewed historical work; May is exploratory and July is retrospective validation, not a prospectively preregistered field trial. The protocol lists all scheduled arms and seeds.

| Path under the new run | Meaning |
|---|---|
| `private/data_*.pkl` | Local record/label caches |
| `private/training_features.npz`, `private/model_*.pkl` | Private features/models |
| `private/*_events.pkl` | Private complete final events, assignments and merge logs |
| `private/*_candidates.jsonl` | Private incremental actual-scoring snapshots |
| `reports/preparation_manifest.json` | Input/source/environment provenance |
| `results/fit_*.json` | Completed fit provenance and checksums |
| `reports/calibration_selection.json` | Completed March selection |
| `reports/run_schedule.json` | Planned suite, not completion evidence |
| `results/<run_id>.json` | Completed aggregate replay and invariants |

Fit/replay cache identities bind relevant inputs, configuration and source hashes. A mismatch fails explicitly. Do not edit the frozen protocol or bypass a mismatch to reuse results. A new configuration needs another run directory and an amendment record. Never run `prepare` over a completed run as a shortcut for resuming it.

## 5. Summary and mechanism analysis

After the scheduled main comparisons finish:

```text
python -m analysis.summarize_revision --run-dir /absolute/path/to/new-run --mechanism
```

This writes `results_summary.json`, normalized integer-count metrics, matched contrasts, event/runtime timing summaries and the result manifest under `reports/`. It requires all five main seeds per expected group. Pointwise paired 95% t intervals describe scorer-fit variability, not population uncertainty or multiplicity-adjusted significance.

`--mechanism` additionally reads complete private logs for the six seed-13 policies in May and July. It reconstructs positive-pair additions/losses, directed wrong-merge sets, triggers, exact denominators, common-source support and stratified sensitivity. To rerun only these analyses:

```text
python -m analysis.summarize_revision --run-dir /absolute/path/to/new-run --mechanism-only
```

`--allow-partial` supports progress monitoring, but its explicitly partial output cannot replace the final main result. To recompute quality summaries from released aggregates without touching those files:

```text
python -m analysis.summarize_revision --run-dir reproductions/revision_v2 --out-dir build/revision_v2_summary
```

This aggregate-only command cannot recreate private-event occupancy or mechanism analyses; missing trace-dependent quantities are reported unavailable. Released trace-derived aggregate reports remain inspectable.

## 6. Candidate diagnostics and complete-prefix audits

```text
python -m analysis.candidate_analysis --run-dir /absolute/path/to/new-run
python -m analysis.revision_audit --run-dir /absolute/path/to/new-run --policy gate5 --mode probe
python -m analysis.revision_audit --run-dir /absolute/path/to/new-run --policy gate5 --mode determinism
```

Candidate analysis reads actual private JSONL traces and writes per-run/combined aggregates. It distinguishes direct positive coverage, transitive graph bounds, a capped static construction, and fixed-candidate comparisons. These graphs depend on the observed policy and are not policy-independent cluster-recall ceilings. `--run-id` restricts analysis to a chosen completed traced run; repeat the option for multiple runs, for example `--run-id May_gbt_s13_arrival --run-id May_gbt_s13_band`.

The real-stream audit uses May 1--7 and a fixed seed-13 model. Suffix perturbations are compared through identical UTC cutoffs using both complete, independently filtered final-event logs. A deliberately future-dependent scorer is a sensitivity control. Determinism mode runs independent processes with different Python hash seeds and compares complete event hashes. Results concern the recorded stream/policy/cutoffs, not all streams or platforms.

Reports are `reports/prefix_audit.json` and `reports/hashseed_replay.json`. Running a different audit policy replaces those filenames; retain separately named copies when comparing policies. Scripts unpickle trusted caches from the local runner. Do not use untrusted downloaded pickles. Publish reviewed aggregates, not `private/`.

## 7. Louisville from the released derivative

```text
python -m analysis.reproduce_louisville --verify --out build/louisville_reanalysis.json
```

Only `derived_data/louisville/anonymous_issue_graph.json` and numerical targets are needed. The command recomputes agreement and lag statistics using 4,000 issue resamples with seed 20261027. Source ids, exact dates, services, text, addresses and annotator names are omitted. The graph supports numerical reanalysis conditional on precomputed eligible pairs, not reconstruction of privacy screening or text-based eligibility.

## 8. Historical E26 and external baselines

Use a separate checkout:

```text
git clone --branch v0.1.0 --depth 1 https://github.com/yyting2006-ai/deferral-streaming-linkage.git deferral-history-v0.1.0
```

Follow that tag's README/dependencies for historical commands. Old entry points retained in the current checkout import corrected `methods.py`, so they are not byte-equivalent historical implementations. Even the tag cannot recover unrecorded historical Python hash seeds, selection timestamps or full configurations. Stored aggregates remain the historical record.

Top-level `analysis/make_tables.py`, `analysis/check_results.py`, and `analysis/make_figures.py` reconstruct historical evidence. They are not corrected-suite result generators. Historical E28 checks pair logs rather than complete final events. Historical Splink uses separate blocking/calibration and must not be described as sharing this engine's evolving candidates or identical cap semantics.
