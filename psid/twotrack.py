"""Two-track linkage.

The diagnosis says the official relation is a disjunction: contemporaneous
duplicates, and recurrence of a chronic condition months later. A single linkage
operator with one threshold and one memory window cannot serve both, because the
window is the wrong carrier of memory: shortening it drops recurrence, lengthening
it re-admits the long tail into the merge decision.

Two-track separates the operators.

  Track A (open)  : merge arriving records into open issues, short window.
  Track B (long)  : link an arriving record to a *closed* past record by anchor
                    lookup, without merging. Cheap because it is a dictionary
                    probe, not a clustering decision.

The split is disjoint by construction: track B only proposes links whose time gap
exceeds the open window, so the two tracks never propose the same pair.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .methods import MERGE, NEW, PENDING, Issue, PrefixClusterer, StreamConfig, pair_features
from .schema import Record


class RecurrenceIndex:
    """Long-lived anchor index over *closed* past records."""

    def __init__(self, max_age_days: float = 540.0, chan_cap: int = 24):
        self.max_age = max_age_days
        self.chan_cap = chan_cap
        self.by_addr: Dict[Tuple[str, str], List[Tuple[str, int]]] = defaultdict(list)
        self.by_block: Dict[Tuple[str, str], List[Tuple[str, int]]] = defaultdict(list)
        self.recs: Dict[str, Record] = {}

    def add(self, rec: Record) -> None:
        self.recs[rec.rid] = rec
        day = int(rec.day)
        if rec.addr.key:
            self.by_addr[(rec.service_key, rec.addr.key)].append((rec.rid, day))
        if rec.addr.block:
            self.by_block[(rec.service_key, rec.addr.block)].append((rec.rid, day))

    def query(self, rec: Record, min_gap_days: float, k: int = 12) -> List[str]:
        """Past records sharing a structural anchor, at least `min_gap_days` older."""
        cutoff = rec.day - min_gap_days
        floor = rec.day - self.max_age
        out: List[str] = []
        seen = set()
        for table in (self.by_addr, self.by_block):
            key = ((rec.service_key, rec.addr.key) if table is self.by_addr
                   else (rec.service_key, rec.addr.block))
            for rid, day in table.get(key, ())[-self.chan_cap:]:
                if rid in seen or day > cutoff or day < floor:
                    continue
                seen.add(rid)
                out.append(rid)
                if len(out) >= k:
                    return out
        return out


class TwoTrackClusterer(PrefixClusterer):
    """Prefix-safe clusterer with an extra long-range linking track."""

    def __init__(self, cfg: StreamConfig, scorer, tau_recur: float = 0.90,
                 max_age_days: float = 540.0):
        super().__init__(cfg, scorer)
        self.recur = RecurrenceIndex(max_age_days=max_age_days)
        self.tau_recur = tau_recur
        self.link_pairs: List[Tuple[str, str]] = []
        self.link_scores: List[float] = []

    def _try_link(self, rec: Record, tokens, char3) -> None:
        cands = self.recur.query(rec, min_gap_days=self.cfg.window_days)
        if not cands:
            return
        best: Optional[str] = None
        best_score = -1.0
        reps = []
        for rid in cands:
            rep = self.recur.recs.get(rid)
            if rep is None:
                continue
            iv = Issue(iid="tmp", created=rep.day, last=rep.day)
            f = pair_features(rec, tokens, char3, iv, rep,
                              self._tokens(rep.rid, rep), self._char3(rep.rid, rep))
            reps.append((rep, f))
        if not reps:
            return
        scores = self._score_many(np.stack([f for _, f in reps]))
        k = int(np.argmax(scores))
        best_score = float(scores[k])
        best = reps[k][0].rid
        if best is not None and best_score >= self.tau_recur:
            self.link_pairs.append((rec.rid, best))
            self.link_scores.append(best_score)

    def on_record(self, rec: Record):
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        # track A
        action, iid, score, pairs = super().on_record(rec)
        # track B: link against records the open window cannot reach
        self._try_link(rec, tokens, char3)
        # the record now becomes reachable for future recurrence lookups
        self.recur.add(rec)
        return action, iid, score, pairs

    # ---- evaluation -------------------------------------------------------
    def proposed_pairs(self) -> List[Tuple[str, str]]:
        """Merge-created pairs (track A) plus long-range links (track B)."""
        out: List[Tuple[str, str]] = []
        for iv in self.issue_of.values():
            mem = iv.members
            if len(mem) < 2:
                continue
            mem = sorted(mem, key=lambda r: self.by_rid[r].day if r in self.by_rid else 0)
            for i, a in enumerate(mem):
                for b in mem[i + 1:]:
                    out.append((a, b))
        out.extend(self.link_pairs)
        return out
