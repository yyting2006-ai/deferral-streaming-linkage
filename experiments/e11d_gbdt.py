"""E11d: tree ensemble without any joblib pool, merged into the matched comparison.

HistGradientBoostingClassifier intermittently spawns a thread pool that needs a
named pipe, which the sandbox denies. GradientBoostingClassifier is implemented
without that backend, so it is the reliable way to get a boosted-tree operating
point here. Settings are modest so the fit stays inside a few minutes.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "4")

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.decision_metrics import decision_fmp
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.methods import FEATURE_NAMES, PrefixClusterer, StreamConfig
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs

OUT = Path(__file__).resolve().parents[1] / "results"
TARGET = 0.40


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class GBT:
    def __init__(self, X, y, seed=13):
        from sklearn.ensemble import GradientBoostingClassifier
        self.m = GradientBoostingClassifier(
            n_estimators=160, learning_rate=0.1, max_depth=3, subsample=0.5,
            min_samples_leaf=20, random_state=seed)
        self.m.fit(np.asarray(X), np.asarray(y))

    def __call__(self, X):
        return self.m.predict_proba(np.asarray(X))[:, 1]


def measure(model, records, gold, tau, with_partition=False):
    cl = PrefixClusterer(StreamConfig(t_merge=float(tau), t_pending=0.0), model)
    for r in records:
        cl.on_record(r)
    d = decision_fmp(cl.merge_log, gold)
    d["tau"] = round(float(tau), 4)
    if with_partition:
        ids = {r.rid for r in records}
        ev = evaluate_partition(cl.partition(), gold, ids)
        d["pair_recall"] = ev["recall"]
        d["pair_precision"] = ev["precision"]
        d["pair_f1"] = ev["f1"]
        d["n_gold_pos"] = ev["n_pos"]
    return d


def bisect(model, records, gold, iters=5):
    lo, hi, best = 0.02, 0.98, None
    for _ in range(iters):
        mid = (lo + hi) / 2
        d = measure(model, records, gold, mid)
        if d["dec_fmp"] <= TARGET:
            best = d
            lo = mid
        else:
            hi = mid
    if best is None:
        best = measure(model, records, gold, hi)
    return best


def main() -> None:
    records, links = load_san_diego()
    gold = build_gold("san_diego", records, links)
    tr = subset_records(records, "2024-01-01", "2024-02-29")
    cal = subset_records(records, "2024-03-01", "2024-03-31")[:20000]
    cf = subset_records(records, "2024-05-01", "2024-05-31")

    X, y = build_candidate_pairs(tr, gold, max_records=22000)
    log(f"training pairs {X.shape} pos={int(y.sum())}")
    t0 = time.time()
    model = GBT(X, y)
    log(f"  gbdt(no-pool) fitted in {time.time()-t0:.0f}s")

    pick = bisect(model, cal, gold)
    res = measure(model, cf, gold, pick["tau"], with_partition=True)
    res.update({"model": "gbdt", "cal_fmp": pick["dec_fmp"],
                "cal_decisions": pick["decisions"]})
    log(f"gbdt        tau={pick['tau']:.3f} | cal FMP={pick['dec_fmp']:.3f} | "
        f"TEST merges={res['decisions']:5d} FMP={res['dec_fmp']:.3f} "
        f"P={res['dec_precision']:.3f} R={res['pair_recall']:.3f} "
        f"F1={res['pair_f1']:.3f}")

    path = OUT / "e11b_matched.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "target_fmp": TARGET, "rows": []}
    payload["rows"] = [r for r in payload.get("rows", []) if r.get("model") != "gbdt"]
    payload["rows"].append(res)
    order = {"rule_block7": 0, "logistic": 1, "mlp": 2, "gbdt": 3}
    payload["rows"].sort(key=lambda r: order.get(r.get("model"), 9))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"merged into {path.name}: {[r['model'] for r in payload['rows']]}")


if __name__ == "__main__":
    main()
