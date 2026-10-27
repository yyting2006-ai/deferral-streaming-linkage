"""E31: an end-to-end external linker, blocking included.

The comparison in the paper is weak in one specific way: the Fellegi-Sunter model in
E29 is fed *our* candidate pairs, so it cannot do worse than our blocking. This runs a
published system end to end --- Splink 5.0, which does its own blocking, its own
parameter estimation and its own clustering --- on the raw records of the same month,
and scores it on the same adjudicated domain.

Fairness choices, stated because they decide the result:

  * Raw records, its own blocking.  Splink sees service, address number, street,
    hundred-block, zip, description and date, and chooses its own candidate pairs from
    blocking rules we give it. Its recall ceiling is therefore its own, not ours, which
    is the point of the experiment.
  * Its own parameters.  m and u are estimated by its EM from the data, with no labels.
    We only choose the match-probability threshold, exactly as we choose our own.
  * Same scoring.  Its clusters are projected onto the same adjudicated domain with the
    same pairwise and decision-level measures.

What would change our conclusions: if it lands near or above our engine, the claim that
the pipeline is strong is dead and both papers need rewriting. If it lands far below,
the external-validity objection is answered for the engine but not for the mechanism.
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

from psid.data import load_chicago, load_san_diego
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.decision_metrics import decision_fmp
from psid.replay import subset_records

OUT = Path(__file__).resolve().parents[1] / "results"

from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def frame(records):
    rows = []
    for r in records:
        a = r.addr
        rows.append({
            "unique_id": r.rid,
            "service": (r.service or "").lower().strip() or "unknown",
            "addr_number": a.number or "none",
            "addr_street": a.street or "none",
            "block": a.block or "none",
            "zip": a.zip5 or "none",
            "description": r.text or "none",
            "date": r.when.strftime("%Y-%m-%d"),
            "day": r.day,
        })
    return pd.DataFrame(rows)


def clusters_to_decisions(assign, by):
    """Turn spink cluster ids into (partition, decisions) in our convention."""
    comps = {}
    for rid, cid in assign.items():
        comps.setdefault(cid, []).append(rid)
    decisions = []
    for mem in comps.values():
        if len(mem) < 2:
            continue
        rep = min(mem, key=lambda r: (by[r].when, r))
        decisions.extend((rid, rep) for rid in mem if rid != rep)
    return {rid: str(c) for rid, c in assign.items()}, decisions


def capped_components(pairs, cap=20):
    """Agglomerate pairs in descending score under a hard component-size ceiling.

    Splink's own clustering is connected components, which on this stream produces a
    component of 151 requests and drives precision to 0.14. Our engine carries a
    ceiling of 20, so comparing an unconstrained external system against a constrained
    one measures the ceiling, not the matcher. This arm applies our ceiling to Splink's
    own predicted pairs, so that the only difference left is whose scorer produced
    them.
    """
    parent = {}
    size = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, s in pairs:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        size.setdefault(ra, 1)
        size.setdefault(rb, 1)
        if size[ra] + size[rb] > cap:
            continue
        if ra < rb:
            parent[rb] = ra
            size[ra] = size[ra] + size[rb]
        else:
            parent[ra] = rb
            size[rb] = size[ra] + size[rb]
    assign = {}
    for x in parent:
        assign[x] = find(x)
    return assign


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="sandiego", choices=("sandiego", "chicago"))
    ap.add_argument("--start", default="2024-05-01")
    ap.add_argument("--end", default="2024-05-31")
    ap.add_argument("--u-pairs", type=float, default=2_000_000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.city == "sandiego":
        rec, links = load_san_diego()
        gold = build_gold("san_diego", rec, links)
    else:
        rec, links = load_chicago()
        gold = build_gold("chicago", rec, links)
        args.start, args.end = "2026-03-01", "2026-03-14"
    cf = subset_records(rec, args.start, args.end)
    by = {r.rid: r for r in cf}
    ids = set(by)
    log(f"{args.city} {args.start}..{args.end}: {len(cf):,} records")

    df = frame(cf)
    log(f"frame {df.shape}; services={df['service'].nunique()} "
        f"blocks={df['block'].nunique()} empty_desc={(df['description']=='none').mean():.2f}")

    db_api = DuckDBAPI()
    sdf = db_api.register(df)
    settings = SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            cl.ExactMatch("service"),
            cl.ExactMatch("addr_number"),
            cl.JaroWinklerAtThresholds("addr_street", [0.9, 0.7]),
            cl.ExactMatch("block"),
            cl.JaroWinklerAtThresholds("description", [0.8, 0.5]),
            cl.AbsoluteDateDifferenceAtThresholds(
                "date", input_is_string=True,
                metrics=["day", "day", "day"], thresholds=[1, 7, 30]),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("block"),
            block_on("addr_street"),
            block_on("service", "zip"),
        ],
    )
    linker = Linker(sdf, settings)
    t0 = time.time()
    linker.training.estimate_u_using_random_sampling(max_pairs=args.u_pairs)
    log(f"u estimated ({time.time()-t0:.0f}s)")
    t0 = time.time()
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("block"))
    log(f"EM done ({time.time()-t0:.0f}s)")

    t0 = time.time()
    pred = linker.inference.predict(threshold_match_probability=0.10)
    pred_df = pred.as_pandas_dataframe()
    log(f"predicted {len(pred_df):,} pairs above 0.10 ({time.time()-t0:.0f}s)")

    rows = []
    for thr in (0.99, 0.95, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.10):
        t0 = time.time()
        clu = linker.clustering.cluster_pairwise_predictions_at_threshold(
            pred, threshold_match_probability=thr)
        cdf = clu.as_pandas_dataframe()
        assign = dict(zip(cdf["unique_id"].astype(str), cdf["cluster_id"].astype(str)))
        assign = {k: v for k, v in assign.items() if k in ids}
        part, dec = clusters_to_decisions(assign, by)
        ev = evaluate_partition(part, gold, ids)
        d = decision_fmp(dec, gold)
        row = {"threshold": thr, "clusters": int(cdf["cluster_id"].nunique()),
               "merged_records": sum(1 for r in dec),
               "pair_precision": ev["precision"], "pair_recall": ev["recall"],
               "pair_f1": ev["f1"], "max_pred_cluster": ev["max_pred_cluster"],
               "dec_fmp": d["dec_fmp"], "decisions": d["decisions"],
               "dec_coverage": d["dec_coverage"],
               "seconds": round(time.time() - t0, 1)}
        rows.append(row)
        log(f"thr={thr:>4.2f} native | P={ev['precision']:.3f} R={ev['recall']:.3f} "
            f"F1={ev['f1']:.3f} FMP={d['dec_fmp']:.3f} max={ev['max_pred_cluster']}")

        sub = pred_df[pred_df["match_probability"] >= thr]
        pairs = [(str(x), str(y), float(s)) for x, y, s in
                 zip(sub["unique_id_l"], sub["unique_id_r"], sub["match_probability"])
                 if str(x) in ids and str(y) in ids]
        pairs.sort(key=lambda q: -q[2])
        cassign = capped_components(pairs, cap=20)
        for rid in ids:
            cassign.setdefault(rid, "solo_" + rid)
        cpart, cdec = clusters_to_decisions(cassign, by)
        cev = evaluate_partition(cpart, gold, ids)
        cd = decision_fmp(cdec, gold)
        rows.append({"threshold": thr, "arm": "capped",
                     "pair_precision": cev["precision"], "pair_recall": cev["recall"],
                     "pair_f1": cev["f1"], "max_pred_cluster": cev["max_pred_cluster"],
                     "dec_fmp": cd["dec_fmp"], "decisions": cd["decisions"],
                     "dec_coverage": cd["dec_coverage"]})
        log(f"thr={thr:>4.2f} capped | P={cev['precision']:.3f} R={cev['recall']:.3f} "
            f"F1={cev['f1']:.3f} FMP={cd['dec_fmp']:.3f} "
            f"max={cev['max_pred_cluster']}")

    native = [r for r in rows if r.get("arm") != "capped"]
    capped = [r for r in rows if r.get("arm") == "capped"]
    best = max(native, key=lambda r: r["pair_f1"])
    best_capped = max(capped, key=lambda r: r["pair_f1"]) if capped else None
    if best_capped:
        log(f"best capped F1={best_capped['pair_f1']:.4f} at thr={best_capped['threshold']}")
    log(f"best F1={best['pair_f1']:.4f} at thr={best['threshold']}")
    out = Path(args.out or (OUT / f"e31_splink_{args.city}.json"))
    out.write_text(json.dumps({
        "city": args.city, "test": [args.start, args.end],
        "n_records": len(cf), "library": "splink",
        "blocking": ["block", "addr_street", "service+zip"],
        "pairs_above_0.10": int(len(pred_df)),
        "rows": rows, "best": best, "best_capped": best_capped,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out.name}")


if __name__ == "__main__":
    main()
