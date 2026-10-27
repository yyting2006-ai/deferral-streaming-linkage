# Replay entry points and evidence scope

Run commands from the repository root with Python 3.11 and the required local data. Create `replays/` first. Use that directory for new outputs so recorded `results/` remain an immutable reference. All commands below are executable interfaces; only `--help`, aggregate reconstruction and synthetic tests were executed during package preparation.

## Primary policies

```bash
# Same scorer seed and window: arrival and score-band deferral
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0,0.5 --deadlines 7 --fresh --out replays/may_band_s1.json

# Staleness gate within the marginal-score region
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0.5 --deadlines 7 --defer-rule stale --stale-min 5 --int-min 0 --fresh --out replays/may_gate5_s1.json

# Arrival-time cutoff comparison
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0 --max-lag 10 --fresh --out replays/may_cutoff10_s1.json

# Matched invisibility ablations
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0.5 --deadlines 7 --visibility off --fresh --out replays/may_band_invisible_s1.json
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0.5 --deadlines 7 --visibility off --defer-rule stale --stale-min 5 --int-min 0 --fresh --out replays/may_gate5_invisible_s1.json

# June band replication
python experiments/e26_decision_timing.py --window june --seed 1 --deltas 0,0.5 --deadlines 7 --fresh --out replays/june_band_s1.json
```

May arrival, band, gate and cutoff use seeds `1,2,3,4,5,6,7,8,13` in the reported nine-seed aggregates. June and invisible-band comparisons use `1,2,3,4,13`; invisible-gate comparisons use `1,2,3,4,5,6,7,8`. Match seeds explicitly before differencing. The JSONs often omit their full original configuration; filenames and the reconstruction code provide partial provenance, not a complete run registry.

The default tau is 0.0358. Selection sweeps and the four measured operating points are preserved in the saved results. April cutoff X=14 has only three recorded seeds, whereas X=5 and X=10 have five; it is not a complete matched three-way selection. No frozen first-view/choice timestamps establish a prospective selection process. `analysis/evidence/selection_sweep_summary.csv` reports completeness.

## Stratification and external comparison

Generate local record-level dumps only when needed for mechanism analysis; keep them out of the repository:

```bash
python experiments/e26_decision_timing.py --window may --seed 13 --deltas 0,0.5 --deadlines 7 --fresh --out replays/may_s13.json --dump replays/e26_dump.json
python experiments/e30_lag_stratified.py --base replays/e26_dump_base.json --defer replays/e26_dump_d0p5.json --out replays/lag_stratified.json
python experiments/e31_splink_end2end.py --city sandiego --out replays/splink_sandiego.json
python experiments/e32_splink_supervised.py --city sandiego --out replays/splink_supervised.json
```

`e23_window_seeds.py`, `e25_batch_global.py`, `e27_retraction.py` and `e29_external_baseline.py` retain their own CLI entry points. Run `python experiments/<file>.py --help` for their options. Current `e26` exposes `--city chicago --window dev` and `--window test`; use the appropriate Chicago calibration rather than assuming the San Diego default tau transfers.

## Historical and complete-prefix audits

`experiments/e28_prefix_audit.py` is the historical pair-log checker. It compares equal-length slices selected from the baseline and cannot certify equal complete emission prefixes. Its 817/820 sum comes from overlapping cutoffs, not 820 independent decisions.

The separate `audit/compare_prefix.py` compares complete timestamped logs after independently filtering each by the same time. It requires explicit emission events, finalization identifiers/actions/targets and a shared-randomness fingerprint. The original engine does not provide those logs, so a new passing real-stream result requires instrumentation and replay. The included nine synthetic tests are comparator tests only.

## Reproducibility limits

The pipeline setting max_cluster=20 is a destination-counter eligibility threshold, not a hard cap on final cluster membership. The Issue.n counter includes the first member twice, and merging a provisional group does not check the combined membership count. Saved sweeps include observed maximum clusters larger than 20. Splink's capped-component routine enforces a true cap of 20. The max columns describe observed cluster sizes, while the destination-counter setting describes the historical eligibility rule.

Specifically, max_pred_cluster is computed after projecting onto known_ids (the workflow-labelled domain K). The comparison table therefore labels this column max on K; it does not establish a maximum over all requests. Both nine-seed pipeline groups have maximum 19 on K; Splink native/capped values use the same projection.

The code uses set-based candidate processing with unresolved tie ordering. The original JSONs do not record Python hash seeds, code hashes, complete argument vectors or every RNG state. Pinning `PYTHONHASHSEED` for new runs can improve repeatability but does not reconstruct an unknown historical state. Library versions in `requirements.txt` are the versions observed for these preparation checks, not a recovered lockfile from every original run.

The nominal deadline is checked on replay progression, and repeated merge rows are permitted by the historical implementation. See the README before interpreting latency or merge counts as a final irreversible output contract. Runtime/resource use for a complete clean-environment rerun has not been validated.
