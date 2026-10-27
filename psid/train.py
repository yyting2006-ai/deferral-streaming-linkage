"""Pair-scorer training on the *training* window only (prefix-safe by construction)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .gold import CONCURRENT_DAYS, Gold
from .methods import (FEATURE_NAMES, Issue, _char3, jaccard, pair_features,
                      text_cosine, tokens_of)
from .schema import Record


_TOK: Dict[str, List[str]] = {}
_C3: Dict[str, List[str]] = {}


def _feat(rec: Record, rep: Record, gap_days: float, size: int = 1,
          leaky: bool = False) -> np.ndarray:
    tok_r = _TOK.get(rec.rid)
    if tok_r is None:
        tok_r = tokens_of(rec.text); _TOK[rec.rid] = tok_r
    c3_r = _C3.get(rec.rid)
    if c3_r is None:
        c3_r = _char3(rec.description); _C3[rec.rid] = c3_r
    tok_p = _TOK.get(rep.rid)
    if tok_p is None:
        tok_p = tokens_of(rep.text); _TOK[rep.rid] = tok_p
    c3_p = _C3.get(rep.rid)
    if c3_p is None:
        c3_p = _char3(rep.description); _C3[rep.rid] = c3_p
    iv = Issue(iid="t", created=rep.day, last=int(rep.day - gap_days), n=size)
    f = pair_features(rec, tok_r, c3_r, iv, rep, tok_p, c3_p)
    if leaky:
        # Appended only for the leakage audit: fields that exist after closure.
        closed = 1.0 if rec.extra.get("date_closed") else 0.0
        age = float(rec.extra.get("case_age_days") or 0.0)
        floc = 1.0 if rec.extra.get("floc") else 0.0
        f = np.concatenate([f, np.array([closed, min(30.0, age) / 30.0, floc],
                                        dtype=np.float32)])
    return f


def build_pairs(records: Sequence[Record], gold: Gold, max_pos: int = 200_000,
                max_neg: int = 400_000, seed: int = 13,
                leaky: bool = False) -> Tuple[np.ndarray, np.ndarray]:
    """Positives = official concurrent links; negatives = same-block non-links."""
    rng = random.Random(seed)
    in_window = {r.rid: r for r in records}
    X: List[np.ndarray] = []
    y: List[float] = []

    for child, parent in gold.links.items():
        if child not in in_window or parent not in in_window:
            continue
        rc, rp = in_window[child], in_window[parent]
        lag = (rc.when - rp.when).total_seconds() / 86400.0
        if lag < 0 or lag > CONCURRENT_DAYS:
            continue
        X.append(_feat(rc, rp, lag, leaky=leaky))
        y.append(1.0)
        if len(X) >= max_pos:
            break
    n_pos = len(X)

    # hard negatives: same service + same block, different gold component
    by_block: Dict[Tuple[str, str], List[Record]] = {}
    for r in in_window.values():
        if r.addr.block:
            by_block.setdefault((r.service_key, r.addr.block), []).append(r)
    block_keys = [k for k, v in by_block.items() if len(v) >= 2]
    by_svc: Dict[str, List[Record]] = {}
    for r in in_window.values():
        by_svc.setdefault(r.service_key, []).append(r)
    svc_keys = [k for k, v in by_svc.items() if len(v) >= 2]
    if not block_keys or not svc_keys:
        return np.stack(X), np.array(y, dtype=np.float32)

    target_hard = min(max_neg, max(1000, n_pos * 3))
    target_easy = min(max_neg, max(1000, n_pos * 2))
    tried = 0
    hard = 0
    while hard < target_hard and tried < target_hard * 50:
        tried += 1
        grp = by_block[block_keys[rng.randrange(len(block_keys))]]
        a, b = rng.sample(grp, 2)
        ca, cb = gold.component.get(a.rid), gold.component.get(b.rid)
        if ca is not None and cb is not None and ca == cb:
            continue
        lag = abs((a.when - b.when).total_seconds() / 86400.0)
        X.append(_feat(a, b, lag, leaky=leaky))
        y.append(0.0)
        hard += 1

    easy = 0
    tried = 0
    while easy < target_easy and tried < target_easy * 50:
        tried += 1
        grp = by_svc[svc_keys[rng.randrange(len(svc_keys))]]
        a, b = rng.sample(grp, 2)
        if a.addr.block and a.addr.block == b.addr.block:
            continue
        ca, cb = gold.component.get(a.rid), gold.component.get(b.rid)
        if ca is not None and cb is not None and ca == cb:
            continue
        lag = abs((a.when - b.when).total_seconds() / 86400.0)
        X.append(_feat(a, b, lag, leaky=leaky))
        y.append(0.0)
        easy += 1

    return np.stack(X), np.array(y, dtype=np.float32)


@dataclass
class PairModel:
    pipeline: Pipeline
    feature_names: List[str]

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return self.pipeline.predict_proba(X)[:, 1]

    def weights(self) -> Dict[str, float]:
        lr: LogisticRegression = self.pipeline.named_steps["lr"]
        return dict(zip(self.feature_names, np.round(lr.coef_[0], 4)))


def fit_pair_model(X: np.ndarray, y: np.ndarray, names: Sequence[str],
                   C: float = 1.0, seed: int = 13) -> PairModel:
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(C=C, max_iter=2000, class_weight="balanced",
                                  random_state=seed)),
    ])
    pipe.fit(X, y)
    return PairModel(pipe, list(names))
