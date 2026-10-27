"""Metrics for streaming issue linkage under a partially certifiable gold.

Pair-level precision is reported as an interval because a predicted link that
touches a record outside the adjudicated domain can be neither confirmed nor
refuted:  strict = every unverifiable link counted as an error; possible = every
unverifiable link ignored.  Recall only ever counts certified positives.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .gold import Gold


@dataclass
class PairScore:
    tp: int = 0
    fp_certified: int = 0
    unverifiable: int = 0
    predicted: int = 0
    n_pos: int = 0

    @property
    def recall(self) -> float:
        return self.tp / self.n_pos if self.n_pos else float("nan")

    def precision(self, mode: str = "strict") -> float:
        if mode == "strict":
            den = self.tp + self.fp_certified + self.unverifiable
        elif mode == "possible":
            den = self.tp + self.fp_certified
        else:
            den = self.predicted
        return self.tp / den if den else float("nan")

    def f1(self, mode: str = "strict") -> float:
        p, r = self.precision(mode), self.recall
        if not (p == p and r == r) or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)

    def as_dict(self) -> Dict[str, float]:
        return {
            "predicted": self.predicted, "tp": self.tp,
            "fp_certified": self.fp_certified, "unverifiable": self.unverifiable,
            "n_pos": self.n_pos, "recall": round(self.recall, 6),
            "precision_strict": round(self.precision("strict"), 6),
            "precision_possible": round(self.precision("possible"), 6),
            "f1_strict": round(self.f1("strict"), 6),
            "f1_possible": round(self.f1("possible"), 6),
            "fmp_strict": round(false_merge_proportion(self), 6),
            "fp_rate_measure": round(self.fp_certified / max(1, self.fp_certified + self.tp), 6),
        }


def false_merge_proportion(ps: PairScore) -> float:
    """FMP: share of *audited* predicted links that are certified wrong.

    Unverifiable links are excluded from the denominator because they cannot be
    adjudicated; this matches how a duty officer can audit real merges.
    """
    den = ps.tp + ps.fp_certified
    return ps.fp_certified / den if den else 0.0


def window_positives(gold: Gold, rids: Set[str]) -> int:
    """Number of certified positive pairs whose BOTH endpoints are in `rids`."""
    total = 0
    per_comp: Dict[str, int] = {}
    for rid in rids:
        c = gold.component.get(rid)
        if c is None:
            continue
        per_comp[c] = per_comp.get(c, 0) + 1
    for n in per_comp.values():
        if n > 1:
            total += n * (n - 1) // 2
    return total


def evaluate_partition(partition: Dict[str, str], gold: Gold, window_ids: Set[str],
                       max_pairs: int = 4_000_000) -> Dict[str, float]:
    """Project a predicted partition onto the adjudicated domain and score it.

    Only requests that take part in an official link have a workflow-issued
    identity, so only pairs of *such* requests are certified positive or
    certified negative.  Projecting the partition onto that domain removes the
    "unverifiable pair" explosion that otherwise dominates precision whenever a
    merge joins a large cluster.
    """
    known_ids = [r for r in window_ids if r in gold.known]
    kset = set(known_ids)
    n_pos = window_positives(gold, kset)
    clusters: Dict[str, List[str]] = {}
    for rid in known_ids:
        clusters.setdefault(partition.get(rid, "?" + rid), []).append(rid)
    tp = fp = 0
    emitted = 0
    capped = False
    for mem in clusters.values():
        if len(mem) < 2:
            continue
        mem = sorted(mem)
        for i, a in enumerate(mem):
            for b in mem[i + 1:]:
                if gold.component[a] == gold.component[b]:
                    tp += 1
                else:
                    fp += 1
                emitted += 1
                if emitted >= max_pairs:
                    capped = True
                    break
            if capped:
                break
        if capped:
            break
    p = tp / (tp + fp) if (tp + fp) else float("nan")
    r = tp / n_pos if n_pos else float("nan")
    f = 2 * p * r / (p + r) if (p == p and r == r and p + r) else float("nan")
    sizes = [len(m) for m in clusters.values()]
    merges = sum(s for s in sizes if s > 1)
    return {
        "n_records": len(window_ids), "n_known": len(known_ids),
        "known_rate": round(len(known_ids) / max(1, len(window_ids)), 5),
        "pred_pairs": tp + fp, "tp": tp, "fp": fp, "n_pos": n_pos,
        "precision": round(p, 6), "recall": round(r, 6), "f1": round(f, 6),
        "fmp": round(fp / (tp + fp), 6) if (tp + fp) else 0.0,
        "merge_rate": round(merges / max(1, len(known_ids)), 5),
        "max_pred_cluster": max(sizes) if sizes else 0,
        "capped": capped,
    }


def score_pairs(pred_pairs: Iterable[Tuple[str, str]], gold: Gold,
                restrict: Optional[Set[str]] = None,
                max_pairs: Optional[int] = None) -> PairScore:
    """Pairwise score. `restrict` limits the certified positive denominator to the
    evaluated window, which is essential for a streaming protocol: a one-month run
    cannot be credited with the whole year's positive pairs."""
    if restrict is None:
        n_pos = gold.n_pos_pairs
    else:
        n_pos = window_positives(gold, restrict)
    ps = PairScore(n_pos=n_pos)
    seen: Set[Tuple[str, str]] = set()
    for a, b in pred_pairs:
        if a == b:
            continue
        if restrict is not None and (a not in restrict or b not in restrict):
            continue
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        ps.predicted += 1
        rel = gold.same_issue(*key)
        if rel is None:
            ps.unverifiable += 1
        elif rel:
            ps.tp += 1
        else:
            ps.fp_certified += 1
        if max_pairs is not None and ps.predicted >= max_pairs:
            break
    return ps


def b3(pred: Dict[str, str], gold: Gold, scope: Optional[Iterable[str]] = None
       ) -> Dict[str, float]:
    """B-cubed P/R/F1 over the adjudicated domain only."""
    ids = [r for r in (scope if scope is not None else gold.known)
           if r in gold.known and r in pred]
    if not ids:
        return {"b3_p": float("nan"), "b3_r": float("nan"), "b3_f1": float("nan")}
    gold_comp = [gold.component[r] for r in ids]
    pred_comp = [pred[r] for r in ids]
    g_count: Dict[str, int] = {}
    p_count: Dict[str, int] = {}
    for g in gold_comp:
        g_count[g] = g_count.get(g, 0) + 1
    for p in pred_comp:
        p_count[p] = p_count.get(p, 0) + 1
    inter: Dict[Tuple[str, str], int] = {}
    for g, p in zip(gold_comp, pred_comp):
        inter[(g, p)] = inter.get((g, p), 0) + 1
    p_sum = r_sum = 0.0
    for g, p in zip(gold_comp, pred_comp):
        c = inter[(g, p)]
        p_sum += c / p_count[p]
        r_sum += c / g_count[g]
    P, R = p_sum / len(ids), r_sum / len(ids)
    F = 2 * P * R / (P + R) if (P + R) else float("nan")
    return {"b3_p": round(P, 6), "b3_r": round(R, 6), "b3_f1": round(F, 6)}


def paired_bootstrap_ci(deltas: Sequence[float], n_boot: int = 2000,
                        alpha: float = 0.05, seed: int = 13) -> Tuple[float, float, float]:
    rng = random.Random(seed)
    n = len(deltas)
    if n == 0:
        return (float("nan"),) * 3
    mean = sum(deltas) / n
    boots = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randrange(n)]
        boots.append(s / n)
    boots.sort()
    lo = boots[int((alpha / 2) * n_boot)]
    hi = boots[int((1 - alpha / 2) * n_boot) - 1]
    return (round(mean, 6), round(lo, 6), round(hi, 6))


def risk_coverage(curve: List[Tuple[float, float, float]]) -> List[Dict[str, float]]:
    """curve items: (score_threshold, accepted_fraction, fmp_at_that_threshold)."""
    return [{"threshold": t, "coverage": c, "fmp": f} for t, c, f in curve]
