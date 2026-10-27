# Timestamped prefix comparator

This is a standalone comparator and nine synthetic fault tests. It is not a new real-data audit result. Historical result `e28_prefix_audit.json` has not been replaced or relabelled.

Run with Python 3.10+ (standard library only):

```text
python test_compare_prefix.py
python compare_prefix.py baseline_events.json perturbed_events.json --cutoff 2024-05-01T12:00:00+00:00
```

Each input is a JSON object with a `common_randomness` fingerprint and an `events` array. Every event needs `emitted_at` (ISO 8601 with explicit timezone), `object_id` (stable string), `action` (stable string), and `target_id` (stable string or null). Optional fields such as `payload` are also compared. The log must already be in emission order; equal-time events retain list order.

Both complete logs are independently filtered by the same absolute cutoff `T` using `emitted_at <= T`. Equality checks length, order, normalized emission time, object, action, target, and all optional fields. Missing or different common-randomness fingerprints invalidate the comparison.

`shared_randomness` and `cloned_rng` demonstrate paired replay with identical Python RNG states. A real replay must additionally record/freeze every relevant model and library RNG state, fitted scorer, configuration, input-prefix snapshot and runtime; a supplied matching string alone does not prove that a harness respected this condition.

Tests cover an extra emission before T, a missing emission before T, earlier emission of the same action, changed action/target, equal-time ordering, a future-only change, shared RNG restoration, timezone equivalence, inclusive cutoff, and rejection of naive timestamps. Nine test methods pass. `test_result.json` records this synthetic scope explicitly.

The historical engine does not persist a complete timestamped emission stream, so its `assign`/`merge_log` dumps cannot be passed to this comparator as if they were complete output logs. Instrumentation and a declared finalization contract are needed before a new real-data audit.
