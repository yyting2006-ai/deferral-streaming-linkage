"""E25: a real batch baseline --- global clustering, not a pre-seeded index.

The earlier "future-visible" arm is a streaming algorithm with a pre-seeded index,
and a reviewer is entitled to call that a straw man: it never clusters globally.
This experiment builds the actual batch pipeline over the same May window --- score
every candidate pair at once, keep the pairs above tau, cluster the resulting graph
--- and puts it next to the deployed streaming engine at the same tau with the same
scorer.

Four arms share the record universe, the scorer and the threshold:

  batch          connected components of the full replay candidate graph whose
                 score is >= tau.  No arrival order, no pruning, no ceiling, no
                 irreversibility.
  batch_capped   the same graph, agglomerated by descending score with the same
                 cluster ceiling the streaming engine enforces (20).  Without this
                 arm the comparison is confounded: an uncapped global graph is
                 guaranteed to form blobs, so "batch is worse" would say nothing
                 about the protocol.
  stream         PrefixClusterer, window 60, at tau.  The deployed prefix-safe engine.
  strict_batch   connected components of exactly the edges the stream itself scored
                 at that tau (its top-1 candidate per arrival), so the candidate set
                 is identical by construction and the only difference is the
                 acceptance rule: global closure versus irreversible attachment.

`strict_batch` exists because `batch` and `stream` do not draw candidates from the
same source --- the batch graph comes from the replay generator, the stream from its
own incremental index --- so a difference between them mixes two causes.  With
`strict_batch` the candidate set is held fixed and only the acceptance rule moves.

A note on what the window means here.  May spans 31 days and the index window is 60,
so no issue is ever pruned inside the evaluation window: the streaming arm has, in
effect, unbounded memory for this comparison.  Any gap between the arms is therefore
not a memory effect --- which E22 already showed is null --- but the effect of being
able to chain through pairs that a later record will create.

The failure mode this experiment is designed to expose is the one theory predicts:
global closure can bridge through a single false edge and weld two large components
together, which is why the maximum component size is reported next to F1.  If batch
F1 is higher and its false-merge proportion lower, global clustering is simply
better here and section 6 has to say so; if the false-merge proportion explodes with
component size, the irreversibility of the stream is a protection rather than a cost.
"""
from __future__ import annotations

import argparse
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
from psid.methods import PrefixClusterer, StreamConfig
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs, build_candidate_pairs_with_ids
from experiments.e11d_gbdt import GBT

OUT = Path(__file__).resolve().parents[1] / "results"
TAUS = (0.5, 0.2, 0.05, 0.02)
WINDOW = 60.0
K_CAND = 60
CAP = 20


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class UF:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        p = self.parent.setdefault(x, x)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[x] = p
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def as_partition(uf, ids, by):
    """Partition from a union-find, plus the decisions the stream would have made.

    The decision log mirrors the streaming contract: every non-representative
    record is scored against the earliest record of its component.
    """
    comps = {}
    for rid in ids:
        comps.setdefault(uf.find(rid), []).append(rid)
    partition = {rid: str(root) for root, mem in comps.items() for rid in mem}
    decisions = []
    for mem in comps.values():
        if len(mem) < 2:
            continue
        rep = min(mem, key=lambda r: (by[r].when, r))
        decisions.extend((rid, rep) for rid in mem if rid != rep)
    sizes = [len(m) for m in comps.values()]
    return partition, decisions, {
        "n_components": len(comps),
        "max_component": max(sizes) if sizes else 0,
        "n_merged_records": sum(s for s in sizes if s > 1),
    }


def cc_uncapped(pairs, ids, by):
    uf = UF()
    for a, b in pairs:
        uf.union(a, b)
    return as_partition(uf, ids, by)


def cc_capped(ordered_pairs, ids, by, cap=CAP):
    """Agglomerate in descending score, refusing any union that breaks the ceiling.

    This is the batch analogue of `max_cluster`: it keeps every decision the global
    graph supports but forbids the blob.  Comparing it against `batch` separates
    "global information" from "no ceiling".
    """
    uf = UF()
    size = {}
    for a, b in ordered_pairs:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        sa, sb = size.get(ra, 1), size.get(rb, 1)
        if sa + sb > cap:
            continue
        uf.union(a, b)
        size[uf.find(a)] = sa + sb
    return as_partition(uf, ids, by)


def score_arm(partition, decisions, extra, gold, ids):
    ev = evaluate_partition(partition, gold, ids)
    d = decision_fmp(decisions, gold)
    out = {"pair_precision": ev["precision"], "pair_recall": ev["recall"],
           "pair_f1": ev["f1"], "n_pos": ev["n_pos"],
           "max_pred_cluster": ev["max_pred_cluster"],
           "dec_fmp": d["dec_fmp"], "decisions": d["decisions"],
           "dec_precision": d["dec_precision"], "dec_coverage": d["dec_coverage"]}
    out.update(extra)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "e25_batch_global.json"))
    ap.add_argument("--max-records", type=int, default=40000)
    ap.add_argument("--cap", type=int, default=CAP)
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any partial result file and start over")
    args = ap.parse_args()
    cap = args.cap

    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    tr = subset_records(rec, "2024-01-01", "2024-02-29")
    cf = subset_records(rec, "2024-05-01", "2024-05-31")
    ids = {r.rid for r in cf}
    by = {r.rid: r for r in cf}
    log(f"May window {len(cf):,} records")

    t0 = time.time()
    Xt, yt = build_candidate_pairs(tr, gold, max_records=22000)
    model = GBT(Xt, yt)
    log(f"scorer fitted on {Xt.shape} pos={int(yt.sum())} ({time.time()-t0:.0f}s)")

    t0 = time.time()
    Xc, yc, pids = build_candidate_pairs_with_ids(cf, gold,
                                                  max_records=args.max_records)
    scores = np.asarray(model(Xc))
    log(f"candidate graph {Xc.shape} pos={int(yc.sum())} "
        f"({time.time()-t0:.0f}s)")

    # ---- the candidates must describe the same universe ----------------------
    bad = [p for p in pids if p[0] not in ids or p[1] not in ids]
    assert not bad, f"{len(bad)} candidate pairs touch records outside the window"
    assert len(pids) == len(Xc) == len(scores)
    log(f"graph checks passed: all {len(pids):,} pairs lie inside the "
        f"{len(ids):,}-record window")

    # descending score order is tau-independent, so sort once
    order = np.argsort(-scores, kind="stable")
    log("edges sorted by score")

    rows = []
    out_path = Path(args.out)
    rows = []
    if out_path.exists() and not args.fresh:
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            if prev.get("n_records") == len(ids) and prev.get("ceiling") == cap:
                rows = prev.get("rows", [])
                log(f"resuming: {len(rows)} tau rows already on disk")
        except (ValueError, KeyError, OSError):
            rows = []
    done = {r["tau"] for r in rows}

    def checkpoint():
        payload = {"city": "san_diego", "test": ["2024-05-01", "2024-05-31"],
                   "taus": list(TAUS), "window_days": WINDOW,
                   "k_candidates": K_CAND, "ceiling": cap, "n_records": len(ids),
                   "batch_candidate_pairs": len(pids), "rows": rows}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return payload

    for tau in TAUS:
        if tau in done:
            log(f"tau={tau} already on disk, skipping")
            continue
        t1 = time.time()
        keep = [int(i) for i in order if scores[i] >= tau]
        batch_edges = [(pids[i][0], pids[i][1]) for i in keep]
        part_b, dec_b, extra_b = cc_uncapped(batch_edges, ids, by)
        batch = score_arm(part_b, dec_b, extra_b, gold, ids)
        batch["edges_accepted"] = len(batch_edges)

        part_c, dec_c, extra_c = cc_capped(batch_edges, ids, by, cap=cap)
        capped = score_arm(part_c, dec_c, extra_c, gold, ids)
        capped["edges_accepted"] = len(batch_edges)
        capped["ceiling"] = cap

        # same tau, deployed engine
        cl = PrefixClusterer(StreamConfig(t_merge=float(tau), t_pending=0.0,
                                          window_days=WINDOW, k_candidates=K_CAND,
                                          max_cluster=cap), model)
        for r in cf:
            cl.on_record(r)
        stream = score_arm(cl.partition(), cl.merge_log,
                           {"edges_accepted": len(cl.merge_log)}, gold, ids)
        stream["ceiling"] = cap

        # same tau, same candidate list as the engine itself produced
        strict_edges = [(rid, rep) for rid, rep, sc in cl.top_log if sc >= tau]
        part_s, dec_s, extra_s = cc_uncapped(strict_edges, ids, by)
        strict = score_arm(part_s, dec_s, extra_s, gold, ids)
        strict["edges_accepted"] = len(strict_edges)

        row = {"tau": tau, "batch": batch, "batch_capped": capped,
               "stream": stream, "strict_batch": strict,
               "matched": {"same_scorer": True, "same_tau": True,
                           "same_window_days": WINDOW, "same_k_candidates": K_CAND,
                           "same_ceiling": cap, "n_records": len(ids),
                           "stream_scored_pairs": len(cl.top_log),
                           "batch_candidate_pairs": len(pids),
                           "candidate_sets_identical": False,
                           "strict_batch_candidates_identical": True,
                           "seconds": round(time.time() - t1, 1)}}
        rows.append(row)
        checkpoint()
        for arm in ("batch", "batch_capped", "stream", "strict_batch"):
            r = row[arm]
            mc = r.get("max_component", r.get("max_pred_cluster", 0))
            log(f"tau={tau:<5} {arm:12s} F1={r['pair_f1']:.3f} "
                f"P={r['pair_precision']:.3f} R={r['pair_recall']:.3f} "
                f"FMP={r['dec_fmp']:.3f} merges={r['edges_accepted']:5d} "
                f"max={mc:5d}")

    checkpoint()
    log(f"wrote {out_path.name}")


if __name__ == "__main__":
    main()
