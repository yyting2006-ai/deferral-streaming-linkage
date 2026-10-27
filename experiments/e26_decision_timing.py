"""E26: decision timing --- does bounded deferral buy back what irreversibility costs?

Motivation.  E25 showed that a ceiling-matched *batch* clustering is 0.019--0.045
F1 ahead of the deployed streaming engine, and the difference is almost entirely
recall.  The mechanism the paper offers for that gap is irreversibility: a streaming
engine commits a marginal merge at arrival, and if it is wrong the record can never
be re-attached.  If that diagnosis is right, then a *bounded* amount of waiting
should recover part of the gap without leaving the prefix-safe protocol.

The protocol question this experiment turns on.  The paper's prefix-safety is
"the decision at t may not read anything after t".  The property a deployed system
actually owes its operator is weaker: the *output emitted at tau* may not depend on
anything after tau.  A decision committed at t+D from data up to t+D satisfies that,
because it is emitted at t+D.  So bounded deferral is admissible, and the whole
defer action space the paper defines but never analyses becomes live.

The policy.  At arrival the record's best candidate is scored against all open
issues, including provisional ones.

  * score >= tau + delta   -> merge now (confident; irreversible, as deployed).
  * tau <= score < tau+delta -> hold: the record becomes a *provisional* issue that
    is visible to later arrivals, but its merge is committed only at t+D, against
    whatever index exists then.
  * score < tau            -> new issue now.

At the deadline the provisional issue is re-scored.  If it now has a match at
>= tau it is merged (carrying every member that attached to it in the meantime),
otherwise it is finalised as its own issue.  delta = 0 is exactly the deployed
engine, which is the baseline; delta -> inf defers every non-singleton decision.

What makes this falsifiable.  If bounded deferral does not improve the frontier at
the paper's own operating point (tau calibrated on March to a 0.40 decision-level
false-merge budget), then the irreversibility explanation for the batch gap is
wrong, or the gap comes from somewhere else, and the idea dies.  Deferral that only
costs latency without buying recall is a negative result worth reporting as such.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psid.data import load_chicago, load_san_diego
from psid.decision_metrics import decision_fmp
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.methods import (MERGE, NEW, PENDING, Issue, PrefixIndex, StreamConfig,
                          _char3, pair_features, tokens_of)
from psid.replay import subset_records
from psid.train_cand import build_candidate_pairs
from experiments.e11d_gbdt import GBT
from experiments.logit_scorer import Logit

OUT = Path(__file__).resolve().parents[1] / "results"
_last_state = {}   # holds the partition and merge log of the last policy run

# The calibrated operating point from E23 (all three windows land on the same tau).
TAU = 0.0358


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class DeferralClusterer:
    """Prefix-safe-at-emission streaming linker with bounded deferral.

    The index holds committed issues *and* provisional ones, so a later arrival can
    attach to a record whose own merge decision has not been committed yet.  That
    visibility is what makes deferral informative rather than merely late.
    """

    def __init__(self, cfg: StreamConfig, scorer, delta: float = 0.0,
                 deadline_days: float = 7.0, visible: bool = True):
        self.cfg = cfg
        self.scorer = scorer
        self.delta = float(delta)
        self.deadline_days = float(deadline_days)
        self.visible = bool(visible)
        self.max_lag = None      # optional staleness cutoff, in days
        self.lag_tau = None      # optional higher threshold for stale candidates
        self.defer_rule = "band"  # "band" (score only) or "stale"
        self.stale_min = 7.0     # days: how old the best candidate must be
        self.int_min = 0         # records: how busy the local key must be
        self.int_window = 7.0    # days: the intensity window
        self._recent = {}        # (block, service) -> list of arrival days
        self.lag_cut = None      # the lag beyond which lag_tau applies
        self.index = PrefixIndex(cfg.window_days)
        self.by_rid = {}
        self.assign = {}
        self.issue_of = {}
        self.tentative = {}            # iid -> (deadline_day, arrival_day, score)
        self.merge_log = []
        self.decisions = []
        self._counter = 0
        self._cur_day = -1
        self._tok = {}
        self._c3 = {}
        self.n_deferred = 0
        self.latencies = []

    # -- caches -------------------------------------------------------------
    def _tokens(self, rid, rec):
        t = self._tok.get(rid)
        if t is None:
            t = tokens_of(rec.text)
            self._tok[rid] = t
        return t

    def _char3(self, rid, rec):
        t = self._c3.get(rid)
        if t is None:
            t = _char3(rec.description)
            self._c3[rid] = t
        return t

    def _new_issue(self, rec, tokens, tag="I", visible=True):
        """Create an issue; `visible=False` keeps it out of the retrieval index.

        The invisible variant exists for the delay-versus-visibility ablation: a
        deferred record can still be re-scored at its deadline, but later arrivals
        cannot find it, so nothing can attach to it in the meantime.
        """
        self._counter += 1
        iv = Issue(iid=f"{tag}{self._counter:07d}", created=rec.day, last=rec.day)
        iv.absorb(rec)
        if visible:
            self.index.add(iv, rec, tokens)
        self.issue_of[iv.iid] = iv
        self.assign[rec.rid] = iv.iid
        return iv

    # -- scoring ------------------------------------------------------------
    def _intensity(self, rec):
        """How many records with the same address block and service arrived in
        the last `int_window` days. Causal: only past arrivals are counted."""
        key = (rec.addr.block, rec.service)
        q = self._recent.get(key)
        if not q:
            return 0
        cutoff = rec.day - self.int_window
        while q and q[0] < cutoff:
            q.pop(0)
        return len(q)

    def _remember(self, rec):
        key = (rec.addr.block, rec.service)
        self._recent.setdefault(key, []).append(rec.day)

    def _best(self, rec, exclude=None):
        """Best open issue for `rec`, optionally excluding one issue id."""
        tokens = self._tokens(rec.rid, rec)
        char3 = self._char3(rec.rid, rec)
        cand = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                     use_text=self.cfg.use_text)
        usable = []
        for iid in cand:
            if iid == exclude:
                continue
            issue = self.index.issues.get(iid)
            if issue is None or issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid.get(issue.reps[0])
            if rep is not None:
                if self.max_lag is not None and (rec.day - rep.day) > self.max_lag:
                    continue          # refuse a merge into an issue this stale
                usable.append((issue, rep))
        if not usable:
            return None, -1.0
        feats = np.stack([pair_features(rec, tokens, char3, iss, rep,
                                        self._tokens(rep.rid, rep),
                                        self._char3(rep.rid, rep))
                          for iss, rep in usable])
        scores = self.scorer(feats)
        k = int(np.argmax(scores))
        return usable[k][0], float(scores[k])

    def _merge_into(self, src, dst):
        """Move every member of `src` into `dst` and log one decision per member."""
        rep = dst.reps[0]
        for rid in list(src.members):
            rec = self.by_rid[rid]
            dst.absorb(rec)
            self.assign[rid] = dst.iid
            self.merge_log.append((rid, rep))
        self.index.issues.pop(src.iid, None)
        self.issue_of.pop(src.iid, None)
        self.tentative.pop(src.iid, None)

    # -- deadline handling --------------------------------------------------
    def _resolve(self, day):
        due = [(dl, iid) for iid, (dl, _a, _s) in self.tentative.items() if dl <= day]
        due.sort()
        for dl, iid in due:
            # provisional issues may be absent from the retrieval index when the
            # visibility ablation is off, but they always exist in issue_of
            issue = self.issue_of.get(iid)
            if issue is None or not issue.members:
                self.tentative.pop(iid, None)
                continue
            rep = self.by_rid[issue.reps[0]]
            target, score = self._best(rep, exclude=iid)
            arrival = self.tentative[iid][1]
            self.tentative.pop(iid, None)
            if target is not None and score >= self.cfg.t_merge:
                self._merge_into(issue, target)
                self.decisions.append((rep.rid, MERGE, score))
            else:
                self.decisions.append((rep.rid, NEW, max(0.0, score)))
            self.latencies.append(day - arrival)

    def _advance(self, day):
        if day != self._cur_day:
            if self.tentative:
                self._resolve(day)
            self._cur_day = day
            self.index.prune(day)

    # -- main entry ---------------------------------------------------------
    def on_record(self, rec):
        day = int(rec.day)
        self._advance(day)
        tokens = self._tokens(rec.rid, rec)
        self.by_rid[rec.rid] = rec
        self._remember(rec)
        cfg = self.cfg
        issue, score = self._best(rec)

        # Optional lag-conditional threshold: a merge into a candidate older than
        # `lag_cut` days must clear `lag_tau` instead of `t_merge`. This is the
        # strongest one-parameter-per-tier lag-aware baseline we can build, and it is
        # what the mechanism has to beat if the mechanism is not itself a lag rule.
        thr = cfg.t_merge + self.delta
        if (issue is not None and self.lag_tau is not None
                and self.lag_cut is not None):
            _r0 = self.by_rid.get(issue.reps[0])
            if _r0 is not None and (rec.day - _r0.day) > self.lag_cut:
                thr = self.lag_tau + self.delta

        if issue is not None and score >= thr:
            rep = issue.reps[0]
            issue.absorb(rec)
            self.assign[rec.rid] = issue.iid
            self.merge_log.append((rec.rid, rep))
            self.decisions.append((rec.rid, MERGE, score))
            # If the target was itself waiting for evidence, it just got some: a
            # record scored against it and chose it. Resolve it now instead of at
            # its deadline, so waiting ends when the evidence arrives rather than
            # at a fixed clock. Leaving it open would also risk dragging its
            # members into a worse issue at the deadline.
            if issue.iid in self.tentative:
                _dl, arrival, _s = self.tentative.pop(issue.iid)
                self.latencies.append(day - arrival)
            return MERGE
        if issue is not None and score >= cfg.t_merge:
            # Which marginal records are held is a decision. The band holds every
            # record whose score is in [tau, tau+delta). The alternative tested here
            # holds a record only when its best candidate is stale AND the local key
            # is busy, i.e. when a fresh claim is both plausible and worth waiting
            # for. That policy can use the arrival process; the band cannot.
            hold = True
            if self.defer_rule == "stale":
                _rep = self.by_rid.get(issue.reps[0])
                _stale = (rec.day - _rep.day) if _rep is not None else 0
                hold = (_stale >= self.stale_min
                        and self._intensity(rec) >= self.int_min)
            if not hold:
                rep = issue.reps[0]
                issue.absorb(rec)
                self.assign[rec.rid] = issue.iid
                self.merge_log.append((rec.rid, rep))
                self.decisions.append((rec.rid, MERGE, score))
                return MERGE
            iv = self._new_issue(rec, tokens, tag="D", visible=self.visible)
            self.tentative[iv.iid] = (day + self.deadline_days, day, score)
            self.n_deferred += 1
            self.decisions.append((rec.rid, PENDING, score))
            return PENDING
        iv = self._new_issue(rec, tokens)
        self.decisions.append((rec.rid, NEW, max(0.0, score)))
        return NEW

    def finish(self, last_day):
        self._resolve(last_day + self.deadline_days + 1)

    def partition(self):
        return dict(self.assign)


def run_policy(model, records, gold, tau, delta, deadline, visible=True,
               max_lag=None, lag_tau=None, lag_cut=None, defer_rule="band",
               stale_min=7.0, int_min=0, int_window=7.0):
    cfg = StreamConfig(t_merge=float(tau), t_pending=0.0, window_days=60.0,
                       k_candidates=60, max_cluster=20)
    cl = DeferralClusterer(cfg, model, delta=delta, deadline_days=deadline,
                           visible=visible)   # noqa: E501
    cl.max_lag = max_lag
    cl.lag_tau = lag_tau
    cl.lag_cut = lag_cut
    cl.defer_rule = defer_rule
    cl.stale_min = stale_min
    cl.int_min = int_min
    cl.int_window = int_window
    t0 = time.perf_counter()
    for r in records:
        cl.on_record(r)
    cl.finish(int(records[-1].day))
    elapsed = time.perf_counter() - t0
    ids = {r.rid for r in records}
    ev = evaluate_partition(cl.partition(), gold, ids)
    d = decision_fmp(cl.merge_log, gold)
    _last_state.clear()
    _last_state.update({"assign": cl.partition(),
                        "merge_log": [list(p) for p in cl.merge_log]})
    return {
        "tau": round(float(tau), 4), "delta": round(float(delta), 4),
        "deadline_days": float(deadline), "visible": bool(visible),
        "pair_precision": ev["precision"], "pair_recall": ev["recall"],
        "pair_f1": ev["f1"], "n_pos": ev["n_pos"],
        "max_pred_cluster": ev["max_pred_cluster"],
        "dec_fmp": d["dec_fmp"], "decisions": d["decisions"],
        "dec_precision": d["dec_precision"], "dec_coverage": d["dec_coverage"],
        "n_deferred": cl.n_deferred,
        "deferred_share": round(cl.n_deferred / max(1, len(records)), 4),
        "latency_mean": round(float(np.mean(cl.latencies)), 2) if cl.latencies else 0.0,
        "latency_max": int(max(cl.latencies)) if cl.latencies else 0,
        "ms_per_record": round(1000.0 * elapsed / max(1, len(records)), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=TAU)
    ap.add_argument("--deltas", default="0,0.05,0.2")
    ap.add_argument("--deadlines", default="7")
    ap.add_argument("--out", default=str(OUT / "e26_decision_timing.json"))
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--seed", type=int, default=13,
                    help="seed of the scorer fit; cross-process tie-order determinism is not established")
    ap.add_argument("--window", default="may",
                    choices=("may", "april", "march", "june", "dev", "test"),
                    help="evaluation window; historical selector checks include exploratory "
                         "May inspection (see docs/REPRODUCE.md). dev/test are the "
                         "Chicago split (1-7 vs 8-14 March 2026).")
    ap.add_argument("--city", default="sandiego", choices=("sandiego", "chicago"),
                    help="Chicago's links have a median lag of 0.42 days, so its "
                         "deferral horizon is days-to-weeks shorter than San Diego's")
    ap.add_argument("--visibility", default="on", choices=("on", "off"),
                    help="off = delay without provisional visibility (the record is "
                         "still re-scored at its deadline but later arrivals cannot "
                         "retrieve it). This is the delay-versus-visibility ablation.")
    ap.add_argument("--defer-rule", default="band", choices=("band", "stale"),
                    help="band = hold any marginal score; stale = hold only when the best candidate is old and the local key is busy")
    ap.add_argument("--stale-min", type=float, default=7.0)
    ap.add_argument("--int-min", type=int, default=0)
    ap.add_argument("--int-window", type=float, default=7.0)
    ap.add_argument("--lag-tau", type=float, default=None,
                    help="threshold for merging into a candidate older than --lag-cut days")
    ap.add_argument("--lag-cut", type=float, default=None,
                    help="the lag in days beyond which --lag-tau applies")
    ap.add_argument("--scorer", default="gbt", choices=("gbt", "logit"),
                    help="model family; gbt is the default and logit is the replication arm")
    ap.add_argument("--max-lag", type=float, default=None,
                    help="refuse merges whose issue representative is older than "
                         "this many days; this is the staleness control")
    ap.add_argument("--dump", default=None,
                    help="write {assign, merge_log} for the last policy to this path, "
                         "so the effect can be stratified after the fact")
    args = ap.parse_args()

    CITY = {
        "sandiego": dict(loader=load_san_diego, train=("2024-01-01", "2024-02-29"),
                         windows={"may": ("2024-05-01", "2024-05-31"),
                                  "april": ("2024-04-01", "2024-04-30"),
                                  "march": ("2024-03-01", "2024-03-31"),
                                  "june": ("2024-06-01", "2024-06-30")}),
        "chicago": dict(loader=load_chicago, train=("2026-01-01", "2026-02-28"),
                        windows={"dev": ("2026-03-01", "2026-03-07"),
                                 "test": ("2026-03-08", "2026-03-14")}),
    }[args.city]
    rec, links = CITY["loader"]()
    gold = build_gold(args.city if args.city != "sandiego" else "san_diego",
                      rec, links)
    tr = subset_records(rec, *CITY["train"])
    w0, w1 = CITY["windows"][args.window]
    cf = subset_records(rec, w0, w1)
    log(f"{args.city}/{args.window} window {len(cf):,} records ({w0}..{w1})")

    t0 = time.time()
    X, y = build_candidate_pairs(tr, gold, max_records=22000)
    model = (Logit(X, y, seed=args.seed) if args.scorer == "logit"
             else GBT(X, y, seed=args.seed))
    log(f"scorer fitted on {X.shape} pos={int(y.sum())} ({time.time()-t0:.0f}s)")

    deltas = [float(d) for d in args.deltas.split(",")]
    deadlines = [float(d) for d in args.deadlines.split(",")]
    grid = []
    for delta in deltas:
        ds = [0.0] if delta == 0.0 else deadlines
        for dl in ds:
            grid.append((delta, dl))

    out_path = Path(args.out)
    rows = []
    if out_path.exists() and not args.fresh:
        try:
            rows = json.loads(out_path.read_text(encoding="utf-8")).get("rows", [])
        except (ValueError, KeyError, OSError):
            rows = []
    done = {(r["delta"], r["deadline_days"], r.get("visible", True)) for r in rows}

    def checkpoint():
        payload = {"city": args.city, "window": args.window,
                   "test": [w0, w1], "seed": args.seed,
                   "tau": args.tau, "rows": rows}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    vis = args.visibility == "on"
    for delta, dl in grid:
        if (delta, dl, vis) in done:
            log(f"delta={delta} D={dl} visible={vis}: already on disk, skipping")
            continue
        r = run_policy(model, cf, gold, args.tau, delta, dl, visible=vis,
                   max_lag=args.max_lag, lag_tau=args.lag_tau,
                   lag_cut=args.lag_cut, defer_rule=args.defer_rule,
                   stale_min=args.stale_min, int_min=args.int_min,
                   int_window=args.int_window)
        if args.dump:
            tag = "base" if delta == 0.0 else f"d{str(delta).replace('.', 'p')}"
            Path(args.dump).with_name(
                Path(args.dump).stem + f"_{tag}.json").write_text(
                json.dumps(_last_state), encoding="utf-8")
        rows.append(r)
        checkpoint()
        log(f"delta={delta:<5} D={dl:>4} | F1={r['pair_f1']:.4f} "
            f"R={r['pair_recall']:.4f} P={r['pair_precision']:.4f} "
            f"decFMP={r['dec_fmp']:.3f} | deferred={r['deferred_share']:.3f} "
            f"lat={r['latency_mean']:.1f}/{r['latency_max']}d "
            f"{r['ms_per_record']:.2f}ms")

    checkpoint()
    log(f"wrote {out_path.name}")


if __name__ == "__main__":
    main()
