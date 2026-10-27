"""Prefix-safe streaming issue clusterers and their deliberately leaky twins.

Design contract
---------------
`on_record(rec, t)` may read only: (a) the record itself, (b) issues created by
records that arrived at or before `t`.  The index, the representatives and the
thresholds are all frozen with respect to the future.  `LeakyClusterer` violates
each of those three properties in a switchable way so the audit can price them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .schema import Record, norm_text

MERGE, NEW, PENDING = "merge", "new", "pending"


# --------------------------------------------------------------------------- #
# issue state
# --------------------------------------------------------------------------- #
@dataclass
class Issue:
    iid: str
    created: float           # day ordinal (float)
    last: float
    n: int = 1
    reps: List[str] = field(default_factory=list)        # representative rids (<=3)
    addr_keys: Set[str] = field(default_factory=set)
    block_keys: Set[str] = field(default_factory=set)
    service_keys: Set[str] = field(default_factory=set)
    zips: Set[str] = field(default_factory=set)
    token_counts: Dict[str, float] = field(default_factory=dict)
    centroid: Optional[np.ndarray] = None            # L2-normalised tfidf centroid
    members: List[str] = field(default_factory=list)

    def absorb(self, rec: Record) -> None:
        self.n += 1
        self.last = rec.day
        self.members.append(rec.rid)
        if rec.addr.key:
            self.addr_keys.add(rec.addr.key)
        if rec.addr.block:
            self.block_keys.add(rec.addr.block)
        if rec.service_key:
            self.service_keys.add(rec.service_key)
        if rec.addr.zip5:
            self.zips.add(rec.addr.zip5)
        if len(self.reps) < 3:
            self.reps.append(rec.rid)


# --------------------------------------------------------------------------- #
# prefix-safe indexes
# --------------------------------------------------------------------------- #
def _nearest(entries, day: int, cap: int):
    """The `cap` entries whose day is closest to `day`, chronologically listed."""
    n = len(entries)
    if n <= cap:
        return entries
    lo, hi = 0, n - 1
    while lo < hi:                      # entries are in calendar order
        mid = (lo + hi) // 2
        if entries[mid][1] < day:
            lo = mid + 1
        else:
            hi = mid
    start = max(0, lo - cap // 2)
    start = min(start, n - cap)
    return entries[start:start + cap]


class PrefixIndex:
    """Token inverted index with expiry and an address hierarchy index."""

    def __init__(self, window_days: float, posting_cap: int = 2000):
        self.window = window_days
        self.posting_cap = posting_cap
        self.postings: Dict[str, List[Tuple[str, int]]] = {}
        self.by_addr: Dict[Tuple[str, str], List[str]] = {}
        self.by_block: Dict[Tuple[str, str], List[str]] = {}
        self.by_service: Dict[str, List[str]] = {}
        self.issues: Dict[str, Issue] = {}
        self.idf: Dict[str, float] = {}
        self.df: Dict[str, int] = {}
        self.n_docs = 0

    def add(self, issue: Issue, rec: Record, tokens: Sequence[str]) -> None:
        self.issues[issue.iid] = issue
        seen = set()
        for tok in tokens:
            if tok in seen:
                continue
            seen.add(tok)
            lst = self.postings.setdefault(tok, [])
            lst.append((issue.iid, int(rec.day)))
            if len(lst) > self.posting_cap:
                del lst[: len(lst) - self.posting_cap]
        if rec.addr.key:
            self.by_addr.setdefault((rec.service_key, rec.addr.key), []).append(
                (issue.iid, int(rec.day)))
        if rec.addr.block:
            self.by_block.setdefault((rec.service_key, rec.addr.block), []).append(
                (issue.iid, int(rec.day)))
        self.by_service.setdefault(rec.service_key, []).append(issue.iid)
        for tok in seen:
            self.df[tok] = self.df.get(tok, 0) + 1
        self.n_docs += 1

    def prune(self, now: int) -> None:
        cutoff = now - self.window
        dead = [iid for iid, iv in self.issues.items() if iv.last < cutoff]
        for iid in dead:
            self.issues.pop(iid, None)
        if dead:
            dset = set(dead)
            for key in list(self.by_addr):
                lst = [x for x in self.by_addr[key] if x[0] not in dset]
                if lst:
                    self.by_addr[key] = lst
                else:
                    del self.by_addr[key]
            for key in list(self.by_block):
                lst = [x for x in self.by_block[key] if x[0] not in dset]
                if lst:
                    self.by_block[key] = lst
                else:
                    del self.by_block[key]
            for key in list(self.by_service):
                lst = [x for x in self.by_service[key] if x not in dset]
                if lst:
                    self.by_service[key] = lst
                else:
                    del self.by_service[key]

    def idf_of(self, tok: str) -> float:
        return math.log(1.0 + self.n_docs / (1.0 + self.df.get(tok, 0)))

    def candidates(self, rec: Record, tokens: Sequence[str], k: int = 200,
                   use_text: bool = True, chan_cap: int = 40) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        # Each structured channel contributes at most its `chan_cap` most recent
        # issues.  Without this bound a pre-seeded (future-visible) index makes a
        # hot address key yield hundreds of candidates per arrival.
        for iid, _day in _nearest(self.by_addr.get((rec.service_key, rec.addr.key), ()),
                                  rec.day, chan_cap):
            if iid not in seen:
                seen.add(iid); out.append(iid)
        for iid, _day in _nearest(self.by_block.get((rec.service_key, rec.addr.block), ()),
                                  rec.day, chan_cap):
            if iid not in seen:
                seen.add(iid); out.append(iid)
        if use_text:
            scored: Dict[str, float] = {}
            top = sorted(set(tokens), key=lambda t: -self.idf_of(t))[:8]
            for tok in top:
                w = self.idf_of(tok)
                for iid, _day in self.postings.get(tok, ())[-k:]:
                    scored[iid] = scored.get(iid, 0.0) + w
            for iid, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:k]:
                if iid not in seen:
                    seen.add(iid); out.append(iid)
        return out


# --------------------------------------------------------------------------- #
# pair features (arrival-time only)
# --------------------------------------------------------------------------- #
def tokens_of(text: str) -> List[str]:
    return [t for t in norm_text(text).split() if len(t) > 1]


def text_cosine(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    ca: Dict[str, int] = {}
    cb: Dict[str, int] = {}
    for t in a:
        ca[t] = ca.get(t, 0) + 1
    for t in b:
        cb[t] = cb.get(t, 0) + 1
    dot = sum(v * cb.get(t, 0) for t, v in ca.items())
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


FEATURE_NAMES = [
    "same_addr", "same_block", "same_street", "same_zip", "same_service",
    "addr_missing", "desc_missing", "cos", "jac", "char3_jac",
    "log_gap", "same_day", "gap_le_7", "issue_size", "num_dist_100",
    "geo_dist", "service_rare", "len_ratio",
    # latent street representation: 0.0 when either side lacks one
    "addr_emb_cos", "addr_emb_dist",
    "geo_missing",
]


def _char3(text: str) -> List[str]:
    t = norm_text(text).replace(" ", "")
    return [t[i:i + 3] for i in range(max(0, len(t) - 2))] if t else []


def pair_features(rec: Record, rec_tokens: Sequence[str], rec_char3: Sequence[str],
                  issue: Issue, rep: Record,
                  rep_tokens: Sequence[str], rep_char3: Sequence[str]) -> np.ndarray:
    a, b = rec.addr, rep.addr
    same_addr = 1.0 if (a.key and a.key == b.key) else 0.0
    same_block = 1.0 if (a.block and a.block == b.block) else 0.0
    same_street = 1.0 if (a.street and a.street == b.street) else 0.0
    same_zip = 1.0 if (a.zip5 and a.zip5 == b.zip5) else 0.0
    same_service = 1.0 if rec.service_key == rep.service_key else 0.0
    addr_missing = 1.0 if (not a.street or not b.street) else 0.0
    desc_missing = 1.0 if (not rec.description.strip() or not rep.description.strip()) else 0.0
    cos = text_cosine(rec_tokens, rep_tokens)
    jac = jaccard(rec_tokens, rep_tokens)
    c3 = jaccard(rec_char3, rep_char3)
    gap = max(0.0, rec.day - issue.last)
    same_day = 1.0 if gap == 0 else 0.0
    gap_le7 = 1.0 if gap <= 7 else 0.0
    size = min(10.0, math.log1p(issue.n)) / math.log(11.0)
    ndist = 0.0
    if a.number.isdigit() and b.number.isdigit():
        ndist = min(1.0, abs(int(a.number) - int(b.number)) / 100.0)
    if rec.lat is not None and rep.lat is not None:
        dlat = (rec.lat - rep.lat) * 111.0
        dlng = (rec.lng - rep.lng) * 111.0 * math.cos(math.radians(rec.lat))
        geo = math.sqrt(dlat * dlat + dlng * dlng)
        geo_missing = 0.0
    else:
        geo = 0.0
        geo_missing = 1.0
    geo = min(geo, 20.0) / 20.0
    rare = 1.0 if len(rec.service_key) > 0 else 0.0
    len_ratio = min(len(rec_tokens), len(rep_tokens)) / max(1, max(len(rec_tokens), len(rep_tokens)))
    ea = getattr(rec, "emb", None)
    eb = getattr(rep, "emb", None)
    if ea is not None and eb is not None:
        ecos = float(np.dot(ea, eb))
        edist = -float(np.linalg.norm(ea - eb))
    else:
        ecos = 0.0
        edist = 0.0
    return np.array([same_addr, same_block, same_street, same_zip, same_service,
                     addr_missing, desc_missing, cos, jac, c3,
                     math.log1p(gap) / 5.0, same_day, gap_le7, size, ndist,
                     geo, rare, len_ratio, ecos, edist, geo_missing], dtype=np.float32)


# --------------------------------------------------------------------------- #
# clusterers
# --------------------------------------------------------------------------- #
@dataclass
class StreamConfig:
    window_days: float = 60.0
    k_candidates: int = 60
    use_text: bool = True
    t_merge: float = 0.5
    t_pending: float = 0.35
    pending_absorbs: bool = False
    max_cluster: int = 20
    prior_merge: float = 0.0        # leaky-only: global prior from the future


class PrefixClusterer:
    """The prefix-safe engine.  `scorer` maps feature vectors to a score."""

    def __init__(self, cfg: StreamConfig,
                 scorer: Optional[Callable[[np.ndarray], np.ndarray]] = None):
        self.cfg = cfg
        self.scorer = scorer
        self.index = PrefixIndex(cfg.window_days)
        self.by_rid: Dict[str, Record] = {}
        self.assign: Dict[str, str] = {}
        self.issue_of: Dict[str, Issue] = {}
        self._counter = 0
        self.pending_ids: Set[str] = set()
        self.decisions: List[Tuple[str, str, float]] = []   # (rid, action, score)
        self.pairs_created = 0
        self._cur_day = -1
        self._pair_cap = 50
        self.merge_log: List[Tuple[str, str]] = []   # (new record, representative it was scored against)
        self.feat_log: List[Tuple[str, str, float, tuple]] = []  # optional per-decision feature dump
        self.log_features: bool = False
        self.top_log: List[Tuple[str, str, float]] = []  # (record, top rep, top score) every arrival
        self._tok_cache: Dict[str, List[str]] = {}
        self._c3_cache: Dict[str, List[str]] = {}

    # -- helpers ------------------------------------------------------------
    def _tokens(self, rid: str, rec: Record) -> List[str]:
        t = self._tok_cache.get(rid)
        if t is None:
            t = tokens_of(rec.text)
            self._tok_cache[rid] = t
        return t

    def _char3(self, rid: str, rec: Record) -> List[str]:
        t = self._c3_cache.get(rid)
        if t is None:
            t = _char3(rec.description)
            self._c3_cache[rid] = t
        return t

    def _new_issue(self, rec: Record, tokens: Sequence[str], tag: str = "I") -> Issue:
        self._counter += 1
        iv = Issue(iid=f"{tag}{self._counter:07d}", created=rec.day, last=rec.day)
        iv.absorb(rec)
        self.index.add(iv, rec, tokens)
        self.issue_of[iv.iid] = iv
        return iv

    def _score(self, feats: np.ndarray) -> float:
        if self.scorer is None:
            return 0.0
        out = self.scorer(feats.reshape(1, -1))
        return float(out[0])

    def _score_many(self, feats: np.ndarray) -> np.ndarray:
        if self.scorer is None or len(feats) == 0:
            return np.zeros(len(feats), dtype=np.float32)
        return self.scorer(feats)

    # -- main entry ---------------------------------------------------------
    def on_record(self, rec: Record) -> Tuple[str, Optional[str], float, List[Tuple[str, str]]]:
        """Returns (action, issue_id, score, new_pairs)."""
        day = int(rec.day)
        if day != self._cur_day:
            self._cur_day = day
            self.index.prune(day)
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        cand_ids = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                        use_text=self.cfg.use_text)
        usable: List[Tuple[Issue, Record]] = []
        for iid in cand_ids:
            issue = self.index.issues.get(iid)
            if issue is None or issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid.get(issue.reps[0])
            if rep is not None:
                usable.append((issue, rep))
        self.by_rid[rec.rid] = rec
        cfg = self.cfg
        best_issue: Optional[Issue] = None
        best_score = -1.0
        if usable:
            feats = np.stack([pair_features(rec, tokens, char3, iss, rep,
                                           self._tokens(rep.rid, rep),
                                           self._char3(rep.rid, rep))
                              for iss, rep in usable])
            scores = self._score_many(feats)
            k = int(np.argmax(scores))
            best_score = float(scores[k])
            best_issue = usable[k][0]
        if best_issue is not None and best_issue.reps:
            self.top_log.append((rec.rid, best_issue.reps[0], best_score))
            if self.log_features:
                k = int(np.argmax(scores)) if usable else -1
                self.feat_log.append((rec.rid, best_issue.reps[0], best_score,
                                      tuple(float(x) for x in feats[k])))
        new_pairs: List[Tuple[str, str]] = []
        if best_issue is not None and best_score >= cfg.t_merge:
            new_pairs = [(rec.rid, other) for other in best_issue.members[:self._pair_cap]]
            self.merge_log.append((rec.rid, best_issue.reps[0]))
            best_issue.absorb(rec)
            self.assign[rec.rid] = best_issue.iid
            self.decisions.append((rec.rid, MERGE, best_score))
            return MERGE, best_issue.iid, best_score, new_pairs
        if best_issue is not None and best_score >= cfg.t_pending:
            iv = self._new_issue(rec, tokens, tag="P")
            self.pending_ids.add(rec.rid)
            self.assign[rec.rid] = iv.iid
            self.decisions.append((rec.rid, PENDING, best_score))
            return PENDING, iv.iid, best_score, []
        iv = self._new_issue(rec, tokens)
        self.assign[rec.rid] = iv.iid
        self.decisions.append((rec.rid, NEW, max(0.0, best_score)))
        return NEW, iv.iid, max(0.0, best_score), []

    # -- offline accessors ---------------------------------------------------
    def partition(self) -> Dict[str, str]:
        return dict(self.assign)

    def pairs(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for iv in self.issue_of.values():
            mem = iv.members
            if len(mem) < 2:
                continue
            mem_sorted = sorted(mem, key=lambda r: self.by_rid[r].day if r in self.by_rid else 0)
            for i, a in enumerate(mem_sorted):
                for b in mem_sorted[i + 1:]:
                    out.append((a, b))
        return out


class LeakyRetrievalClusterer(PrefixClusterer):
    """Same algorithm, same thresholds -- only candidate visibility differs.

    The index is pre-seeded with every record of the evaluation window, so a
    request that arrives on day 1 can retrieve and merge into an issue that a
    *later* request will create.  This is exactly what a batch pipeline that
    "clusters the corpus and then reads the clusters off in time order" does, and
    it is the single most common way streaming numbers get inflated.
    """

    def __init__(self, cfg: StreamConfig, scorer, window_records: Sequence[Record]):
        super().__init__(cfg, scorer)
        self._seed = {r.rid: r for r in window_records}
        self._seed_issue: Dict[str, str] = {}
        self._seeded = False

    def _seed_index(self) -> None:
        if self._seeded:
            return
        self._seeded = True
        for rid in sorted(self._seed, key=lambda k: self._seed[k].day):
            rec = self._seed[rid]
            tokens = self._tokens(rid, rec)
            iv = self._new_issue(rec, tokens, tag="S")
            self._seed_issue[rid] = iv.iid

    def on_record(self, rec: Record):
        self._seed_index()
        day = int(rec.day)
        own = self._seed_issue.get(rec.rid)
        if own is not None:
            self.index.issues.pop(own, None)      # never match yourself
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        cand_ids = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                        use_text=self.cfg.use_text)
        usable: List[Tuple[Issue, Record]] = []
        for iid in cand_ids:
            issue = self.index.issues.get(iid)
            if issue is None or issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid.get(issue.reps[0]) or self._seed.get(issue.reps[0])
            if rep is not None:
                usable.append((issue, rep))
        self.by_rid.setdefault(rec.rid, rec)
        best_issue, best_score = None, -1.0
        if usable:
            feats = np.stack([pair_features(rec, tokens, char3, iss, rep,
                                           self._tokens(rep.rid, rep),
                                           self._char3(rep.rid, rep))
                              for iss, rep in usable])
            scores = self._score_many(feats)
            k = int(np.argmax(scores))
            best_score, best_issue = float(scores[k]), usable[k][0]
        if best_issue is not None and best_score >= self.cfg.t_merge:
            pairs = [(rec.rid, o) for o in best_issue.members]
            best_issue.absorb(rec)
            self.assign[rec.rid] = best_issue.iid
            self.by_rid[rec.rid] = rec
            self.decisions.append((rec.rid, MERGE, best_score))
            return MERGE, best_issue.iid, best_score, pairs
        if best_issue is not None and best_score >= self.cfg.t_pending:
            iv = self._new_issue(rec, tokens, tag="P")
            self.index.issues.pop(own, None) if own else None
            self.pending_ids.add(rec.rid)
            self.assign[rec.rid] = iv.iid
            self.by_rid[rec.rid] = rec
            self.decisions.append((rec.rid, PENDING, best_score))
            return PENDING, iv.iid, best_score, []
        iv = None
        if own is not None and own in self.issue_of:
            iv = self.issue_of[own]
            self.index.issues[own] = iv
        if iv is None:
            iv = self._new_issue(rec, tokens)
        self.assign[rec.rid] = iv.iid
        self.by_rid[rec.rid] = rec
        self.decisions.append((rec.rid, NEW, max(0.0, best_score)))
        return NEW, iv.iid, max(0.0, best_score), []


class StaticClusterer:
    """Post-hoc (omniscient) twin used to price retrieval leakage.

    The whole evaluation window is indexed before any decision is made, exactly
    as a batch pipeline that "clusters the corpus" would.  Every record may
    retrieve candidates from the future.  Edges above tau are merged with
    union-find, which is the standard single-linkage-on-thresholded-edges recipe.
    """

    def __init__(self, cfg: StreamConfig,
                 scorer: Callable[[np.ndarray], np.ndarray]):
        self.cfg = cfg
        self.scorer = scorer

    def compute(self, records: List[Record]) -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
        cfg = self.cfg
        tok: Dict[str, List[str]] = {}
        c3: Dict[str, List[str]] = {}
        for r in records:
            tok[r.rid] = tokens_of(r.text)
            c3[r.rid] = _char3(r.description)
        # blocking on (service, address block) and (service, day bucket)
        blocks: Dict[Tuple[str, str], List[Record]] = {}
        for r in records:
            key = (r.service_key, r.addr.street or r.addr.block or "")
            blocks.setdefault(key, []).append(r)
        edges: List[Tuple[str, str]] = []
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        day_ord = {r.rid: r.day for r in records}
        win = cfg.window_days
        for key, group in blocks.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda r: r.day)
            n = len(group)
            for i in range(n):
                a = group[i]
                max_n = 400
                for j in range(i + 1, min(n, i + 1 + max_n)):
                    b = group[j]
                    if b.day - a.day > win:
                        break
                    iv = Issue(iid="tmp", created=a.day, last=a.day)
                    f = pair_features(b, tok[b.rid], c3[b.rid], iv, a,
                                      tok[a.rid], c3[a.rid])
                    s = float(self.scorer(f.reshape(1, -1))[0])
                    if s >= cfg.t_merge:
                        edges.append((a.rid, b.rid))
                        union(a.rid, b.rid)
        assign = {r.rid: find(r.rid) for r in records}
        return assign, edges

# Public aliases
Clusterer = PrefixClusterer
