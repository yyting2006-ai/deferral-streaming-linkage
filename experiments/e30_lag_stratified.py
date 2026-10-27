"""E30: stratify the deferral effect by the lag of the relation.

The mechanism claim is that provisional visibility pays because the evidence that
distinguishes two requests arrives *after* the request does. If that is right, the
gain should be concentrated in long-lag pairs and the losses in short-lag ones; if the
gain is spread evenly across lags, the mechanism story is wrong and the effect is
something else.

Two stratifications, both from partitions and merge logs that E26 dumps:

  * certified positive pairs, by |day(a) - day(b)|: found by both / only by the
    arrival-time engine / only by the deferred engine / by neither;
  * wrong merge decisions, by the same lag between the record and the representative
    it was merged into.

No replay here: this only reads the two dumps.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_san_diego
from psid.gold import build_gold
from psid.replay import subset_records

OUT = Path(__file__).resolve().parents[1] / "results"
BUCKETS = [(0.0, 1.0, "<=1d"), (1.0, 7.0, "1-7d"), (7.0, 30.0, "8-30d"),
           (30.0, 1e9, ">30d")]


def bucket(lag):
    for lo, hi, name in BUCKETS:
        if lo < lag <= hi or (lo == 0.0 and lag <= hi):
            return name
    return ">30d"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(OUT / "e26_dump_base.json"))
    ap.add_argument("--defer", default=str(OUT / "e26_dump_d0p5.json"))
    ap.add_argument("--out", default=str(OUT / "e30_lag_stratified.json"))
    args = ap.parse_args()

    rec, links = load_san_diego()
    gold = build_gold("san_diego", rec, links)
    cf = subset_records(rec, "2024-05-01", "2024-05-31")
    by = {r.rid: r for r in cf}
    ids = set(by)

    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    defer = json.loads(Path(args.defer).read_text(encoding="utf-8"))
    pb, pd = base["assign"], defer["assign"]

    # ---- certified positive pairs, stratified by lag -----------------------
    comp = defaultdict(list)
    for rid in by:
        c = gold.component.get(rid)
        if c is not None:
            comp[c].append(rid)
    stat = defaultdict(lambda: {"both": 0, "base_only": 0, "defer_only": 0,
                                "neither": 0})
    for members in comp.values():
        if len(members) < 2:
            continue
        ms = sorted(members)
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                lag = abs((by[a].when - by[b].when).total_seconds()) / 86400.0
                k = bucket(lag)
                fb = pb.get(a) == pb.get(b)
                fd = pd.get(a) == pd.get(b)
                if fb and fd:
                    stat[k]["both"] += 1
                elif fb:
                    stat[k]["base_only"] += 1
                elif fd:
                    stat[k]["defer_only"] += 1
                else:
                    stat[k]["neither"] += 1

    print(f"{'lag':>8} {'both':>7} {'base_only':>10} {'defer_only':>11} "
          f"{'neither':>8} {'n':>8} {'d_recall':>9}")
    rows = []
    for _, _, name in BUCKETS:
        s = stat[name]
        n = sum(s.values())
        if not n:
            continue
        d = (s["defer_only"] - s["base_only"]) / n
        rows.append({"bucket": name, **s, "n": n, "delta_recall": round(d, 4)})
        print(f"{name:>8} {s['both']:>7} {s['base_only']:>10} {s['defer_only']:>11} "
              f"{s['neither']:>8} {n:>8} {d:>9.4f}")

    # ---- wrong merges, stratified by lag to the representative -------------
    wrong = {}
    for tag, dump in (("base", base), ("defer", defer)):
        per = defaultdict(int)
        for rid, rep in dump["merge_log"]:
            if rid not in by or rep not in by:
                continue
            rel = gold.same_issue(rid, rep)
            if rel is None or rel:
                continue
            lag = abs((by[rid].when - by[rep].when).total_seconds()) / 86400.0
            per[bucket(lag)] += 1
        wrong[tag] = per

    print(f"\n{'lag':>8} {'wrong_base':>11} {'wrong_defer':>12} {'delta':>7}")
    wrows = []
    for _, _, name in BUCKETS:
        b, d = wrong["base"].get(name, 0), wrong["defer"].get(name, 0)
        wrows.append({"bucket": name, "wrong_base": b, "wrong_defer": d,
                      "delta": d - b})
        print(f"{name:>8} {b:>11} {d:>12} {d-b:>+7}")

    Path(args.out).write_text(json.dumps(
        {"positive_pairs_by_lag": rows, "wrong_merges_by_lag": wrows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {Path(args.out).name}")


if __name__ == "__main__":
    main()
