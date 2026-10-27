"""Metamorphic audit of prefix-safety.

Prefix-safety is a functional property, so it is testable without labels: replace
the text of a record at index s with an arbitrary string and require the system's
output on records 0..s-1 to be unchanged.  A prefix-safe pipeline passes by
construction; a pipeline that lets an arriving request see later records fails.
"""
from __future__ import annotations

import random
import string
from typing import Callable, Dict, List, Sequence, Tuple

from .schema import Record


def _scramble(rng: random.Random) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(24))


def _run(clusterer, records: Sequence[Record]) -> Dict[str, str]:
    for rec in records:
        clusterer.on_record(rec)
    return clusterer.partition()


def audit_prefix(clusterer_factory: Callable[[], object],
                 records: Sequence[Record],
                 indices: Sequence[int],
                 seed: int = 13) -> Dict[str, object]:
    """Return the number of prefix outputs that change when a later record is edited."""
    rng = random.Random(seed)
    base_clusterer = clusterer_factory()
    base = _run(base_clusterer, records)
    violations: List[Tuple[int, str, str, str]] = []
    per_index = []
    for s in indices:
        mutated = [Record(**{**rec.__dict__}) for rec in records[s:]]
        for m in mutated:
            m._addr = None
            m._text = None
        for m in records[:s]:
            m._addr = None
            m._text = None
        patched = list(records[:s]) + list(mutated)
        patched[s] = Record(
            rid=records[s].rid, city=records[s].city, when=records[s].when,
            service=records[s].service, service_detail=records[s].service_detail,
            address_raw=records[s].address_raw, zipcode=records[s].zipcode,
            description=_scramble(rng), lat=records[s].lat, lng=records[s].lng,
        )
        alt = _run(clusterer_factory(), patched)
        # compare the *induced co-clustering* on the prefix, not raw ids
        changed = 0
        for rid in base:
            if rid not in alt:
                continue
            for rid2 in base:
                if rid2 <= rid or rid2 not in alt:
                    continue
                same_before = base[rid] == base[rid2]
                same_after = alt[rid] == alt[rid2]
                if same_before != same_after:
                    changed += 1
                    if len(violations) < 20:
                        violations.append((s, rid, rid2, "split" if same_before else "join"))
        per_index.append({"s": s, "prefix_pairs_changed": changed})
    total = sum(p["prefix_pairs_changed"] for p in per_index)
    return {
        "n_records": len(records),
        "indices": list(indices),
        "prefix_pair_changes": total,
        "per_index": per_index,
        "examples": violations[:5],
        "passed": total == 0,
    }
