"""E29: an off-the-shelf linker as an external comparator.

Every contrast in this project so far is between artefacts we built, which is the
first thing a reviewer attacks. This runs a published implementation of the classic
Fellegi-Sunter model -- `recordlinkage` 0.16 (de Bruin, the Python Record Linkage
Toolkit) -- on exactly the same candidate pairs, the same adjudicated domain and the
same window as the streaming engine.

Fairness choices, stated because they decide the result:

  * Same candidates.  The external model sees the pairs our blocking proposes, so the
    comparison isolates matcher quality from blocking quality.  Its recall ceiling is
    therefore ours (0.688) by construction, and its absolute recall is not comparable
    to a system that does its own blocking.
  * Its own features and its own fitting.  The comparison vector uses standard
    Fellegi-Sunter agreement fields (service, exact address, block, street, zip,
    description similarity in three bins, within-a-week, address missing) and its
    m/u probabilities are estimated by its own unsupervised EM.  We supply no labels
    to it.
  * Its own score.  We read the fitted log(m/u) weights back out and score each pair
    with them; the only thing we choose is the threshold, which is what we also choose
    for our own engine.  Its native `predict` decision is reported too.

The external method produces per-pair decisions, not a stream of irreversible merges,
so its natural deployment is connected components over the accepted pairs. That is
what we evaluate, and it is the same construction as the batch arm of E25.
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
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.decision_metrics import decision_fmp
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs_with_ids
from psid.methods import FEATURE_NAMES

OUT = Path(__file__).resolve().parents[1] / "results"
IX = {n: i for i, n in enumerate(FEATURE_NAMES)}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        p = self.p.setdefault(x, x)
        while p != self.p[p]:
            self.p[p] = self.p[self.p[p]]
            p = self.p[p]
        self.p[x] = p
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.p[rb] = ra
            else:
                self.p[ra] = rb


def components(edges, ids, by):
    uf = UF()
    for a, b in edges:
        uf.union(a, b)
    comps = {}
    for rid in ids:
        comps.setdefault(uf.find(rid), []).append(rid)
    part = {rid: str(root) for root, mem in comps.items() for rid in mem}
    decisions = []
    for mem in comps.values():
        if len(mem) < 2:
            continue
        rep = min(mem, key=lambda r: (by[r].when, r))
        decisions.extend((rid, rep) for rid in mem if rid != rep)
    return part, decisions, max((len(m) for m in comps.values()), default=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "e29_external_baseline.json"))
    ap.add_argument("--max-records", type=int, default=40000)
    args = ap.parse_args()

    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    cf = subset_records(rec, "2024-05-01", "2024-05-31")
    ids = {r.rid for r in cf}
    by = {r.rid: r for r in cf}
    log(f"May window {len(cf):,} records")

    t0 = time.time()
    X, y, pids = build_candidate_pairs_with_ids(cf, gold, max_records=args.max_records)
    log(f"candidate graph {X.shape} pos={int(y.sum())} ({time.time()-t0:.0f}s)")

    # ---- standard Fellegi-Sunter comparison vector -------------------------
    def col(name):
        return X[:, IX[name]].astype(np.int8)

    desc = X[:, IX["jac"]]
    # recordlinkage's ECM initialises from binary agreement patterns, so every
    # comparison field is binarised; a description is "agreeing" above 0.15 Jaccard.
    desc_agree = (desc >= 0.15).astype(np.int8)
    cmp_df = pd.DataFrame({
        "same_service": col("same_service"),
        "same_addr": col("same_addr"),
        "same_block": col("same_block"),
        "same_street": col("same_street"),
        "same_zip": col("same_zip"),
        "addr_missing": col("addr_missing"),
        "gap_le_7": col("gap_le_7"),
        "desc_agree": desc_agree,
    })
    cmp_df.index = pd.MultiIndex.from_arrays([np.arange(len(pids)), np.arange(len(pids))])
    log(f"comparison vector {cmp_df.shape}, levels "
        f"{ {c: int(cmp_df[c].nunique()) for c in cmp_df} }")

    from recordlinkage.classifiers import ECMClassifier
    t0 = time.time()
    ecm = ECMClassifier()
    ecm.fit(cmp_df)
    log(f"ECM fitted unsupervised in {time.time()-t0:.0f}s")

    # score each pair with the model's own fitted log(m/u) weights
    lw = ecm.log_weights
    if isinstance(lw, list):
        lw = lw[0]
    score = np.zeros(len(cmp_df), dtype=np.float64)
    for field in cmp_df.columns:
        table = lw[field]
        score += cmp_df[field].map(table).to_numpy(dtype=np.float64)
    log(f"FS score range [{score.min():.2f}, {score.max():.2f}]")

    # its own native decision, for reference
    native = ecm.predict(cmp_df)
    native_pairs = set((int(a), int(b)) for a, b in native)
    log(f"native predict returns {len(native_pairs):,} pairs")

    # ---- evaluate at a threshold grid --------------------------------------
    order = np.argsort(-score, kind="stable")
    rows = []
    for q in (0.9995, 0.999, 0.995, 0.99, 0.98, 0.95, 0.90):
        cut = int(len(order) * (1 - q))
        keep = order[:cut]
        edges = [(pids[i][0], pids[i][1]) for i in keep]
        part, dec, maxc = components(edges, ids, by)
        ev = evaluate_partition(part, gold, ids)
        d = decision_fmp(dec, gold)
        row = {"kept_share": round(cut / len(order), 5), "edges": len(edges),
               "pair_precision": ev["precision"], "pair_recall": ev["recall"],
               "pair_f1": ev["f1"], "dec_fmp": d["dec_fmp"],
               "decisions": d["decisions"], "max_component": maxc,
               "fs_threshold": round(float(score[keep[-1]]), 3) if cut else None}
        rows.append(row)
        log(f"keep top {q:>7.4f} | edges={len(edges):6d} tau_frac={row['fs_threshold']} "
            f"| P={ev['precision']:.3f} R={ev['recall']:.3f} F1={ev['f1']:.3f} "
            f"FMP={d['dec_fmp']:.3f} max={maxc}")

    best = max(rows, key=lambda r: (r["pair_f1"] if r["pair_f1"] == r["pair_f1"] else -1))
    log(f"best external point: F1={best['pair_f1']:.3f} at keep={best['kept_share']}")

    Path(args.out).write_text(json.dumps({
        "city": "san_diego", "test": ["2024-05-01", "2024-05-31"],
        "library": "recordlinkage 0.16 ECMClassifier (unsupervised)",
        "n_candidates": len(pids), "positives_in_pairs": int(y.sum()),
        "native_pairs": len(native_pairs), "rows": rows, "best": best,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {Path(args.out).name}")


if __name__ == "__main__":
    main()
