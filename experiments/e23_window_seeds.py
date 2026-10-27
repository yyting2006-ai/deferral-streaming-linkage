"""E23: the memory-window sweep with a *per-window* calibrated threshold and seeds.

The single-threshold version of this table was not a fair comparison.  Its
tau=0.85 operating point was tuned for the logistic scorer; on the gradient-
boosted scorer that value sits in the tail of the score distribution, so every
window returned almost no merges and the "the window does not change quality"
claim was really the claim "the threshold, not the window, set quality".

Here each (seed, window) pair gets its own threshold, calibrated on the March
calibration set to the same decision-level false-merge budget (0.40) that the
rest of the paper uses, and is then tested once on May.  Bisection moves *down*
when the budget is met (lower threshold -> more merges -> more recall), so the
calibrated threshold spends the whole budget instead of sitting at the strict end
of the feasible interval.

Records that never merge are excluded from the FMP denominator by the metric's
definition, so an infeasible budget shows up as "no feasible threshold found"
rather than as a silent pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.decision_metrics import decision_fmp
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.methods import PrefixClusterer, StreamConfig
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs
from experiments.e11d_gbdt import GBT

OUT = Path(__file__).resolve().parents[1] / "results"
TARGET = 0.40
WINDOWS = (15.0, 30.0, 60.0)
SEEDS = (1, 2, 3)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def measure(model, records, gold, tau, window_days, with_partition=True):
    cl = PrefixClusterer(StreamConfig(t_merge=float(tau), t_pending=0.0,
                                      window_days=float(window_days)), model)
    t0 = time.perf_counter()
    for r in records:
        cl.on_record(r)
    elapsed = time.perf_counter() - t0
    d = decision_fmp(cl.merge_log, gold)
    d["tau"] = round(float(tau), 5)
    d["ms_per_record"] = round(1000.0 * elapsed / max(1, len(records)), 3)
    if with_partition:
        ev = evaluate_partition(cl.partition(), gold, {r.rid for r in records})
        d.update({"pair_precision": ev["precision"], "pair_recall": ev["recall"],
                  "pair_f1": ev["f1"], "n_pos": ev["n_pos"],
                  "max_pred_cluster": ev["max_pred_cluster"]})
    return d


def bisect(model, records, gold, window_days, iters=5):
    """Return the lowest tau whose calibration FMP is within budget.

    Direction per the operating-point convention: meeting the budget means we can
    afford to merge more, so the bracket moves down (`hi = mid`).
    """
    lo, hi, best = 0.005, 0.99, None
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        d = measure(model, records, gold, mid, window_days, with_partition=False)
        log(f"      bisect w={window_days:4.0f} tau={mid:.4f} "
            f"cal_fmp={d['dec_fmp']:.4f} merges={d['decisions']}")
        if d["dec_fmp"] <= TARGET:
            best = d
            hi = mid
        else:
            lo = mid
    if best is None:
        # never met the budget: report the strictest trial so the row still exists
        d = measure(model, records, gold, hi, window_days, with_partition=False)
        d["feasible"] = False
        return d
    best["feasible"] = True
    return best


def run_seed(seed, rec, gold, cal, cf, sink, windows=WINDOWS, done=(),
             on_row=None):
    tr = subset_records(rec, "2024-01-01", "2024-02-29")
    t0 = time.time()
    X, y = build_candidate_pairs(tr, gold, max_records=22000)
    model = GBT(X, y, seed=seed)
    log(f"seed={seed} pairs={X.shape} pos={int(y.sum())} fit {time.time()-t0:.0f}s")

    for w in windows:
        if (seed, w) in done:
            continue
        pick = bisect(model, cal, gold, w)
        res = measure(model, cf, gold, pick["tau"], w, with_partition=True)
        row = {"seed": seed, "window_days": w, "tau": pick["tau"],
               "cal_fmp": pick["dec_fmp"], "cal_decisions": pick["decisions"],
               "feasible": bool(pick.get("feasible", True)),
               "dec_fmp": res["dec_fmp"], "decisions": res["decisions"],
               "precision": res["pair_precision"], "recall": res["pair_recall"],
               "f1": res["pair_f1"], "n_pos": res.get("n_pos"),
               "max_pred_cluster": res.get("max_pred_cluster"),
               "ms_per_record": res["ms_per_record"]}
        sink.append(row)
        if on_row is not None:
            on_row()
        log(f"seed={seed} W={w:4.0f} tau={row['tau']:.4f} calFMP={row['cal_fmp']:.3f} "
            f"| TEST F1={row['f1']:.4f} P={row['precision']:.4f} "
            f"R={row['recall']:.4f} FMP={row['dec_fmp']:.3f} "
            f"merges={row['decisions']} {row['ms_per_record']:.2f}ms")
    return sink


def summarise(rows):
    out = {}
    for w in sorted({r["window_days"] for r in rows}):
        sub = [r for r in rows if r["window_days"] == w]
        f1 = np.array([r["f1"] for r in sub], dtype=float)
        fmp = np.array([r["dec_fmp"] for r in sub], dtype=float)
        ms = np.array([r["ms_per_record"] for r in sub], dtype=float)
        tau = np.array([r["tau"] for r in sub], dtype=float)
        sd = lambda a: float(a.std(ddof=1)) if len(a) > 1 else 0.0
        out[str(w)] = {
            "n_seeds": len(sub),
            "f1_mean": round(float(f1.mean()), 4), "f1_sd": round(sd(f1), 4),
            "fmp_mean": round(float(fmp.mean()), 4), "fmp_sd": round(sd(fmp), 4),
            "ms_mean": round(float(ms.mean()), 3), "ms_sd": round(sd(ms), 3),
            "tau_mean": round(float(tau.mean()), 4),
            "precision_mean": round(float(np.mean([r["precision"] for r in sub])), 4),
            "recall_mean": round(float(np.mean([r["recall"] for r in sub])), 4),
            "all_feasible": all(r["feasible"] for r in sub),
            "max_cal_fmp": round(float(max(r["cal_fmp"] for r in sub)), 4),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    ap.add_argument("--out", default=str(OUT / "e23_window_seeds.json"))
    ap.add_argument("--merge", nargs="*", default=None,
                    help="partial json files to merge instead of running")
    ap.add_argument("--cal-max", type=int, default=20000)
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any partial result file and start over")
    args = ap.parse_args()

    out_path = Path(args.out)
    if args.merge:
        rows = []
        for p in args.merge:
            rows += json.loads(Path(p).read_text(encoding="utf-8"))["rows"]
        rows.sort(key=lambda r: (r["window_days"], r["seed"]))
        payload = {"target_fmp": TARGET, "windows": list(WINDOWS),
                   "rows": rows, "summary": summarise(rows)}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        log(f"merged {len(rows)} rows -> {out_path}")
        for w, s in payload["summary"].items():
            log(f"W={float(w):4.0f} F1={s['f1_mean']:.4f}+/-{s['f1_sd']:.4f} "
                f"FMP={s['fmp_mean']:.3f} {s['ms_mean']:.2f}ms "
                f"feasible={s['all_feasible']}")
        return

    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    cal = subset_records(rec, "2024-03-01", "2024-03-31")[:args.cal_max]
    cf = subset_records(rec, "2024-05-01", "2024-05-31")
    log(f"cal={len(cal):,} test={len(cf):,}")

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    rows = []
    if out_path.exists() and not args.fresh:
        try:
            rows = json.loads(out_path.read_text(encoding="utf-8")).get("rows", [])
            log(f"resuming: {len(rows)} rows already on disk")
        except (ValueError, KeyError, OSError):
            rows = []
    done = {(r["seed"], r["window_days"]) for r in rows}

    def checkpoint():
        rows.sort(key=lambda r: (r["window_days"], r["seed"]))
        payload = {"target_fmp": TARGET, "windows": list(WINDOWS),
                   "rows": rows, "summary": summarise(rows)}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return payload

    for seed in seeds:
        if all((seed, w) in done for w in WINDOWS):
            log(f"seed={seed} already on disk, skipping")
            continue
        run_seed(seed, rec, gold, cal, cf, sink=rows, done=done, on_row=checkpoint)

    payload = checkpoint()
    for w, s in payload["summary"].items():
        log(f"W={float(w):4.0f} F1={s['f1_mean']:.4f}+/-{s['f1_sd']:.4f} "
            f"FMP={s['fmp_mean']:.3f} {s['ms_mean']:.2f}ms "
            f"feasible={s['all_feasible']}")
    log(f"wrote {out_path.name}")


if __name__ == "__main__":
    main()
