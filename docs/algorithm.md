# Event-time irreversible linkage: pseudocode

The complete configuration, feature definitions, label assumptions, event
schema, and reproducibility contract are in [causal_protocol.md](causal_protocol.md).
The executable implementation is [`psid/causal.py`](../psid/causal.py).

Inputs are records in `(UTC arrival time, stable record id)` order, fixed scorer
`f`, threshold `tau`, marginal width `delta`, maximum delay `D`, cluster cap `C`,
pending-visibility switch `v`, and optional gate age `h` or cutoff age `X`.
State consists of committed identities, pending singleton identities, a causal
retrieval index, a deadline priority queue, and an append-only final event log.

```text
ADVANCE(T)
  while a pending timer has due <= T:
    choose minimum (due, identity id); set clock to due
    ignore canceled timers
    r <- sole member of pending identity p
    (q, s) <- best eligible indexed identity for r, excluding p
    if q exists and s >= tau: MERGE(r, q, deadline, source=p)
    else: NEW(p, deadline)
  set clock to T

ARRIVE(r)
  ADVANCE(r.arrival)                        # equal-time timers execute first
  store r's arrival-time attributes
  (q, s) <- best eligible indexed identity for r
  if no q or s < tau: create singleton p=I:r; NEW(p, arrival)
  else:
    hold <- D > 0 and tau <= s < tau + delta
    if gate enabled: hold <- hold and age(q's first representative) >= h
    if hold:
      create pending singleton p=I:r with due=r.arrival + D
      queue (due,p); expose p iff v
    else: MERGE(r, q, arrival)

NEW(p, trigger)
  require p is a singleton and its record has no final event
  cancel p's pending timer; expose p in the index
  emit NEW for p's record at the current clock

MERGE(r, q, trigger, optional source p)
  require r is uncommitted, r not in q, and actual size(q) < C
  if q is pending: NEW(q, claim)            # target finalizes before claimant
  if p supplied: require p={r}; cancel and remove p
  add only r to q; retain q's existing committed assignments
  emit MERGE for r into q at the current clock

FINISH()
  while any pending timer remains: ADVANCE(next exact due)
  require one final NEW or MERGE per observed record and zero pending records
```

Eligibility requires no self-membership, a target smaller than `C`, a latest
member within the 60-day active window, and the optional representative-age
cutoff. Model-score ties are resolved by lexical identity id. Deadline rescoring
uses only records already observed by that deadline. Pending identities always
remain singletons; a committed identity may grow but its members never move to a
different identity.

The complete output prefix through `T` contains every final event emitted at or
before `T`, in emission order. Audits compare both independently time-filtered
logs, including times, objects, actions, targets, triggers, and payloads, with the
same fixed scorer and common randomness. A length-based prefix of one log is
not a substitute for this comparison.
