"""Historical pair-log audit; not a complete timestamped output-prefix check.

See docs/REPRODUCE.md for its k-slice limitation and audit/ for the new comparator.
The following original experiment rationale is retained for provenance.

E28: an output-prefix-safety audit that is shown to have power.

The paper's original metamorphic audit passed on both a prefix-safe and a
future-visible pipeline, so it certified nothing: a probe that cannot fail on a
known leak is not a test. This is the replacement, built for the deferral policy,
where "prefix-safe" has to mean something slightly different from the arrival-time
engine:

    a decision *emitted* at stream position t may depend only on records that
    arrived at or before t.

A deferral policy emits a decision when it *commits*, not when the record arrives,
so it satisfies this even though the commit can read records that arrived after the
record itself. That is the whole admissibility argument, and it has to be auditable.

The audit: perturb the address and text of the record at position s, replay, and
require every decision emitted before position s to be unchanged. Perturbing the
address matters here, unlike in the text-only audit: this pipeline's leak channel is
the structured anchor, not the description.

The positive control: a deliberately leaky variant that lets the scorer peek at the
address block of the *next* record. If the audit cannot flag that, it has no power
and the audit result is worthless. Both arms run on a one-week slice so the whole
check costs minutes.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.gold import build_gold
from psid.methods import Issue, StreamConfig, pair_features
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs
from experiments.e11d_gbdt import GBT
from experiments.e26_decision_timing import DeferralClusterer, TAU

OUT = Path(__file__).resolve().parents[1] / "results"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class PreSeededClusterer(DeferralClusterer):
    """Deliberately violates output-prefix-safety by indexing the whole window.

    Every record is present in the retrieval index from the start, so a record
    arriving early can find and merge into a record that has not arrived yet. This
    is the canonical "cluster the corpus, then read clusters off in time order"
    leak, and it reads the future record's *address*, which is exactly what the
    audit perturbs -- so if the audit has any power at all it must fail here.

    The record's own pre-created issue is removed when it arrives, otherwise every
    arrival would simply match itself.
    """

    def preseed(self, records):
        self._pre = {}
        for n, r in enumerate(records):
            self._counter += 1
            iid = f"S{self._counter:07d}"
            iv = Issue(iid=iid, created=r.day, last=r.day)
            iv.absorb(r)
            self.index.add(iv, r, self._tokens(r.rid, r))
            self.issue_of[iid] = iv
            self.by_rid[r.rid] = r
            self._pre[r.rid] = iid

    def on_record(self, rec):
        own = self._pre.pop(rec.rid, None)
        if own is not None:
            self.index.issues.pop(own, None)
            self.issue_of.pop(own, None)
        return super().on_record(rec)


def perturb(rec, donor):
    """Replace one record's address and description with a donor's."""
    return dataclasses.replace(rec, address_raw=donor.address_raw,
                               zipcode=donor.zipcode,
                               description=donor.description,
                               _addr=None, _text=None)


def perturb_suffix(records, s, salt=0):
    """Replace the address and text of every record after position s.

    A single-record perturbation proved too weak a probe: the leak is about which
    population is available to be retrieved, and changing one record rarely changes
    any earlier decision. Replacing the whole future is the right probe -- a
    prefix-safe pipeline's output before s cannot depend on it at all.
    """
    tail = records[s:]
    donors = tail[1:] + tail[:1]
    if salt:
        donors = donors[salt:] + donors[:salt]
    return list(records[:s]) + [perturb(r, d) for r, d in zip(tail, donors)]


def replay(cl, records, perturb_at=None, replacement=None):
    """Run a clusterer, returning its commit log and per-position emit prefix."""
    prefix = []
    for i, r in enumerate(records):
        prefix.append(len(cl.merge_log))
        cl.on_record(replacement if (perturb_at is not None and i == perturb_at) else r)
    cl.finish(int(records[-1].day))
    return cl, prefix


def make(model, delta, deadline, peeking, records=None):
    cfg = StreamConfig(t_merge=float(TAU), t_pending=0.0, window_days=60.0,
                       k_candidates=60, max_cluster=20)
    if peeking:
        cl = PreSeededClusterer(cfg, model, delta=delta, deadline_days=deadline,
                                visible=True)
        cl.preseed(records)
        return cl
    return DeferralClusterer(cfg, model, delta=delta, deadline_days=deadline,
                             visible=True)


def replay_peeking(cl, records):
    prefix = []
    for i, r in enumerate(records):
        prefix.append(len(cl.merge_log))
        cl.on_record(r)
    cl.finish(int(records[-1].day))
    return cl, prefix


def audit(model, records, indices, peeking=False, delta=0.5, deadline=7.0):
    runner = replay_peeking if peeking else replay
    base, prefix = runner(make(model, delta, deadline, peeking, records), records)
    base_log = list(base.merge_log)
    changes = []
    for s in indices:
        perturbed = perturb_suffix(records, s, salt=s % 17)
        arm, _ = runner(make(model, delta, deadline, peeking, perturbed), perturbed)
        n = prefix[s]
        head_a, head_b = base_log[:n], list(arm.merge_log)[:n]
        diff = sum(1 for a, b in zip(head_a, head_b) if a != b) + abs(len(head_a) - len(head_b))
        changes.append({"index": s, "committed_before": n, "changed": diff})
        log(f"    perturb s={s:5d}  decisions before s={n:5d}  changed={diff}")
    total = sum(c["changed"] for c in changes)
    return {"peeking": peeking, "indices": indices, "detections": changes,
            "total_changed": total, "passed": total == 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "e28_prefix_audit.json"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    tr = subset_records(rec, "2024-01-01", "2024-02-29")
    end = f"2024-05-{args.days:02d}"
    cf = subset_records(rec, "2024-05-01", end)
    log(f"audit window 2024-05-01..{end}: {len(cf):,} records")

    t0 = time.time()
    X, y = build_candidate_pairs(tr, gold, max_records=22000)
    model = GBT(X, y, seed=args.seed)
    log(f"scorer fitted ({time.time()-t0:.0f}s)")

    rng = random.Random(7)
    indices = sorted(rng.sample(range(len(cf) // 10, len(cf) - 10), 3))
    log(f"perturbation indices {indices}")

    log("== honest policy (must pass) ==")
    honest = audit(model, cf, indices)
    log("== peeking control (must fail) ==")
    leaky = audit(model, cf, indices, peeking=True)

    payload = {"window": ["2024-05-01", end], "delta": 0.5, "deadline": 7.0,
               "tau": TAU, "honest": honest, "peeking_control": leaky}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    log(f"honest passed={honest['passed']} changed={honest['total_changed']}")
    log(f"peeking passed={leaky['passed']} changed={leaky['total_changed']} "
        f"-> audit has power: {not leaky['passed']}")
    log(f"wrote {Path(args.out).name}")


if __name__ == "__main__":
    main()
