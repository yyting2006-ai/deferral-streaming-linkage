"""Gold structure from official workflow links.

The city's own case-management record links a child request to a parent request
when the request is judged to belong to an existing case.  We treat the
partition induced by those links as the gold identity for every request that
takes part in at least one link ("adjudicated" / known domain K).

Two facts make this gold usable but not naive:
  * links are created after the fact, at operator discretion -> the induced
    partition is a *workflow* judgement, not a physical ground truth;
  * the same field carries both contemporaneous duplicates and long-lag
    recurrence, which are different relations.  We therefore expose a
    pre-registered conflation split.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .schema import Record

# Pre-registered split of the official link relation by lag.
CONCURRENT_DAYS = 7.0
RECURRENCE_DAYS = 30.0


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        p = self.parent.setdefault(x, x)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[x] = p
        return p

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


@dataclass
class LinkInfo:
    child: str
    parent: str
    lag_days: float
    same_address: bool
    same_service: bool
    same_block: bool


@dataclass
class Gold:
    city: str
    records: List[Record]
    by_id: Dict[str, Record]
    links: Dict[str, str]                     # resolved child -> parent
    raw_links: Dict[str, str]                 # as recorded (parent may be absent)
    link_info: List[LinkInfo]
    component: Dict[str, str]                 # rid -> component root (only K)
    known: set                                # rids in the known domain K
    component_members: Dict[str, List[str]]
    positives: set                            # frozenset pairs certified same
    n_pos_pairs: int = 0
    meta: Dict[str, object] = field(default_factory=dict)

    # ---------- gold queries ----------
    def same_issue(self, a: str, b: str) -> Optional[bool]:
        """True/False when certified by the workflow partition, else None."""
        ca, cb = self.component.get(a), self.component.get(b)
        if ca is None or cb is None:
            return None
        return ca == cb

    def decidable(self, a: str, b: str) -> bool:
        return a in self.known and b in self.known

    def is_positive(self, a: str, b: str) -> bool:
        return self.same_issue(a, b) is True

    def component_of(self, rid: str) -> Optional[str]:
        return self.component.get(rid)

    def subset(self, rids: Iterable[str]) -> Dict[str, List[str]]:
        """Gold partition restricted to a record set (certified components only)."""
        out: Dict[str, List[str]] = collections.defaultdict(list)
        for rid in rids:
            c = self.component.get(rid)
            if c is not None:
                out[c].append(rid)
        return dict(out)


def build_gold(city: str, records: List[Record], raw_links: Dict[str, str],
               ) -> Gold:
    by_id = {r.rid: r for r in records}
    resolved = {c: p for c, p in raw_links.items() if c in by_id and p in by_id}

    uf = UnionFind()
    infos: List[LinkInfo] = []
    for child, parent in resolved.items():
        uf.union(child, parent)
        rc, rp = by_id[child], by_id[parent]
        infos.append(LinkInfo(
            child=child, parent=parent,
            lag_days=(rc.when - rp.when).total_seconds() / 86400.0,
            same_address=bool(rc.addr.key) and rc.addr.key == rp.addr.key,
            same_block=bool(rc.addr.block) and rc.addr.block == rp.addr.block,
            same_service=rc.service_key == rp.service_key,
        ))

    component: Dict[str, str] = {}
    members: Dict[str, List[str]] = collections.defaultdict(list)
    for rid in uf.parent:
        root = uf.find(rid)
        component[rid] = root
        members[root].append(rid)
    known = set(component)

    positives: set = set()
    for root, mem in members.items():
        if len(mem) > 1:
            mem_sorted = sorted(mem)
            for i, a in enumerate(mem_sorted):
                for b in mem_sorted[i + 1:]:
                    positives.add((a, b))

    lags = sorted(i.lag_days for i in infos if i.lag_days >= 0)
    def q(p: float) -> float:
        return lags[min(len(lags) - 1, int(len(lags) * p))] if lags else 0.0

    meta = {
        "records": len(records), "raw_links": len(raw_links),
        "resolved_links": len(resolved),
        "known_records": len(known),
        "known_rate": round(len(known) / max(1, len(records)), 6),
        "components": len(members),
        "positive_pairs": len(positives),
        "max_component": max((len(m) for m in members.values()), default=0),
        "lag_p50": round(q(.5), 3), "lag_p90": round(q(.9), 3),
        "lag_p99": round(q(.99), 3),
        "lag_le_1d": round(sum(1 for v in lags if v <= 1) / max(1, len(lags)), 4),
        "lag_le_7d": round(sum(1 for v in lags if v <= CONCURRENT_DAYS) / max(1, len(lags)), 4),
        "lag_gt_30d": round(sum(1 for v in lags if v > RECURRENCE_DAYS) / max(1, len(lags)), 4),
        "same_address_rate": round(sum(i.same_address for i in infos) / max(1, len(infos)), 4),
        "same_block_rate": round(sum(i.same_block for i in infos) / max(1, len(infos)), 4),
        "same_service_rate": round(sum(i.same_service for i in infos) / max(1, len(infos)), 4),
        "negative_child_lag": sum(1 for v in lags if v < 0),
    }
    return Gold(city=city, records=records, by_id=by_id, links=resolved,
                raw_links=raw_links, link_info=infos, component=component,
                known=known, component_members=dict(members),
                positives=positives, n_pos_pairs=len(positives), meta=meta)


def conflation_buckets(gold: Gold) -> Dict[str, Dict[str, int]]:
    """Split official links into the two relations they conflate."""
    out = {"concurrent": {"links": 0, "pos_pairs": 0, "components": 0},
           "recurrence": {"links": 0, "pos_pairs": 0, "components": 0},
           "mixed": {"links": 0, "pos_pairs": 0, "components": 0}}
    per_comp: Dict[str, str] = {}
    for info in gold.link_info:
        root = gold.component.get(info.child, "")
        tag = "concurrent" if info.lag_days <= CONCURRENT_DAYS else "recurrence"
        out[tag]["links"] += 1
        prev = per_comp.get(root)
        per_comp[root] = tag if prev in (None, tag) else "mixed"
    for root, tag in per_comp.items():
        out[tag]["components"] += 1
        n = len(gold.component_members.get(root, []))
        out[tag]["pos_pairs"] += max(0, n * (n - 1) // 2)
    return out
