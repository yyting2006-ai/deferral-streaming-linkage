"""Gap-bucketed pair evaluation.

The two-track claim is not that it wins on one aggregate number; it is that the
two ends of the time-gap axis are served by different operators. That claim has to
be read off a table in which the gap is an explicit axis, so every pair -- gold
positive and prediction alike -- is bucketed by its time gap.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Set, Tuple

from .gold import Gold

BUCKETS = [(0, 7, "$\\le$7d"), (8, 30, "8--30d"), (31, 90, "31--90d"),
           (91, 10 ** 6, "$>$90d")]


def _bucket(gap_days: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= gap_days <= hi:
            return name
    return BUCKETS[0][2]


def evaluate_by_gap(pred_pairs: Iterable[Tuple[str, str]], gold: Gold,
                    window_ids: Set[str]) -> Dict[str, dict]:
    """Precision/recall/FMP per time-gap bucket, over the adjudicated domain."""
    by_id = gold.by_id

    # gold positives, bucketed
    pos: Dict[str, int] = defaultdict(int)
    for a, b in gold.positives:
        if a not in window_ids or b not in window_ids:
            continue
        gap = abs((by_id[a].when - by_id[b].when).total_seconds()) / 86400.0
        pos[_bucket(gap)] += 1

    # predictions, bucketed; only pairs whose both ends are adjudicated count
    stat: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"pred": 0, "tp": 0, "fp": 0, "unver": 0})
    seen = set()
    for a, b in pred_pairs:
        if a not in window_ids or b not in window_ids:
            continue
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        gap = abs((by_id[a].when - by_id[b].when).total_seconds()) / 86400.0
        s = stat[_bucket(gap)]
        s["pred"] += 1
        rel = gold.same_issue(*key)
        if rel is None:
            s["unver"] += 1
        elif rel:
            s["tp"] += 1
        else:
            s["fp"] += 1

    out = {}
    for lo, hi, name in BUCKETS:
        s = stat.get(name, {"pred": 0, "tp": 0, "fp": 0, "unver": 0})
        n_pos = pos.get(name, 0)
        adj = s["tp"] + s["fp"]
        out[name] = {
            "gold_positive": n_pos,
            "predicted": s["pred"],
            "tp": s["tp"],
            "fp": s["fp"],
            "unverifiable": s["unver"],
            "precision": round(s["tp"] / adj, 4) if adj else None,
            "recall": round(s["tp"] / n_pos, 4) if n_pos else None,
            "fmp": round(s["fp"] / adj, 4) if adj else None,
            "coverage": round(adj / s["pred"], 4) if s["pred"] else None,
        }
    return out


def summarise(pred_pairs: Iterable[Tuple[str, str]], gold: Gold,
              window_ids: Set[str]) -> dict:
    """Aggregate over all buckets plus the per-bucket breakdown."""
    per = evaluate_by_gap(pred_pairs, gold, window_ids)
    n_pos = sum(v["gold_positive"] for v in per.values())
    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / n_pos if n_pos else float("nan")
    f = 2 * p * r / (p + r) if p == p and r == r and p + r else float("nan")
    return {"overall": {"gold_positive": n_pos, "tp": tp, "fp": fp,
                        "precision": round(p, 4), "recall": round(r, 4),
                        "f1": round(f, 4),
                        "fmp": round(fp / (tp + fp), 4) if tp + fp else None},
            "by_gap": per}
