"""Decision-level false-merge accounting.

Online FDR controls one hypothesis per arrival; the hypothesis is the pair the
scorer actually scored, (record, representative of the chosen issue).  The
matching error measure is therefore the decided-pair false-merge proportion, not
the proportion of wrong pairs inside the induced partition (which over-counts a
single bad merge into a large cluster).
"""
from __future__ import annotations

from typing import Dict, Iterable, Sequence, Tuple

from .gold import Gold


def decision_fmp(merge_log: Sequence[Tuple[str, str]], gold: Gold) -> Dict[str, float]:
    correct = wrong = unverifiable = 0
    for rid, rep in merge_log:
        rel = gold.same_issue(rid, rep)
        if rel is None:
            unverifiable += 1
        elif rel:
            correct += 1
        else:
            wrong += 1
    n = correct + wrong
    return {
        "decisions": len(merge_log),
        "dec_correct": correct,
        "dec_wrong": wrong,
        "dec_unverifiable": unverifiable,
        "dec_fmp": round(wrong / n, 6) if n else 0.0,
        "dec_precision": round(correct / n, 6) if n else float("nan"),
        "dec_coverage": round(n / max(1, len(merge_log)), 6) if merge_log else 0.0,
    }
