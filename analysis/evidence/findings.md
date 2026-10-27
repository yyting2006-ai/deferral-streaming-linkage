# Evidence reconstruction for PVLDB revision — 2026-10-27

Scope: read-only review of the manuscript, table/audit generators, local JSONs/logs and current implementation. New files live only in this evidence directory. No model fitting, experiment replay, external API upload, or historical-result overwrite. 

## Directly supportable revisions

### Matched seeds and numerical corrections (M02, N01–N03)

`comparison_seed_rows.csv` names every source file and zero-based row. `matched_statistics.json` contains recomputed sample SDs and paired t intervals, conditional on this fixed stream and scorer fits.

| Contrast | Matched seeds | F1 means, first → second | Paired difference ± SD | t 95% interval |
|---|---|---:|---:|---:|
| May arrival → band | 1–8,13 | .42432911 → .46143011 | +.03710100 ± .00893230 | [.03023503,.04396697] |
| May cutoff10 → gate5 | 1–8,13 | .44870778 → .47318711 | +.02447933 ± .00987984 | [.01688501,.03207365] |
| May arrival → invisible gate5 | 1–8 | .42339075 → .42742000 | +.00402925 ± .00665776 | [−.00153678,.00959528] |
| May visible gate5 → invisible gate5 | 1–8 | .47311512 → .42742000 | −.04569512 ± .01086440 | [−.05477799,−.03661226] |
| May arrival → invisible band | 1–4,13 | .42437560 → .42138980 | −.00298580 ± .00321187 | [−.00697386,.00100226] |
| June arrival → band | 1–4,13 | .48201880 → .49702980 | +.01501100 ± .00241300 | [.01201486,.01800714] |

Eight-seed marginal SDs: arrival .00226221, invisible gate .00463454, visible gate .00989774. The invisible-gate difference is positive in 5/8 seeds, not evidence of equivalence to zero. Nine-seed arrival .42432911 must not be used as the marginal mean of this eight-seed contrast.

Gate–cutoff .02447933 is 2.4479 percentage points of F1. Logistic .505−.474=.031 is 3.1 percentage points. The single saved timing run has 2.736 → 3.128 ms/record, +14.33%; its reciprocal rate is −12.53% under equal serial resources. Using the displayed rounded 2.7 → 3.1 gives +14.81%/−12.90%. No measured concurrent throughput is saved. `n_deferred` measures entry to the provisional path, not deadline-trigger count.

### Selection record and its limits (M02)

`result_row_manifest.csv` lists 209 E26 result rows with SHA256, persisted seed/date/window, row parameters, and explicit missing first-view/lock times. Missing seeds in legacy files are attributed by the existing analysis-script mapping and E26 default 13; `seed_persisted` distinguishes that attribution from stored metadata. File mtime was not used as a lock or pre-registration time.

April gate thresholds 2/3/5 each have seeds 1–4,13: mean F1 .4833988/.4838588/.4845932. April gate deadline D=3/7/14 each has those five seeds: .4798052/.4845932/.4814992. Thus the observed numerical argmax is gate5 and D7 among these stored sweeps. These results do not establish that the candidate set or choice was frozen before May was inspected.

**Cutoff selection is incomplete and cannot be called an April argmax over {5,10,14}.** X5 and X10 have five April seeds with means .4354180/.4567154. X14 has only seeds 1,2,13 and mean .4612973. `historical-local-analysis/_april_x.py:43–45` skips candidates with fewer than five runs, then prints “April selects X=10.” This is a completeness filter, not a fully matched three-way optimization. Do not impute missing X14 runs. `selection_sweep_summary.csv` exposes all counts. For band δ=.3/.5/.7, combine four `e26_april_s*.json` rows with the respective seed13 legacy April row; the means remain .4975118/.4959424/.4586646.

The current E26 checkpoint (`experiments/e26_decision_timing.py:423`) does not persist scorer family, max_lag, defer_rule, stale_min, int_min, int_window, command line, version hash, hash seed, or freeze timestamps. Filename and auxiliary-script associations are useful provenance but not complete run configurations. Existing June files test band/arrival; no June gate-versus-cutoff files were found. Preserve May policy comparisons as exploratory and avoid claiming prospective frozen testing.

### FMP denominator reconstruction (M04)

`psid/decision_metrics.py:16` counts every `(rid,rep)` row in `merge_log`; FMP = wrong/(correct+wrong), coverage = (correct+wrong)/all logged merge rows. These are workflow-label-relative decisions. `psid/gold.py:75` returns false whenever two known records occupy different workflow components: this is a closed-world partition-label assumption, not separate human negative adjudication.

All 209 saved E26 rows admit exactly one integer solution for adjudicable/correct/wrong/unknown under the saved six-decimal FMP and coverage rounding. `merge_denominators.csv` labels them `unique_integer_reconstruction`; `comparison_denominators.csv` selects manuscript arms. Counts from seed13 dumps were independently checked against the original CSV workflow links:

| Arm | Records in partition | Logged merge rows | Adjudicable | Correct | Wrong | Unknown |
|---|---:|---:|---:|---:|---:|---:|
| arrival | 35,915 | 8,626 | 2,454 | 1,380 | 1,074 | 6,172 |
| band | 35,915 | 7,907 | 2,492 | 1,680 | 812 | 5,415 |

These exact one-seed counts establish an increase of 38 adjudicable merge rows for that run. Nine-seed mean counts are in `comparison_denominator_means.csv`; they are means, not one run's integer counts. Unknown merge rows are not all NEW/unresolved decisions, and no full final-submission count is saved.

**Logged rows are not unique final record commits.** The band dump contains 571 source records appearing more than once, 722 extra source rows, and a maximum of six merge entries for one record; arrival has none. Therefore 7,907 must be called logged merge operations, not 7,907 independent immutable final decisions. This observation does not by itself show a cluster split/retraction; it does prevent equating the log with a one-final-output-per-record contract.

### Set differences actually available (M03, N04)

`e30_lag_stratified.json` already stores both/base_only/defer_only/neither for the seed13 band. Per gold-pair lag ≤1, 1–7, 8–30 days: common=1612/817/164; newly recovered by band=418/199/20; lost from arrival=462/287/144. Total additions=637, losses=893, net −256. Thus “adds certified pairs nowhere” is false even though every bucket has a negative net change.

New `wrong_merge_pair_set_counts.csv` intersects exact directed (record, representative) pairs in the saved logs. Per target-age bin ≤1, 1–7, 8–30 days: common=81/110/117; arrival-only=178/197/391; band-only=158/263/83. This distinguishes avoided and introduced logged wrong pair operations. It is not a timestamped event decomposition and should not be conflated with final clustered pair identity. No gate dumps or trigger-specific event logs exist in the inspected results directory, so the corresponding gate or evidence/deadline decompositions remain unavailable.

Louisville `e35b_louisville.json` has 292 records, 226 clusters, 26 multirecord clusters. Same-issue counts are 43/537,18/366,35/398,38/639 across 0–1,2–3,4–6,7–13 days; neither monotonicity nor statistical equivalence to a flat profile is demonstrated. `_persist` script also filters same service and token Jaccard≥.10, so the label “same-service candidates” alone omits a filter. The source is private R652 human-gold development/temporal-confirmation files, not independent citywide random pairs. Do not infer annotation agreement from filenames. Chicago single-run differences should remain descriptive.

## Implementation and formal-protocol boundary (M08)

The saved D14 rows in `e26_decision_timing.json` have mean latencies 14.48/14.98 days and maximum 28 days. D7 band/gate rows have maximum 14 days. These are saved run observations, not inferred estimates.

The current implementation explains why hard deadlines cannot be claimed: `_advance` (line221) calls `_resolve(day)` only when an arriving record advances the integer day; `finish` (line292) calls `_resolve(last_day+D+1)` once. Latency is measured from deferred arrival to that processing day. There are no real-time timer callbacks. At month end the code advances a synthetic clock but adds no later-month observations; it re-scores against the window's retained index. Exact overrun quantiles and trigger fractions cannot be recovered from saved mean/max summaries.

Algorithm semantics should distinguish the intended state machine from the measured implementation:

1. A score-band record enters a provisional index state with a nominal due date. Gate is an additional predicate within the marginal-score region; it does not defer score≥τ+δ records. The current `_remember` runs before intensity, so intensity includes the current arrival; reported int_min=0 makes that condition vacuous.
2. High-confidence arrival into a provisional target removes its pending timer (line260), but no separate finalized action is logged for the prior provisional representative. A marginal arrival bypassing the gate merges without this cancellation (line276–281).
3. `_resolve` can select a provisional target; `_merge_into` (line187) moves every member and records another merge for each member. A subsequent target resolution can move them again. This differs from a formal one-shot two-object finalization rule.
4. `decisions` includes PENDING and incomplete finalization entries; only `assign` and pair-only `merge_log` are persisted (line322). No complete timestamped emission log, timer cancellation log, or candidate snapshots support a full real-time contract audit.

For a future production algorithm, specify stable object keys, one finalization event per object, target reservation/finalization, timer cancellation, and timestamped emission. Those would be implementation changes requiring new experiments; do not silently present them as the semantics behind historical numbers.

## Prefix audit and deterministic replay (M01, M08)

The actual historical checker is exactly the weak k-slice described in the review: `experiments/e28_prefix_audit.py:153–155` takes `n=prefix[s]` from baseline and compares the first n pair rows of both full logs. It does not independently filter the perturbed log by the same real time, and does not compare emitted timestamps or NEW actions. The code is not stronger than the paper description. `e28_prefix_audit.json` reports positive-control differences 185/186,283/284,349/350 across overlapping cutoffs; their sum 817/820 is not 820 independent decisions.

`audit/compare_prefix.py` now implements a bounded standalone complete-event comparator, with explicit timestamp zones, each log independently filtered at the same T, and equality over length/order/time/object/action/target/payload. `audit/test_compare_prefix.py` passes nine synthetic test methods, including extra/missing/early events and restored identical RNG states. This validates comparator behavior, not the historical system. No new real-stream passing verdict is claimed.

Historical `historical-local-analysis/e28.log` records about 216 seconds end-to-end for 8,153 records: training71 seconds plus eight replays around145 seconds. A similarly sized instrumented rerun may cost several minutes, but complete emission instrumentation and semantics are prerequisites. The historical dumps cannot be retroactively turned into true-time output logs.

Current `psid/methods.py` contains no Python object-id ranking. Concrete instability points are `sorted(set(tokens), key=-idf)` at line160 without a lexical tie-break, tied candidate-score insertion order at line165, and `np.argmax` selecting the first tied candidate in E26 line184. Pinning Python hash seed is not documented in the JSONs; no deterministic cross-process replay proof is available. `e23_window_seeds.py:72–95` explicitly searches toward the lowest feasible τ via hi=mid, contradicting the manuscript's “largest”; monotonicity of the evolving stateful system is not established. Limit threshold claims to the four tested operating points. Only logistic band/cutoff are present, not logistic gate.

## Retrieval-description correction

The released `psid/methods.py` has SHA256 `055dddead533d56821836d22f14a69cf3dcbab9928097d77190b9116d65b9a6c`. E26 imports this `PrefixIndex` and calls `candidates` at line163. Its implementation (`methods.py:143–168`) retrieves service-conditioned exact-address and block channels (40 each), then up to60 IDF-token-scored candidates, deduplicating the union without a final60-item trim. The combined upper bound is140 before eligibility filters. `by_service` is maintained but not queried by `candidates`; no dense, alias or BM25 retrieval implementation supports the former five-channel prose. A nearest-day slice is used for structured lists, including when the query is an old provisional representative. These are code observations for the measured implementation, not a general blocking-system description.

The revised manuscript distinguishes the intended causal final-output interface from historical simulator updates, logged operations and latency.

## Reuse and remaining evidence

### Destination-counter limit versus cluster cap

Implementation review additionally distinguishes the pipeline's destination-counter eligibility threshold of 20 from a true final component cap. The Issue.n counter includes the first member twice; the deferral merge operation does not test combined membership before moving a group. Stored sweeps include observed maximum clusters above 20. The Splink capped-component routine enforces a true size cap of 20. Table max columns report observed sizes rather than assuming that the configured destination-counter threshold is a membership invariant.

The saved max_pred_cluster measure projects clusters onto known_ids (domain K) before taking their maximum size. The table reports max on K: both nine-seed pipeline groups yield 19; native/capped Splink values use the same domain projection. These values are distinct from both full-stream maximum membership and the configured destination-counter threshold.

The aggregate evidence here was reconstructed from the saved results and a local input CSV. Private-input reconstruction was performed locally; this package releases aggregate evidence only. Python 3.11 and SciPy were used for t quantiles without model fitting. The prefix comparator/tests are standard-library only. The raw_count_context.json file gives the input CSV SHA256, with its path normalized to a repository-relative filename. No raw addresses, request text, or record identifiers are exported by these tables.

Remaining material evidence gaps: frozen run chronology and complete commands/configs; independent gate-versus-cutoff confirmation; complete timestamped finalization logs; trigger-stratified timing and state load; gate set differences; missing April X14 seeds; wider threshold exploration; logistic gate; stronger external/generalization and labeling validation. These are not passed by arithmetic corrections or template replacement.
