"""Certified merge: distribution-free false-merge control for a merge stream.

The operational error in streaming issue linkage is a *false merge*: two distinct
issues joined into one.  Organisers of the underlying civic workflows say the
dominant failure mode is over-merging, and a fixed score threshold gives no
statement at all about how often that happens.

We cast the deployed system as an ordered hypothesis stream: at every arrival the
system tests one hypothesis, "this request belongs to that open issue".  Merging
is a rejection of the null.  Then

    false-merge proportion  =  (# wrong merges) / (# merges)

is exactly the false discovery proportion of the rejection set, so controlling it
is online FDR control.  Two ingredients are needed:

1. a p-value valid for a *single* pair, from a labelled calibration window that
   precedes the stream (split-conformal on the non-match scores);
2. an online FDR procedure over the ordered decisions (LORD++, Ramdas et al. 2017).

Both only ever read the past, so the whole construction is prefix-safe.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from .methods import MERGE, NEW, PENDING, Issue, PrefixClusterer, StreamConfig
from .schema import Record


# --------------------------------------------------------------------------- #
# 1. split-conformal p-values on pair scores
# --------------------------------------------------------------------------- #
class ConformalPairPValue:
    """p(s) = (1 + #{calibration non-matches with score >= s}) / (n0 + 1).

    This is the standard "all-negatives" conformal construction: it is valid for
    H0 = "the pair is not the same issue" whenever the calibration non-matches and
    the test pair are exchangeable.
    """

    def __init__(self, neg_scores: Sequence[float], n_pos: int = 0):
        self.sorted = np.sort(np.asarray(neg_scores, dtype=np.float64))
        self.n = len(self.sorted)
        self.n_pos = n_pos

    def __call__(self, score: float) -> float:
        if self.n == 0:
            return 1.0
        # number of calibration non-matches with score >= score
        idx = np.searchsorted(self.sorted, score, side="left")
        ge = self.n - idx
        return (1.0 + ge) / (self.n + 1.0)

    def min_p(self) -> float:
        return 1.0 / (self.n + 1.0)

    def threshold_for(self, alpha: float) -> float:
        """Largest score whose p-value is <= alpha (a fixed-threshold view of the
        conformal rule, useful for comparing against tuned thresholds)."""
        k = math.floor(alpha * (self.n + 1)) - 1
        if k <= 0:
            return float("inf")
        return float(self.sorted[self.n - k]) if k <= self.n else float("inf")


# --------------------------------------------------------------------------- #
# 2. LORD++ online FDR
# --------------------------------------------------------------------------- #
class LORDPlusPlus:
    """LORD++ (Ramdas et al., 2017) with the published gamma sequence."""

    def __init__(self, alpha: float, n_extra: int = 20000):
        self.alpha = alpha
        self._gamma = self._make_gamma(n_extra)
        self.w0 = alpha            # alpha-wealth
        self.tau = 0               # index of last rejection
        self.t = 0                 # number of hypotheses tested
        self.rejections = 0
        self.alpha_levels: List[float] = []

    @staticmethod
    def _make_gamma(n: int) -> np.ndarray:
        g = np.empty(n + 1)
        g[0] = 0.0
        raw = np.zeros(n + 1)
        for j in range(1, n + 1):
            raw[j] = math.log(max(j, 2)) / (j * math.exp(math.sqrt(math.log(max(j, 2)))))
        c = 1.0 / raw.sum()
        g[1:] = c * raw[1:]
        return g

    def next_alpha(self) -> float:
        j = self.t + 1 - self.tau
        j = min(j, len(self._gamma) - 1)
        a = self._gamma[j] * self.w0
        self.alpha_levels.append(a)
        return a

    def update(self, rejected: bool, alpha_t: float) -> None:
        self.t += 1
        if rejected:
            self.rejections += 1
            self.tau = self.t
            self.w0 = self.w0 + self.alpha - alpha_t


# --------------------------------------------------------------------------- #
# 3. the clusterer that uses the certificate
# --------------------------------------------------------------------------- #
@dataclass
class FDREvent:
    t: int
    rid: str
    score: float
    p: float
    alpha_t: float
    merged: bool
    issue_id: Optional[str]


class CertifiedClusterer(PrefixClusterer):
    """Prefix-safe clusterer whose merge rule is an online FDR procedure.

    `t_pending` still applies: candidates between `t_pending` and the calibrated
    level are parked instead of merged, which is the deployable form of deferral.
    """

    def __init__(self, cfg: StreamConfig, scorer, pvalue: ConformalPairPValue,
                 alpha: float = 0.10, use_pending: bool = True,
                 fixed_threshold: Optional[float] = None):
        super().__init__(cfg, scorer)
        self.pvalue = pvalue
        self.fdr = LORDPlusPlus(alpha)
        self.use_pending = use_pending
        self.fixed_threshold = fixed_threshold
        self.events: List[FDREvent] = []

    def on_record(self, rec: Record):
        import numpy as np

        day = int(rec.day)
        if day != self._cur_day:
            self._cur_day = day
            self.index.prune(day)
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        cand_ids = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                        use_text=self.cfg.use_text)
        from .methods import pair_features
        usable = []
        for iid in cand_ids:
            issue = self.index.issues.get(iid)
            if issue is None or issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid.get(issue.reps[0])
            if rep is not None:
                usable.append((issue, rep))
        self.by_rid[rec.rid] = rec
        best_issue, best_score = None, -1.0
        if usable:
            feats = np.stack([pair_features(rec, tokens, char3, iss, rep,
                                            self._tokens(rep.rid, rep),
                                            self._char3(rep.rid, rep))
                              for iss, rep in usable])
            scores = self._score_many(feats)
            k = int(np.argmax(scores))
            best_score = float(scores[k])
            best_issue = usable[k][0]

        if best_issue is None:
            iv = self._new_issue(rec, tokens)
            self.assign[rec.rid] = iv.iid
            self.events.append(FDREvent(self.fdr.t + 1, rec.rid, -1.0, 1.0, 0.0,
                                       False, None))
            return NEW, iv.iid, 0.0, []

        if best_issue.reps:
            self.top_log.append((rec.rid, best_issue.reps[0], best_score))
        p = float(self.pvalue(best_score))
        alpha_t = self.fdr.next_alpha()
        take = p <= alpha_t
        self.fdr.update(take, alpha_t)
        self.events.append(FDREvent(self.fdr.t, rec.rid, best_score, p, alpha_t,
                                    take, best_issue.iid if take else None))

        if take:
            new_pairs = [(rec.rid, o) for o in best_issue.members]
            self.merge_log.append((rec.rid, best_issue.reps[0]))
            best_issue.absorb(rec)
            self.assign[rec.rid] = best_issue.iid
            self.decisions.append((rec.rid, MERGE, best_score))
            return MERGE, best_issue.iid, best_score, new_pairs
        if self.use_pending and best_score >= self.cfg.t_pending:
            iv = self._new_issue(rec, tokens, tag="P")
            self.pending_ids.add(rec.rid)
            self.assign[rec.rid] = iv.iid
            self.decisions.append((rec.rid, PENDING, best_score))
            return PENDING, iv.iid, best_score, []
        iv = self._new_issue(rec, tokens)
        self.assign[rec.rid] = iv.iid
        self.decisions.append((rec.rid, NEW, best_score))
        return NEW, iv.iid, best_score, []

    # audit -------------------------------------------------------------
    def audit(self) -> dict:
        tested = self.fdr.t
        return {"alpha": self.fdr.alpha, "hypotheses": tested,
                "merges": self.fdr.rejections,
                "merge_rate": self.fdr.rejections / max(1, tested),
                "alpha_levels": self.fdr.alpha_levels}
