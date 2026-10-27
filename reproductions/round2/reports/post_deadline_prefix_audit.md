# Round 2 N01: post-deadline real-stream prefix audit

Status: PASS. This is an additional targeted audit. The earlier three weekly
prefix probes and their deliberately leaky positive controls are preserved.

The protocol was frozen before the new replay in `prefix_audit_protocol.json`
(SHA-256 `4ad46450cc07c5c1cb7f73fe4e9a9774549ea3774bae1128a06ee3e3e16a8d1a`).
It uses the complete May stream of 35,915 records, the existing corrected GBT
seed 13 scorer, an empty starting index, Gate h=5, D=7, and the original March
threshold .0358. No model was fitted or parameter selected in this supplement.

The fixed comparison cutoff is May 20 00:00 America/Los_Angeles, equivalently
2024-05-20 07:00 UTC. There are 21,683 input records through that cutoff and
14,232 strictly later records. The suffix perturbation rotates the later record
contents/IDs across their timestamps while preserving the complete input prefix.
Both streams are replayed to completion, and each output log is independently
filtered at the same inclusive cutoff before comparison.

| Pre-cutoff observation | Baseline | Rotated suffix |
|---|---:|---:|
| Final output events | 21,328 | 21,328 |
| Arrival-triggered outputs | 20,961 | 20,961 |
| Claim-triggered outputs | 229 | 229 |
| Deadline-triggered outputs / actual deadline re-scoring calls | 138 | 138 |
| Deadline calls executed during end-of-stream drain | 0 | 0 |
| Timestamps with both deadline and arrival scoring | 96 | 96 |
| Deadline calls at those tied timestamps | 98 | 98 |
| Timer-first order violations | 0 | 0 |

All 138 deadline outputs occur at the exact seven-day due time. The first is
2024-05-13 17:33 UTC and the last pre-cutoff deadline is 2024-05-20 03:34 UTC.
The ordered complete output prefixes match, including count, absolute emission
time, object, action, target, trigger, sequence, scores, and every payload field.
The common prefix SHA-256 is
`f23da7dc95154abdef6437d124d2d96273711d4262bb195d6fe53713da5c5254`.

The scheduling witness observes actual scorer-entry calls. It therefore tests
timer-before-arrival ordering even when an equal-time arrival becomes pending
and emits no immediate final output. No deadline scoring call had an equal-time
or future arrival present. A claim output is the pending target's NEW event;
the claimant separately has its own arrival or deadline trigger.

Both complete logs contain 35,915 unique finalizations. The instrumented baseline
also exactly matches the previously retained full May Gate seed 13 event log.
The original audit report checksum is unchanged. All 12 validation requirements
pass. These checks extend finite audit coverage; they do not establish a
universal nonanticipation guarantee.

Two single-thread local CPU replays took 75.85 and 80.55 wall seconds, with 74.81
and 79.48 process CPU seconds. These times include lightweight witness collection
and end-of-stream drain, exclude final log serialization, and do not include
candidate-snapshot writes. They are not production throughput estimates.

## Evidence and reproduction

- Aggregate report: `reports/post_deadline_prefix_audit.json`.
- Frozen design/input/source hashes: `prefix_audit_protocol.json`.
- Targeted/regression test summary: `reports/post_deadline_prefix_tests.json`;
  5 new coverage tests, 8 prior prefix tests, and 23 causal-engine tests pass.
- Private complete event and scheduling logs:
  `private/post_deadline_prefix_audit/`; file hashes appear in the public report.
- New implementation: `analysis/round2_prefix_audit.py`.
- New tests: `tests/test_round2_prefix_audit.py`.

From the repository root, after completing the documented revision-v2 data/model
preparation and original prefix audit, run with a 64-bit Python environment:

```powershell
python -m analysis.round2_prefix_audit --source-run /path/to/completed-run --output-dir /path/to/new-audit-run --freeze
python -m analysis.round2_prefix_audit --source-run /path/to/completed-run --output-dir /path/to/new-audit-run --run
```

The actual run used
CPython 3.11.9
64-bit, NumPy 2.4.6, SciPy 1.17.0, and scikit-learn 1.8.0. To make a new independent
reproduction, substitute a new output directory. `--freeze` refuses to overwrite
an existing frozen design. `--run` verifies every recorded input and source hash
before executing; the existing output directory can be replayed only while
those inputs and sources remain identical.
