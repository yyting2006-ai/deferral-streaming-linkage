"""E32: Splink, supervised, end to end.

E31 compared our engine against Splink's *unsupervised* EM, which is not a like-for-like
comparison: our scorer is fitted on labelled candidate pairs from January--February,
while Splink estimates its m probabilities from the data itself. A reviewer would say,
correctly, that most of the gap is supervision.

This gives Splink the same labels we give ourselves --- the January--February candidate
pairs with the city's gold --- through its own `estimate_m_from_pairwise_labels`, and
then runs exactly the E31 protocol: its own blocking, its own u, its own clustering
(and, for a fair ceiling, our agglomeration as a second arm).

If the supervised external system matches our engine, then our engine has no
distinctive value and both papers must be rewritten.
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
from psid.train_cand import build_candidate_pairs_with_ids

from experiments.e31_splink_end2end import (
    capped_components, clusters_to_decisions, frame,
)

OUT = Path(__file__).resolve().parents[1] / "results"

from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="sandiego", choices=("sandiego", "chicago"))
    ap.add_argument("--train-pairs", type=int, default=300_000)
    ap.add_argument("--out", default=str(OUT / "e32_splink_supervised.json"))
    ap.add_argument("--set-prior", action="store_true",
                    help="set probability_two_random_records_match from the label prevalence instead of the 1e-4 default")
    ap.add_argument("--u-pairs", type=float, default=2_000_000)
    ap.add_argument("--prior", type=float, default=None,
                    help="explicit probability_two_random_records_match; overrides "
                         "--set-prior. The default 1e-4 and the candidate-pair "
                         "prevalence are both wrong for different reasons.")
    args = ap.parse_args()

    if args.city == "sandiego":
        rec, links = load_san_diego()
        gold = build_gold("san_diego", rec, links)
        tr = subset_records(rec, "2024-01-01", "2024-02-29")
        te = subset_records(rec, "2024-05-01", "2024-05-31")
        window = ["2024-05-01", "2024-05-31"]
    else:
        rec, links = load_chicago()
        gold = build_gold("chicago", rec, links)
        tr = subset_records(rec, "2026-01-01", "2026-01-31")
        te = subset_records(rec, "2026-03-01", "2026-03-14")
        window = ["2026-03-01", "2026-03-14"]

    by = {r.rid: r for r in te}
    ids = set(by)
    log(f"{args.city}: train {len(tr):,} / test {len(te):,}")

    # ---- labels from the same distribution our own scorer is fitted on ----
    t0 = time.time()
    X, y, pids = build_candidate_pairs_with_ids(tr, gold, max_records=22000)
    log(f"training pairs {X.shape} pos={int(y.sum())} ({time.time()-t0:.0f}s)")
    rng = np.random.default_rng(0)
    if len(pids) > args.train_pairs:
        pos = np.flatnonzero(y == 1)
        neg = np.flatnonzero(y == 0)
        keep_pos = rng.choice(pos, size=min(len(pos), args.train_pairs // 2), replace=False)
        keep_neg = rng.choice(neg, size=min(len(neg), args.train_pairs - len(keep_pos)),
                              replace=False)
        keep = np.concatenate([keep_pos, keep_neg])
    else:
        keep = np.arange(len(pids))
    labels = pd.DataFrame({
        "unique_id_l": [pids[i][0] for i in keep],
        "unique_id_r": [pids[i][1] for i in keep],
        "label": [int(y[i]) for i in keep],
    })
    log(f"labels {labels.shape} pos={int(labels['label'].sum())}")

    # ---- splink, supervised m ----
    df = frame(te)
    db_api = DuckDBAPI()
    sdf = db_api.register(df)
    prior = (args.prior if args.prior is not None else
             (float(labels["label"].mean()) if args.set_prior else 1e-4))
    print(f"prior = {prior:.5f} (set_prior={args.set_prior})")
    settings = SettingsCreator(
        link_type="dedupe_only",
        probability_two_random_records_match=prior,
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
            block_on("block"), block_on("addr_street"), block_on("service", "zip"),
        ],
    )
    linker = Linker(sdf, settings)
    linker.training.estimate_u_using_random_sampling(max_pairs=args.u_pairs)
    log("u estimated")
    lab_sdf = db_api.register(labels)
    linker.training.estimate_m_from_pairwise_labels(lab_sdf)
    log("m estimated from pairwise labels")

    pred = linker.inference.predict(threshold_match_probability=0.10)
    pred_df = pred.as_pandas_dataframe()
    log(f"predicted {len(pred_df):,} pairs above 0.10")

    rows = []
    for thr in (0.99, 0.95, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30):
        clu = linker.clustering.cluster_pairwise_predictions_at_threshold(
            pred, threshold_match_probability=thr)
        cdf = clu.as_pandas_dataframe()
        assign = dict(zip(cdf["unique_id"].astype(str), cdf["cluster_id"].astype(str)))
        part, dec = clusters_to_decisions(
            {k: v for k, v in assign.items() if k in ids}, by)
        ev = evaluate_partition(part, gold, ids)
        d = decision_fmp(dec, gold)
        rows.append({"threshold": thr, "arm": "native",
                     "pair_precision": ev["precision"], "pair_recall": ev["recall"],
                     "pair_f1": ev["f1"], "max_pred_cluster": ev["max_pred_cluster"],
                     "dec_fmp": d["dec_fmp"], "dec_coverage": d["dec_coverage"]})
        log(f"thr={thr:>4.2f} native | P={ev['precision']:.3f} R={ev['recall']:.3f} "
            f"F1={ev['f1']:.3f} FMP={d['dec_fmp']:.3f} max={ev['max_pred_cluster']}")

        sub = pred_df[pred_df["match_probability"] >= thr]
        pairs = [(str(x), str(yy), float(s)) for x, yy, s in
                 zip(sub["unique_id_l"], sub["unique_id_r"], sub["match_probability"])
                 if str(x) in ids and str(yy) in ids]
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
                     "dec_fmp": cd["dec_fmp"], "dec_coverage": cd["dec_coverage"]})
        log(f"thr={thr:>4.2f} capped | P={cev['precision']:.3f} R={cev['recall']:.3f} "
            f"F1={cev['f1']:.3f} FMP={cd['dec_fmp']:.3f} max={cev['max_pred_cluster']}")

    native = [r for r in rows if r["arm"] == "native"]
    capped = [r for r in rows if r["arm"] == "capped"]
    payload = {"city": args.city, "window": window, "n_test": len(te),
               "n_labels": int(len(labels)), "labels_pos": int(labels["label"].sum()),
               "rows": rows,
               "best_native": max(native, key=lambda r: r["pair_f1"]),
               "best_capped": max(capped, key=lambda r: r["pair_f1"])}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    log(f"best native F1={payload['best_native']['pair_f1']:.4f}  "
        f"capped F1={payload['best_capped']['pair_f1']:.4f}")
    log(f"wrote {Path(args.out).name}")


if __name__ == "__main__":
    main()
