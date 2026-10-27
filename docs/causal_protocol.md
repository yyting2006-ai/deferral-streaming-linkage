# Corrected causal streaming protocol

This document specifies `psid/causal.py` and `experiments/e37_revision_suite.py`.
The implementation is a corrected protocol, identified as `causal-event-v2`.
Historical E26 outputs must retain their historical implementation label; they
are not outputs of this engine. The executable experiment protocol JSON and
per-run provenance reports are the authority for a particular experiment.

## 1. Time, information, and final output

For record `i`, `a_i` is its arrival timestamp and `c_i` is its finalization
timestamp. Its elapsed delay is `d_i = (c_i - a_i) / 86400 seconds`. All engine
timestamps must be timezone-aware and are normalized to UTC. The new San Diego
runner interprets source timestamps as `America/Los_Angeles`, selects month
membership using the original local date, then converts timestamps to UTC.
The source does not encode a DST fold; Python's default fold 0 is the explicit
interpretation for ambiguous local times. Calendar-day features use UTC dates.

Arrivals are processed in ascending `(UTC timestamp, record id)` order. At a
shared timestamp, every due timer executes before arrivals. Equal-time timers
are ordered lexically by their stable identity id, `I:<record id>`. A deadline
is exactly `arrival + timedelta(days=D)`, including across daylight-saving
transitions. Every evaluation window starts with an empty index and no pending
records. `finish()` processes remaining deadlines at their individual due times
using only the already supplied window records; it does not load next-month
records or substitute a common late flush timestamp.

Each observed record emits exactly one final event, either `NEW` or `MERGE`.
Pending state is internal and is not a final output. `NEW` creates a committed
identity anchored by that record. `MERGE` assigns the arriving or held record
to an existing committed identity. A committed assignment is never changed.
New members may join an identity, but its already committed members never move
to another identity. Thus an output consumer can persist each event directly.

The immediate-commit arm has `c_i = a_i`. Deferred arms satisfy
`a_i <= c_i <= a_i + D`. Causality is separate from either latency rule: for a
fixed initial state, scorer, configuration, and randomness, two inputs with
the same arrivals through time `T` must emit identical complete ordered final
event sequences through `T`. Equality includes emission timestamps, record ids,
actions, targets, triggers, and payloads. It is not a comparison of only the
first `k` events from either run.

## 2. State and event algorithm

An identity contains ordered member ids, the first up to three representative
ids, arrival-derived structured keys, and its first/latest member dates. Only
the first representative is scored. An identity is either committed or a
pending singleton; pending identities never acquire committed members. The
engine stores pending deadlines in a priority queue and cancels a timer by
removing its identity from the pending map. Stale queue entries become inert.

```text
ON_ARRIVAL(record r):
    require a timezone-aware timestamp, unique id, and sorted arrival order
    ADVANCE(r.arrival)                       # timers first, including equality
    store r's arrival-time fields
    (target, score) = BEST(r, time=r.arrival, exclude=r)
    if no target or score < tau:
        create singleton identity I:r
        FINALIZE_NEW(I:r, trigger=arrival)
    else if HOLD(target, score, r.arrival):
        create singleton identity I:r
        mark pending with due=r.arrival + D
        insert timer (due, I:r)
        expose I:r in the index iff visibility is enabled
    else:
        COMMIT_MERGE(r, target, trigger=arrival)

ADVANCE(T):
    require T >= current clock
    while next timer.due <= T:
        pop earliest (due, identity), with lexical identity tie breaking
        if identity is no longer pending: continue
        set clock to due; expire retrieval state
        r = the identity's sole record
        (target, score) = BEST(r, time=due, exclude=identity)
        if target exists and score >= tau:
            COMMIT_MERGE(r, target, trigger=deadline, source=identity)
        else:
            FINALIZE_NEW(identity, trigger=deadline)
    set clock to T; expire retrieval state

COMMIT_MERGE(r, target, trigger, optional source):
    require r is uncommitted, absent from target, and target size < 20
    if target is pending:
        FINALIZE_NEW(target, trigger=claim)  # target event emitted first
    if source exists:
        require source is the pending singleton {r}
        remove source from pending state, retrieval index, and identity map
    append r to target; keep latest-member date nondecreasing
    append directed scored pair (r, target's first representative) to merge log
    emit final MERGE for r, preserving any source deadline in the payload

FINALIZE_NEW(identity, trigger):
    require identity is a singleton and its record is uncommitted
    remove its pending state, making its queued timer inert
    make it retrievable, including previously invisible identities
    emit final NEW for its sole record

FINISH():
    repeatedly ADVANCE(next timer's exact due time)
    require zero pending records and one final event per observed record
```

`HOLD` first requires `tau <= score < tau + delta` and `D > 0`. The band arm
holds every such marginal record. The gate arm additionally requires
`decision time - target representative's arrival >= stale_min` days. A gate
bypass still executes the complete merge procedure, including a pending
target's finalization. A marginal record that satisfies `HOLD` creates its own
pending singleton even if the best candidate is also pending; it does not claim
the target until a final merge is selected. A deadline accepts a score at least
`tau` without deferring again.

This construction gives an induction argument for immutable assignments and
causal output prefixes: initial state is empty; each timer or arrival transition
uses only existing state and the current arrival, is deterministic given the
fixed scorer, and writes a final assignment only once. This argument assumes
that no future-dependent scorer, index initialization, or configuration is
injected. The positive-control audit intentionally violates that assumption.

## 3. Retrieval and representation

The corrected suite uses the actual three-channel `PrefixIndex` implementation:

| Channel | Key or ranking | Limit |
|---|---|---|
| Exact address | `(normalized service, normalized address)` | 40 posting entries |
| Address block | `(normalized service, hundred block + street)` | 40 posting entries |
| Token index | Sum of IDF weights for matching query tokens | 60 candidate identities |

Text is lowercase, accent folded, non-alphanumeric characters replaced by
spaces; tokens of length one are dropped. The query contains normalized
description and service fields. For token `t`, IDF is
`log(1 + n_indexed_documents / (1 + df(t)))`. The eight query tokens with largest
IDF are used, with lexical token ties. Each token scans its latest 60 posting
entries; postings retain at most 2,000 entries. Text candidate scores are sorted
by decreasing sum of IDF and then lexical identity id. This is not BM25 and the
suite does not add independent dense, alias, or category retrieval channels.

Structured posting selection uses a contiguous calendar-ordered slice around the query date's insertion point (not absolute-distance nearest neighbors):
find the first posting date at or after the query record's UTC date, take a
40-entry slice centered around that position, and clamp the slice to available
entries. For ordinary arrivals this selects the recent end of the list. For
deadline rescoring the query retains its original date. Posting lists remain
calendar sorted when an invisible older record first becomes visible. Channel
results are concatenated and deduplicated, so 60 is a text-channel budget, not
a global three-channel cap; the union can contain up to 140 identities before
eligibility filtering.

An identity is indexed when it first becomes visible. Member attachment updates
identity state but does not add that member's tokens as a new index document.
Daily index expiration uses the latest member's UTC date; scoring additionally
requires the latest member to be within the exact 60-day elapsed-time window.
The `max_lag` comparison excludes a target whose first representative is older
than the specified elapsed days at the scoring time. Every scoring call also
excludes self-membership and targets already containing 20 actual records.
Remaining candidates are sorted by identity id before scoring; equal model
scores select the lexical identity. No object address or Python hash ordering
is used as a tie rule.

## 4. Scorer inputs and training assumptions

There are 21 feature slots in the fixed order below. No closure/status field
enters the scorer. The query's original arrival attributes remain unchanged at
deadline rescoring; the candidate identity's current size/latest member date
can change. For example, the calendar gap feature clips a negative difference
to zero when a held older record is scored against a later-arriving candidate.

| Feature | Definition |
|---|---|
| `same_addr` | Nonempty normalized address keys equal |
| `same_block` | Nonempty normalized hundred-block keys equal |
| `same_street` | Nonempty normalized street tokens equal |
| `same_zip` | Nonempty five-character zip strings equal |
| `same_service` | Normalized service strings equal |
| `addr_missing` | Either street string missing |
| `desc_missing` | Either description missing |
| `cos` | Token-count cosine similarity |
| `jac` | Token-set Jaccard similarity |
| `char3_jac` | Jaccard of normalized description character trigrams |
| `log_gap` | `log1p(max(0, query UTC date - latest member UTC date))/5` |
| `same_day` | The clipped calendar gap is zero |
| `gap_le_7` | The clipped calendar gap is at most seven |
| `issue_size` | `min(10, log1p(actual member count))/log(11)` |
| `num_dist_100` | House-number absolute difference divided by 100, capped at one; zero if unavailable |
| `geo_dist` | Coordinate distance approximation in km, capped at 20 and divided by 20 |
| `service_rare` | Nonempty service indicator; despite its historical name, not a rarity estimate |
| `len_ratio` | Shorter/longer token-list length, denominator at least one |
| `addr_emb_cos` | Embedding dot product if supplied; zero in the corrected suite |
| `addr_emb_dist` | Negative embedding distance if supplied; zero in the corrected suite |
| `geo_missing` | Coordinates unavailable indicator |

The corrected suite does not attach address embeddings, so both embedding
feature slots are zero. Training replays each of the first 22,000 sorted
January--February records as a singleton candidate identity. Consequently
training `issue_size` is constant at `log(2)/log(11)`, whereas deployed candidate
identities may have multiple members. `Issue.n` now starts at zero before the
first `absorb`, so a singleton counts as one. This fixes the previous count of
two and requires regenerating features and fitting new models. Historical model
files must not be assumed compatible with the corrected feature implementation.

Training candidates in the same known workflow component are positive. Other
candidates, including those involving a record outside the known domain, are
sampled as weak negative examples with keep rate 0.35 and pair-sampling seed 13.
They are not verified operational nonmatches. The workflow graph is built from
the retrospective annual export. Its labels and transitive paths can have been
recorded after the January--February request arrivals; the data do not establish
label availability at a historical deployment date. Output-prefix causality for
a fixed fitted model does not establish such label-availability causality.

The GBT has 160 trees, learning rate 0.1, maximum depth 3, subsampling fraction
0.5, minimum leaf size 20, and scorer seeds 1, 2, 3, 4, 13. The secondary model
uses `StandardScaler` and logistic regression with `C=1`, `lbfgs`,
`max_iter=4000`, no class weights, seed 13. Training pair order and token ties
are deterministic. The five GBT fits vary their model seed while sharing the
same training pairs. Their intervals describe fit-seed variability, not
independent samples of municipal populations.

## 5. Evaluation configuration and provenance

| Component | Corrected suite setting |
|---|---|
| Training requests | January--February 2024, first 22,000 sorted arrivals |
| Threshold calibration | March 2024; full fixed grid per model family, model seed 13 |
| Development sensitivity | April 2024, seed 13; fully reported fixed grids |
| Exploratory evaluation | May 2024 |
| Additional retrospective validation | July 2024; configuration frozen before revised outcomes were inspected |
| Main paired comparisons | Five GBT seeds, identical seeds across all six arms |
| Band | `delta=.5`, `D=7`, visible pending identities |
| Gate | Band condition plus representative age at least five days |
| Cutoff | Immediate commits, representative age at most ten days |
| Visibility controls | Band and gate respectively with `visible=False` |
| Sensitivity checks | D=3/14 for band and gate; April delta=.3/.5/.7, gate age=2/3/5, cutoff=5/10/14 |
| CPU execution | Six worker processes; one numerical-library thread per worker |

The March threshold grid is `.01, .02, .0358, .05, .07, .1, .15, .2, .3, .5,
.7, .9, .99`. Among points with at least 1% of records committed as merges,
at least 100 known-domain merges, and proxy decision FMP at most .40, select
the highest known-positive-pair recall, breaking ties by higher F1 and then
higher threshold. If no point satisfies the risk target, select the lowest proxy
FMP among the volume-eligible points, then higher recall, F1, and threshold, and
report infeasibility. Stateful replay is evaluated independently at every point;
no monotonicity assumption or threshold bisection is used. The previously viewed
band/gate/cutoff choices are inherited, not newly independent April selections.

Evaluation projects predicted clusters onto records participating in the
retrospective workflow graph. Same-component pairs are positive; different
components are treated as negatives under a closed-world workflow assumption.
Unknown records are excluded from these pair denominators. A directed merge is
adjudicable only when both its source and actual scored representative are known.
Decision FMP is wrong/adjudicable merges; coverage is adjudicable/all final merges.
Both counts and unknown counts accompany these proportions. In the canonical
analysis, `analysis.summarize_revision.normalized_row` recomputes ratios from
the integer counts and represents zero-denominator FMP and coverage as `null`.
The original replay JSON preserves the historical metric-helper encoding:
`March_logit_s13_tau0.99.json` has zero merges and stores `0.0` in
`decision.dec_fmp`, `decision.dec_coverage`, and `metrics.fmp`. These are legacy
sentinels, not zero-risk measurements. The raw file and its original metadata
remain unchanged for provenance; `raw_encoding_overrides` in the normalized
results records all three replacements. `json_finite` converts nonfinite
values to `null` but preserves finite sentinels, so raw float fields alone
must not be used to select or plot feasible operating points.

Every model report binds its protocol, feature file, training source hashes, and
serialized model checksum. Every replay report binds its exact job, protocol,
preparation manifest, model checksum, and evaluation source hashes. Reusing a
cache with a different identity raises an error rather than silently adopting
the result. The initial six models completed before cache-identity hardening;
their reports explicitly mark the later provenance reconciliation and preserve
their original fit timestamps/checksums. Preparation reports also record software
versions and the raw-file checksum. In the initial preparation cache only,
`gold.link_info` lag metadata was computed before UTC conversion; those metadata
are not used by corrected metrics or mechanism analyses, which use UTC record
timestamps. Subsequent preparation builds this metadata after conversion.

## 6. Logs, timing, privacy, and checks

Each final event has `emitted_at`, `object_id`, `action`, `target_id`, `trigger`,
and `sequence`. Its immutable payload contains the assigned identity, actual
scored representative (for merges), score, arrival time, original deadline,
whether the record was held, elapsed delay, and representative age. Trigger
values are `arrival`, `claim`, and `deadline`. A claim can emit two events at
the same timestamp; the target's NEW precedes the claimant's MERGE.

The summary includes all-record and held-record delay distributions (count,
mean, median, p95, maximum), peak pending count, deferred fraction, actual
deadline-resolution count, claim count, and the number finalized while draining
the observation window. Window-drain events still carry their true claim or
deadline triggers; draining is not a distinct decision policy.

When enabled, each arrival/deadline scoring trace records the query id, original
arrival, decision time, policy, and every actually scored eligible candidate's
identity, first representative, current member ids, score, pending flag, elapsed
representative age, and signed source/representative arrival gap. This is the
post-filter scored candidate subset, not every record in the corpus. Candidate
graph bounds are conditional on that policy's trace and do not constitute a
counterfactual run with shared evolving candidate state.

Traces are drained incrementally to private JSONL after each arrival and after
final draining. Full replay events, candidate membership and fitted models remain
private; publish their aggregate reports and code. The author-authorized public
San Diego and Chicago source snapshots are separate versioned Release assets,
described in [DATA.md](DATA.md). Private Louisville source renderings and workbooks
remain excluded; its minimal anonymous numerical derivative is released separately.
Runtime reports separate
trace serialization time and state their concurrent-worker setting. Arrival
timing includes timers due before that arrival and snapshot construction but
excludes subsequent JSONL serialization. Wall time includes trace I/O and is not
an isolated throughput benchmark.

Run the synthetic regression checks from the repository root:

```text
python -m unittest discover -s tests -v
```

The causal tests cover exact deadlines, idle gaps, observation-window draining,
timer/arrival equality, lexical arrival and model-score ties, self exclusion,
twenty actual members, pending target finalization in both confident and gate
bypass paths, deadline-to-pending claims, invisible-to-NEW visibility, immutable
committed assignments, duplicate finalization, UTC handling, and seeded
multi-policy complete-prefix equivalence. Separate audit tests inject extra,
missing, and early emissions. Passing synthetic tests is distinct from passing
the real-stream complete-prefix audit; the corresponding result report must be
cited for that empirical claim.
