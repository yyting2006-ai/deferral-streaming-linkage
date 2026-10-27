"""Stream replay driver: run a clusterer over a date range, prefix-safely."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .gold import Gold
from .metrics import b3, evaluate_partition
from .methods import PrefixClusterer, StaticClusterer, StreamConfig
from .schema import Record


@dataclass
class RunResult:
    name: str
    city: str
    window: Tuple[str, str]
    n_records: int
    n_merges: int
    n_pending: int
    ev: Dict[str, float]
    b3: Dict[str, float]
    latency_ms: Dict[str, float]
    extra: Dict[str, object] = field(default_factory=dict)

    def row(self) -> Dict[str, object]:
        d = {"name": self.name, "city": self.city,
             "window": "%s..%s" % self.window, "n": self.n_records,
             "merges": self.n_merges, "pending": self.n_pending}
        d.update({k: v for k, v in self.ev.items() if k != "n_records"})
        d.update({f"b3_{k}": v for k, v in self.b3.items()})
        d.update({f"lat_{k}": v for k, v in self.latency_ms.items()})
        d.update(self.extra)
        return d


def subset_records(records: Sequence[Record], start: Optional[str],
                   end: Optional[str]) -> List[Record]:
    out = []
    for r in records:
        d = r.when.date().isoformat()
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(r)
    return out


def _latency(sorted_ms: List[float], total_s: float) -> Dict[str, float]:
    def q(p: float) -> float:
        return round(sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * p))], 3) \
            if sorted_ms else 0.0
    return {"mean": round(sum(sorted_ms) / len(sorted_ms), 3) if sorted_ms else 0.0,
            "p50": q(.5), "p95": q(.95), "p99": q(.99),
            "max": round(sorted_ms[-1], 1) if sorted_ms else 0.0,
            "over500": sum(1 for x in sorted_ms if x > 500),
            "total_s": round(total_s, 1)}


def run_stream(clusterer: PrefixClusterer, records: Sequence[Record], gold: Gold,
               name: str = "run") -> RunResult:
    t_all = time.perf_counter()
    lat: List[float] = []
    merges = pending = 0
    for rec in records:
        t0 = time.perf_counter()
        action, _iid, _s, _pairs = clusterer.on_record(rec)
        lat.append((time.perf_counter() - t0) * 1000.0)
        if action == "merge":
            merges += 1
        elif action == "pending":
            pending += 1
    total = time.perf_counter() - t_all
    ids = {r.rid for r in records}
    part = clusterer.partition()
    ev = evaluate_partition(part, gold, ids)
    scope = [r for r in ids if r in gold.known]
    b3d = b3(part, gold, scope)
    return RunResult(name=name, city=gold.city,
                     window=(records[0].when.date().isoformat(),
                             records[-1].when.date().isoformat()) if records else ("", ""),
                     n_records=len(records), n_merges=merges, n_pending=pending,
                     ev=ev, b3=b3d, latency_ms=_latency(sorted(lat), total))


def run_static(scorer, cfg: StreamConfig, records: Sequence[Record], gold: Gold,
               name: str = "static") -> RunResult:
    t0 = time.perf_counter()
    assign, edges = StaticClusterer(cfg, scorer).compute(records)
    total = time.perf_counter() - t0
    ids = {r.rid for r in records}
    ev = evaluate_partition(assign, gold, ids)
    scope = [r for r in ids if r in gold.known]
    b3d = b3(assign, gold, scope)
    sizes: Dict[str, int] = {}
    for rid, c in assign.items():
        sizes[c] = sizes.get(c, 0) + 1
    merges = sum(1 for rid in assign if sizes[assign[rid]] > 1)
    return RunResult(name=name, city=gold.city,
                     window=(records[0].when.date().isoformat(),
                             records[-1].when.date().isoformat()) if records else ("", ""),
                     n_records=len(records), n_merges=merges, n_pending=0,
                     ev=ev, b3=b3d,
                     latency_ms={"mean": round(total * 1000 / max(1, len(records)), 3),
                                 "total_s": round(total, 1)})
