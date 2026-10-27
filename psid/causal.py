"""Corrected event-time, irrevocable streaming linkage protocol.

This is a new implementation, not a relabelling of historical E26 results.
Inputs must carry timezone-aware timestamps and arrive in (UTC time, rid)
order. Timers at a timestamp fire before arrivals at that timestamp. Every
observed record produces exactly one final NEW or MERGE event; a provisional
identity always contains exactly one record. A claim finalizes its target
before the claimant, and never moves previously committed members.

Retrieval retains the historical PrefixIndex channels and first representative
scoring, with deterministic lexical ties and corrected Issue.n initialization.
Issue postings are inserted when the identity first becomes visible, not on
every member attachment. Training pairs and scorers must be rebuilt after the
Issue.n correction; historical fitted models are not interchangeable.
"""
from __future__ import annotations

import copy
import heapq
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Sequence

import numpy as np

from .methods import Issue, PrefixIndex, StreamConfig, _char3, pair_features, tokens_of
from .schema import Record

SECONDS_PER_DAY = 86400.0


def utc_time(value: datetime) -> datetime:
    """Normalize an explicitly located timestamp; never infer its timezone."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CausalClusterer requires timezone-aware record/clock timestamps")
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


class CausalClusterer:
    """Event-time linker with a bounded finalization delay.

    ``defer_rule`` is ``band``, ``gate`` (alias ``stale``), or ``arrival``.
    Band holds tau <= score < tau + delta. Gate additionally requires the
    best candidate representative to be at least ``stale_min`` days old.
    ``max_lag`` excludes representatives older than that many elapsed days at
    the scoring event; setting delta=0 gives the arrival/cutoff comparison.
    The visibility-off arm hides pending identities until they finalize NEW.

    ``on_record`` returns NEW, MERGE, or PENDING (uppercase); PENDING is an
    internal state and is never appended to the final output event stream.
    ``finish`` drains exact deadlines using only records already supplied.
    ``advance(T)`` exposes the event prefix through T without reading new data.
    """

    def __init__(self, cfg: StreamConfig, scorer, delta: float = 0.0,
                 D: float = 7.0, visible: bool = True, defer_rule: str = "band",
                 stale_min: float = 5.0, max_lag: Optional[float] = None,
                 trace_candidates: bool = False):
        if not math.isfinite(delta) or delta < 0:
            raise ValueError("delta must be finite and nonnegative")
        if not math.isfinite(D) or D < 0:
            raise ValueError("D must be finite and nonnegative")
        if cfg.max_cluster < 1:
            raise ValueError("max_cluster must be positive")
        if defer_rule not in ("band", "gate", "stale", "arrival"):
            raise ValueError("defer_rule must be band, gate, stale, or arrival")
        if max_lag is not None and (not math.isfinite(max_lag) or max_lag < 0):
            raise ValueError("max_lag must be finite and nonnegative")
        self.cfg = cfg
        self.scorer = scorer
        self.delta = float(delta)
        self.D = float(D)
        self.deadline_days = self.D
        self.visible = bool(visible)
        self.defer_rule = "gate" if defer_rule == "stale" else defer_rule
        self.stale_min = float(stale_min)
        self.max_lag = max_lag
        self.trace_candidates = bool(trace_candidates)
        self.index = PrefixIndex(cfg.window_days)
        self.by_rid: Dict[str, Record] = {}
        self.issue_of: Dict[str, Issue] = {}
        self.assign: Dict[str, str] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.events = []
        self.merge_log = []
        self.decisions = []
        self.candidate_snapshots = []
        self.top_log = []
        self.n_deferred = 0
        self.peak_pending = 0
        self.latencies = []             # All final records, in elapsed days.
        self.held_latencies = []
        self._held_ids = set()
        self._indexed_ids = set()
        self._tokens_cache = {}
        self._char3_cache = {}
        self._timers = []               # (due UTC datetime, stable iid)
        self._clock: Optional[datetime] = None
        self._last_arrival_key = None
        self._finished = False
        self._finishing = False
        self._finish_finalizations = 0
        self._deadline_resolutions = 0
        self._claim_finalizations = 0
        self._pruned_day = None

    @property
    def policy(self):
        return dict(delta=self.delta, D=self.D, visible=self.visible,
                    defer_rule=self.defer_rule, stale_min=self.stale_min,
                    max_lag=self.max_lag, tau=float(self.cfg.t_merge),
                    max_cluster=int(self.cfg.max_cluster))

    def _tokens(self, rec: Record):
        if rec.rid not in self._tokens_cache:
            self._tokens_cache[rec.rid] = tokens_of(rec.text)
        return self._tokens_cache[rec.rid]

    def _char3(self, rec: Record):
        if rec.rid not in self._char3_cache:
            self._char3_cache[rec.rid] = _char3(rec.description)
        return self._char3_cache[rec.rid]

    @staticmethod
    def _iid(rid: str) -> str:
        return "I:" + rid

    def _make_issue(self, rec: Record) -> Issue:
        iid = self._iid(rec.rid)
        if iid in self.issue_of:
            raise RuntimeError("identity already exists")
        issue = Issue(iid=iid, created=rec.day, last=rec.day)
        issue.absorb(rec)
        if issue.n != 1 or issue.members != [rec.rid]:
            raise RuntimeError("Issue initialization must count one record once")
        self.issue_of[iid] = issue
        return issue

    def _show(self, issue: Issue):
        if issue.iid not in self._indexed_ids:
            rep = self.by_rid[issue.reps[0]]
            self.index.add(issue, rep, self._tokens(rep))
            # An invisible pending identity may become visible after newer
            # arrivals. Preserve _nearest's calendar-order precondition.
            for mapping, key in ((self.index.by_addr, (rep.service_key, rep.addr.key)),
                                 (self.index.by_block, (rep.service_key, rep.addr.block))):
                entries = mapping.get(key, [])
                if len(entries) > 1 and entries[-1][1] < entries[-2][1]:
                    entries.sort(key=lambda entry: (entry[1], entry[0]))
            self._indexed_ids.add(issue.iid)
        else:
            self.index.issues[issue.iid] = issue

    def _prune(self, now: datetime):
        # PrefixIndex retains its historical daily expiration; exact elapsed
        # eligibility is also checked below for every scored candidate.
        day = now.toordinal()
        if day != self._pruned_day:
            self.index.prune(day)
            self._pruned_day = day

    def _best(self, rec: Record, now: datetime, phase: str, exclude=None):
        tokens, chars = self._tokens(rec), self._char3(rec)
        candidates = self.index.candidates(rec, tokens, k=self.cfg.k_candidates,
                                           use_text=self.cfg.use_text)
        usable = []
        for iid in sorted(set(candidates)):
            issue = self.index.issues.get(iid)
            if issue is None or iid == exclude or rec.rid in issue.members:
                continue
            if issue.n != len(issue.members):
                raise RuntimeError("issue size invariant violated")
            if issue.n >= self.cfg.max_cluster or not issue.reps:
                continue
            rep = self.by_rid[issue.reps[0]]
            latest = max(self.by_rid[rid].when for rid in issue.members)
            if (now - latest).total_seconds() > self.cfg.window_days * SECONDS_PER_DAY:
                continue
            age = (now - rep.when).total_seconds() / SECONDS_PER_DAY
            if age < 0:
                raise RuntimeError("future representative in causal index")
            if self.max_lag is not None and age > self.max_lag:
                continue
            usable.append((issue, rep))
        scores = np.empty(0, dtype=float)
        if usable:
            feats = np.stack([pair_features(rec, tokens, chars, issue, rep,
                                           self._tokens(rep), self._char3(rep))
                              for issue, rep in usable])
            scores = np.asarray(self.scorer(feats), dtype=float).reshape(-1)
            if len(scores) != len(usable) or not np.all(np.isfinite(scores)):
                raise ValueError("scorer must return one finite score per candidate")
        if self.trace_candidates:
            self.candidate_snapshots.append({
                "query_id": rec.rid, "query_at": _iso(now),
                "record_arrival_at": _iso(rec.when), "phase": phase,
                "policy": self.policy,
                "candidate_scope": "eligible_scored_after_self_size_window_lag_filters",
                "candidates": [{
                    "record_id": rep.rid, "issue_id": issue.iid,
                    "member_ids": list(issue.members), "score": float(score),
                    "is_pending": issue.iid in self.pending,
                    "age_days": (now - rep.when).total_seconds() / SECONDS_PER_DAY,
                    "pair_age_days": (rec.when - rep.when).total_seconds() / SECONDS_PER_DAY,
                } for (issue, rep), score in zip(usable, scores)]})
        if not usable:
            return None, -1.0
        # usable is sorted by iid; np.argmax therefore has a stable tie rule.
        best = int(np.argmax(scores))
        return usable[best][0], float(scores[best])

    def _emit(self, rec: Record, issue: Issue, action: str, now: datetime,
              trigger: str, score: float, representative_id=None, due=None):
        if rec.rid in self.assign:
            raise RuntimeError("record already finalized; retraction is forbidden")
        latency = (now - rec.when).total_seconds() / SECONDS_PER_DAY
        if latency < 0 or latency > self.D + 1e-10:
            raise RuntimeError("finalization outside [arrival, arrival + D]")
        held = rec.rid in self._held_ids
        target_age = (None if representative_id is None else
                      (now - self.by_rid[representative_id].when).total_seconds() / SECONDS_PER_DAY)
        event = {
            "emitted_at": _iso(now), "object_id": rec.rid, "action": action,
            "target_id": issue.iid if action == "MERGE" else None,
            "trigger": trigger, "sequence": len(self.events),
            "payload": {"issue_id": issue.iid, "representative_id": representative_id,
                        "score": float(max(0.0, score)), "arrival_at": _iso(rec.when),
                        "due_at": _iso(due), "held": held,
                        "latency_days": latency, "target_age_days": target_age},
        }
        self.assign[rec.rid] = issue.iid
        self.events.append(event)
        self.decisions.append((rec.rid, action.lower(), float(max(0.0, score))))
        self.latencies.append(latency)
        if held:
            self.held_latencies.append(latency)
        if self._finishing:
            self._finish_finalizations += 1

    def _finalize_new(self, issue: Issue, now: datetime, trigger: str, score: float):
        if len(issue.members) != 1:
            raise RuntimeError("only a singleton uncommitted issue may finalize NEW")
        pending = self.pending.pop(issue.iid, None)
        due = pending["due"] if pending else None
        self._show(issue)
        rec = self.by_rid[issue.members[0]]
        self._emit(rec, issue, "NEW", now, trigger, score, due=due)
        if trigger == "claim":
            self._claim_finalizations += 1

    def _commit_merge(self, rec: Record, target: Issue, now: datetime,
                      trigger: str, score: float, source_issue=None):
        if rec.rid in self.assign:
            raise RuntimeError("already committed source cannot move")
        if rec.rid in target.members:
            raise RuntimeError("self merge forbidden")
        if target.n >= self.cfg.max_cluster:
            raise RuntimeError("cluster cap exceeded")
        # Both objects receive final output events. Canceling the target's
        # pending state makes its queued timer inert, including gate bypass.
        if target.iid in self.pending:
            old_score = self.pending[target.iid]["score"]
            self._finalize_new(target, now, "claim", old_score)
        elif any(rid not in self.assign for rid in target.members):
            raise RuntimeError("merge target must be committed or pending singleton")
        rep = target.reps[0]
        due = None
        if source_issue is not None:
            if source_issue.iid == target.iid or source_issue.members != [rec.rid]:
                raise RuntimeError("only the pending source singleton may move")
            pending = self.pending.pop(source_issue.iid, None)
            if pending is None:
                raise RuntimeError("deadline source is not pending")
            due = pending["due"]
            self.index.issues.pop(source_issue.iid, None)
            self.issue_of.pop(source_issue.iid, None)
        old_last = target.last
        target.absorb(rec)
        # An older held record joining a newer target cannot move last backwards.
        target.last = max(old_last, rec.day)
        self.merge_log.append((rec.rid, rep))
        self._emit(rec, target, "MERGE", now, trigger, score, rep, due)

    def _resolve(self, iid: str, due: datetime):
        pending = self.pending.get(iid)
        if pending is None or pending["due"] != due:
            return
        source = self.issue_of[iid]
        if len(source.members) != 1:
            raise RuntimeError("pending identity acquired members")
        rec = self.by_rid[source.members[0]]
        target, score = self._best(rec, due, "deadline", exclude=iid)
        self._deadline_resolutions += 1
        if target is not None and score >= self.cfg.t_merge:
            self._commit_merge(rec, target, due, "deadline", score, source)
        else:
            self._finalize_new(source, due, "deadline", score)

    def advance(self, timestamp: datetime):
        """Process every due timer through timestamp, before an equal-time arrival."""
        now = utc_time(timestamp)
        if self._clock is not None and now < self._clock:
            raise ValueError("clock cannot move backwards")
        while self._timers and self._timers[0][0] <= now:
            due, iid = heapq.heappop(self._timers)
            if iid not in self.pending:
                continue
            self._clock = due
            self._prune(due)
            self._resolve(iid, due)
        self._clock = now
        self._prune(now)

    def on_record(self, rec: Record):
        if self._finished:
            raise RuntimeError("cannot append arrivals after finish")
        now = utc_time(rec.when)
        if rec.when != now or rec.when.tzinfo != timezone.utc:
            # Copy so converting to UTC cannot alter the caller's record object.
            rec = copy.copy(rec)
            rec.when = now
        if not isinstance(rec.rid, str):
            raise TypeError("record id must be a string")
        key = (now, rec.rid)
        if self._last_arrival_key is not None and key <= self._last_arrival_key:
            raise ValueError("arrivals must have unique ids and be ordered by (UTC time, rid)")
        if rec.rid in self.by_rid:
            raise ValueError("duplicate record id")
        self.advance(now)
        self._last_arrival_key = key
        self.by_rid[rec.rid] = rec
        target, score = self._best(rec, now, "arrival")
        if target is not None:
            self.top_log.append((rec.rid, target.reps[0], score))
        if target is not None and score >= self.cfg.t_merge:
            marginal = score < self.cfg.t_merge + self.delta
            hold = marginal and self.D > 0 and self.defer_rule != "arrival"
            if hold and self.defer_rule == "gate":
                age = (now - self.by_rid[target.reps[0]].when).total_seconds() / SECONDS_PER_DAY
                hold = age >= self.stale_min
            if not hold:
                self._commit_merge(rec, target, now, "arrival", score)
                return "MERGE"
            issue = self._make_issue(rec)
            due = now + timedelta(days=self.D)
            self.pending[issue.iid] = {"due": due, "score": score, "arrival": now}
            self._held_ids.add(rec.rid)
            self.n_deferred += 1
            self.peak_pending = max(self.peak_pending, len(self.pending))
            heapq.heappush(self._timers, (due, issue.iid))
            if self.visible:
                self._show(issue)
            return "PENDING"
        issue = self._make_issue(rec)
        self._finalize_new(issue, now, "arrival", score)
        return "NEW"

    def finish(self):
        """Drain every pending deadline at its exact time; idempotent."""
        if self._finished:
            return
        self._finishing = True
        try:
            while self._timers:
                self.advance(self._timers[0][0])
            if self.pending:
                raise RuntimeError("pending identity has no timer")
            if len(self.assign) != len(self.by_rid):
                raise RuntimeError("not every observed record has one final event")
            self._finished = True
        finally:
            self._finishing = False

    def partition(self):
        return dict(self.assign)

    def envelope(self, common_randomness: str):
        return {"common_randomness": common_randomness,
                "events": copy.deepcopy(self.events)}

    def summary(self):
        def distribution(values: Sequence[float]):
            if not values:
                return dict(n=0, mean=0.0, median=0.0, p95=0.0, max=0.0)
            return dict(n=len(values), mean=float(np.mean(values)),
                        median=float(np.median(values)), p95=float(np.quantile(values, .95)),
                        max=float(max(values)))
        triggers = Counter(event["trigger"] for event in self.events)
        actions = Counter(event["action"] for event in self.events)
        return {
            "protocol": "causal-event-v2", "time_standard": "UTC; elapsed 86400-second days",
            "policy": self.policy, "n_records": len(self.by_rid), "n_events": len(self.events),
            "n_pending": len(self.pending), "n_deferred": self.n_deferred,
            "deferred_share": self.n_deferred / max(1, len(self.by_rid)),
            "peak_pending": self.peak_pending, "trigger_counts": dict(triggers),
            "action_counts": dict(actions), "deadline_resolutions": self._deadline_resolutions,
            "claim_finalizations": self._claim_finalizations,
            # This is an observation-window drain count, not an extra mechanism:
            # its events retain their actual claim/deadline triggers and times.
            "monthend_finalizations": self._finish_finalizations,
            "all_record_latency_days": distribution(self.latencies),
            "held_record_latency_days": distribution(self.held_latencies),
            "max_cluster_size": max((len(issue.members) for issue in self.issue_of.values()), default=0),
            "unique_finalizations": len({e["object_id"] for e in self.events}) == len(self.events),
            "complete": len(self.events) == len(self.by_rid),
            "finished": self._finished,
        }
