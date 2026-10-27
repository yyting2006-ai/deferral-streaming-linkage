"""Training on the *decision-time candidate distribution*.

A pair classifier trained on randomly sampled non-matches sees pairs that a
blocking stage would never surface: far apart in time, often different service.
At decision time every candidate already shares service and street, so the
train/test distributions differ exactly where the decision is hard.  We therefore
build the training pairs by replaying the prefix-safe candidate generator, which
is the only distribution the deployed scorer ever scores.

The replay also gives us something the classifier does not need but the global
clustering baseline in E25 does: the identity of the two records behind each
candidate pair.  `build_candidate_pairs_with_ids` returns them; both public
entry points share one loop so the labelled matrix cannot drift between them.
"""
from __future__ import annotations

import random
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np

from .gold import Gold
from .methods import (FEATURE_NAMES, Issue, PrefixIndex, _char3, pair_features,
                      tokens_of)
from .schema import Record

PairIds = List[Tuple[str, str]]


def _iter_candidate_pairs(records: Sequence[Record], gold: Gold,
                          window_days: float = 60.0,
                          k_candidates: int = 60,
                          neg_keep_rate: float = 0.35,
                          max_records: Optional[int] = None,
                          seed: int = 13,
                          leaky: bool = False,
                          ) -> Iterator[Tuple[np.ndarray, float, str, str]]:
    """Replay the candidate generator, yielding (features, label, rid_a, rid_b).

    `rid_a` is the arriving record and `rid_b` the representative it was scored
    against; `label` is 1.0 when the gold partition certifies them as the same
    issue.  Streaming and batch consumers must be fed the same pairs, so the
    loop lives here exactly once.
    """
    rng = random.Random(seed)
    index = PrefixIndex(window_days)
    by_rid: Dict[str, Record] = {}
    tok: Dict[str, List[str]] = {}
    c3: Dict[str, List[str]] = {}

    def get_tok(rid: str, rec: Record) -> List[str]:
        t = tok.get(rid)
        if t is None:
            t = tokens_of(rec.text)
            tok[rid] = t
        return t

    def get_c3(rid: str, rec: Record) -> List[str]:
        t = c3.get(rid)
        if t is None:
            t = _char3(rec.description)
            c3[rid] = t
        return t

    cur_day = -1
    n_used = 0
    for rec in records:
        day = int(rec.day)
        if day != cur_day:
            cur_day = day
            index.prune(day)
        rt, rc = get_tok(rec.rid, rec), get_c3(rec.rid, rec)
        cand_ids = index.candidates(rec, rt, k=k_candidates)
        for iid in cand_ids:
            issue = index.issues.get(iid)
            if issue is None or not issue.reps:
                continue
            rep = by_rid.get(issue.reps[0])
            if rep is None:
                continue
            same = (gold.component.get(rec.rid) is not None and
                    gold.component.get(rec.rid) == gold.component.get(rep.rid))
            if not same and rng.random() > neg_keep_rate:
                continue
            f = pair_features(rec, rt, rc, issue, rep, get_tok(rep.rid, rep),
                              get_c3(rep.rid, rep))
            if leaky:
                closed = 1.0 if rec.extra.get("date_closed") else 0.0
                age = float(rec.extra.get("case_age_days") or 0.0)
                floc = 1.0 if rec.extra.get("floc") else 0.0
                f = np.concatenate([f, np.array([closed, min(30.0, age) / 30.0, floc],
                                                dtype=np.float32)])
            yield f, (1.0 if same else 0.0), rec.rid, rep.rid
        # the record becomes an issue for later arrivals
        iv = Issue(iid=f"T{n_used:07d}", created=rec.day, last=rec.day)
        iv.absorb(rec)
        index.add(iv, rec, rt)
        by_rid[rec.rid] = rec
        n_used += 1
        if max_records is not None and n_used >= max_records:
            break


def _stack(pairs: Iterator[Tuple[np.ndarray, float, str, str]]):
    X: List[np.ndarray] = []
    y: List[float] = []
    ids: PairIds = []
    for f, lab, rid_a, rid_b in pairs:
        X.append(f)
        y.append(lab)
        ids.append((rid_a, rid_b))
    if not X:
        return (np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32),
                np.zeros(0, np.float32), ids)
    return np.stack(X), np.array(y, dtype=np.float32), ids


def build_candidate_pairs(records: Sequence[Record], gold: Gold,
                          window_days: float = 60.0,
                          k_candidates: int = 60,
                          neg_keep_rate: float = 0.35,
                          max_records: Optional[int] = None,
                          seed: int = 13,
                          leaky: bool = False) -> Tuple[np.ndarray, np.ndarray]:
    """Replay the candidate generator over `records` and label every candidate.

    With `leaky=True` three fields that only exist after the request is closed
    (closure flag, case age, functional-location code) are appended, which is the
    "I trained on the export" failure mode.
    """
    X, y, _ids = _stack(_iter_candidate_pairs(
        records, gold, window_days=window_days, k_candidates=k_candidates,
        neg_keep_rate=neg_keep_rate, max_records=max_records, seed=seed,
        leaky=leaky))
    return X, y


def build_candidate_pairs_with_ids(records: Sequence[Record], gold: Gold,
                                   window_days: float = 60.0,
                                   k_candidates: int = 60,
                                   neg_keep_rate: float = 0.35,
                                   max_records: Optional[int] = None,
                                   seed: int = 13,
                                   leaky: bool = False,
                                   ) -> Tuple[np.ndarray, np.ndarray, PairIds]:
    """Same pairs as `build_candidate_pairs`, plus the (rid_a, rid_b) of each row.

    The batch baseline needs the graph, not just the labels: it clusters the whole
    window at once instead of deciding on arrival.  Row i of `X` scores the pair
    `pair_ids[i]`, in the order the candidate generator emitted them.
    """
    return _stack(_iter_candidate_pairs(
        records, gold, window_days=window_days, k_candidates=k_candidates,
        neg_keep_rate=neg_keep_rate, max_records=max_records, seed=seed,
        leaky=leaky))
