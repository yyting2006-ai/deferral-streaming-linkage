"""E27: the retraction baseline --- is deferral just a worse version of revising?

The risk for E26 is that delaying a commit looks clever only because we never let
the system *undo* a commit.  A stream processor with allowed lateness or an
updatable index would emit immediately and repair later, which is a strictly
weaker requirement than making the caller wait.  If that baseline matches or beats
deferral, then deferral is dominated wherever retraction is possible, and the
algorithm is only interesting in domains where a merge has an irreversible side
effect (a crew dispatched to the wrong address cannot be un-dispatched).

So this experiment gives retraction its best shot at the same budget:

  * decide at arrival exactly as deployed;
  * `D` days after committing a merge, revisit the record once and, if some other
    open issue now scores at least tau for it, move it there.

The one-shot revisit keeps the total work O(N) --- the same order as deferral ---
so the comparison is at equal effort, not equal cleverness.  Moving a record out of
an issue is the operation the deployed engine cannot perform, which is precisely
what we are testing the value of.

Reading the result:
  revision >> deferral   -> deferral is a degraded retraction; narrow the claim to
                            irrevocable-decision settings or drop the idea.
  revision ~= deferral   -> deferral buys the same quality without retraction.
                            That is the useful claim.
  revision <  deferral   -> retraction is not free (it fragments clusters); report
                            it, and the deferral story gets stronger.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.decision_metrics import decision_fmp
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.methods import (MERGE, PrefixClusterer, StreamConfig, _char3,
                          pair_features)
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs
from experiments.e11d_gbdt import GBT

OUT = Path(__file__).resolve().parents[1] / "results"
TAU = 0.0358


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class RevisionClusterer(PrefixClusterer):
    """Deployed engine, plus one retraction attempt per merge, D days later."""

    def __init__(self, cfg: StreamConfig, scorer, revisit_days: float):
        super().__init__(cfg, scorer)
        self.revisit_days = float(revisit_days)
        self.final_rep = {}
        self.schedule = []
        self.n_moved = 0
        self.move_latencies = []

    # -- scoring (mirrors the deferral arm so the two are comparable) ---------
    def _best_other(self, rec, exclude):
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        cand = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                     use_text=self.cfg.use_text)
        usable = []
        for iid in cand:
            if iid == exclude:
                continue
            issue = self.index.issues.get(iid)
            if issue is None or issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid.get(issue.reps[0])
            if rep is not None:
                usable.append((issue, rep))
        if not usable:
            return None, -1.0
        feats = np.stack([pair_features(rec, tokens, char3, iss, rep,
                                        self._tokens(rep.rid, rep),
                                        self._char3(rep.rid, rep))
                          for iss, rep in usable])
        scores = self.scorer(feats)
        k = int(np.argmax(scores))
        return usable[k][0], float(scores[k])

    def _detach(self, issue, rid):
        issue.members.remove(rid)
        issue.n -= 1
        if rid in issue.reps:
            issue.reps.remove(rid)
            for m in sorted(issue.members, key=lambda x: self.by_rid[x].day):
                if len(issue.reps) >= 3:
                    break
                issue.reps.append(m)
        alive = sorted(issue.members, key=lambda x: self.by_rid[x].day)
        issue.last = self.by_rid[alive[-1]].day if alive else issue.created
        if not issue.members:
            self.index.issues.pop(issue.iid, None)
            self.issue_of.pop(issue.iid, None)

    def _advance(self, day):
        if day == self._cur_day:
            return
        while self.schedule and self.schedule[0][0] <= day:
            due, rid = heapq.heappop(self.schedule)
            cur = self.assign.get(rid)
            issue = self.issue_of.get(cur) if cur else None
            rec = self.by_rid.get(rid)
            if issue is None or rec is None:
                continue
            target, score = self._best_other(rec, exclude=issue.iid)
            if target is not None and score >= self.cfg.t_merge:
                self._detach(issue, rid)
                target.absorb(rec)
                self.assign[rid] = target.iid
                self.final_rep[rid] = target.reps[0]
                self.n_moved += 1
                self.move_latencies.append(day - int(rec.day))
        self._cur_day = day
        self.index.prune(day)

    def on_record(self, rec):
        self._advance(int(rec.day))
        action, iid, _score, _pairs = super().on_record(rec)
        if action == MERGE:
            issue = self.issue_of.get(iid)
            if issue is not None and issue.reps:
                self.final_rep[rec.rid] = issue.reps[0]
                heapq.heappush(self.schedule,
                               (int(rec.day) + int(self.revisit_days), rec.rid))
        return action

    def final_decisions(self):
        """(record, representative) after every retraction has been applied.

        The name must not be `decisions`: PrefixClusterer stores a list under that
        attribute, and an instance attribute shadows a method of the same name.
        """
        return [(rid, rep) for rid, rep in self.final_rep.items()]


def run(model, records, gold, revisit_days):
    cfg = StreamConfig(t_merge=TAU, t_pending=0.0, window_days=60.0,
                       k_candidates=60, max_cluster=20)
    cl = RevisionClusterer(cfg, model, revisit_days=revisit_days)
    t0 = time.perf_counter()
    for r in records:
        cl.on_record(r)
    cl._advance(int(records[-1].day) + int(revisit_days) + 1)
    elapsed = time.perf_counter() - t0
    ids = {r.rid for r in records}
    ev = evaluate_partition(cl.partition(), gold, ids)
    d = decision_fmp(cl.final_decisions(), gold)
    return {
        "policy": "revision", "revisit_days": float(revisit_days),
        "pair_precision": ev["precision"], "pair_recall": ev["recall"],
        "pair_f1": ev["f1"], "dec_fmp": d["dec_fmp"],
        "decisions": d["decisions"], "dec_coverage": d["dec_coverage"],
        "max_pred_cluster": ev["max_pred_cluster"],
        "n_moved": cl.n_moved, "move_share": round(cl.n_moved / max(1, len(records)), 4),
        "move_latency_mean": (round(float(np.mean(cl.move_latencies)), 2)
                              if cl.move_latencies else 0.0),
        "ms_per_record": round(1000.0 * elapsed / max(1, len(records)), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="may", choices=("may", "april"))
    ap.add_argument("--revisits", default="7")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--out", default=str(OUT / "e27_retraction.json"))
    args = ap.parse_args()

    WINDOWS = {"may": ("2024-05-01", "2024-05-31"),
               "april": ("2024-04-01", "2024-04-30")}
    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    tr = subset_records(rec, "2024-01-01", "2024-02-29")
    w0, w1 = WINDOWS[args.window]
    cf = subset_records(rec, w0, w1)
    log(f"{args.window} window {len(cf):,} records")

    t0 = time.time()
    X, y = build_candidate_pairs(tr, gold, max_records=22000)
    model = GBT(X, y, seed=args.seed)
    log(f"scorer fitted ({time.time()-t0:.0f}s)")

    rows = []
    for rv in [float(v) for v in args.revisits.split(",")]:
        r = run(model, cf, gold, rv)
        rows.append(r)
        log(f"revision D={rv:>4} | F1={r['pair_f1']:.4f} R={r['pair_recall']:.4f} "
            f"P={r['pair_precision']:.4f} decFMP={r['dec_fmp']:.3f} "
            f"moved={r['move_share']:.3f} lat={r['move_latency_mean']:.1f}d "
            f"{r['ms_per_record']:.2f}ms")

    Path(args.out).write_text(json.dumps(
        {"city": "san_diego", "window": args.window, "test": [w0, w1],
         "tau": TAU, "seed": args.seed, "rows": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {Path(args.out).name}")


if __name__ == "__main__":
    main()
