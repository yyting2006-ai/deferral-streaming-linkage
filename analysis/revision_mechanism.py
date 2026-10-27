"""Set-based and denominator analyses for complete causal replay traces.

No label or text is transmitted. Import ``analyze_bundle(records, gold, arms)``
or run ``python analysis/revision_mechanism.py --bundle local.json --out out``.

The input bundle has records (rid, when, service_key), gold (rid -> component),
and arms (name -> {events, partition, candidate_snapshots}). Events follow
causal_engine_v2's final-event schema. ``gold`` may also be a Gold instance.
``partition`` must describe the final assignment of all finalized records.

Pair recovery is a final-partition metric. Its trigger is the later of the two
records' finalization events; this descriptive attribution does NOT assert that
the pair itself was retrieved, scored or causally responsible for a merge.
Wrong merge sets are directed (source record, scoring representative) operations.
Raw triggers are retained. A separate operation class identifies an arriving
record's MERGE that immediately follows its pending target's claim NEW event;
it distinguishes claim_on_arrival from claim_at_deadline and ordinary immediate
merges. The double-object claim therefore is not hidden in the raw arrival count.
Common support and standardized rates are sensitivity summaries on selected,
nonrandom support, not policy causal effects or operational error estimates.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping


def _field(x, *keys, default=None):
    for k in keys:
        value = x.get(k) if isinstance(x, Mapping) else getattr(x, k, None)
        if value is not None:
            return value
    return default


def _time(x):
    if isinstance(x, datetime):
        value = x
    elif isinstance(x, date):
        value = datetime.combine(x, datetime.min.time())
    elif isinstance(x, (float, int)):
        return float(x)
    else:
        value = datetime.fromisoformat(str(x).replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp() / 86400.0


def _ratio(a, b):
    return a / b if b else None


def _bucket(days):
    if days is None or not math.isfinite(days):
        return 'unknown'
    if days <= 1:
        return '0-1d'
    if days <= 7:
        return '1-7d'
    if days <= 30:
        return '7-30d'
    return '>30d'


def _records(records):
    items = records.values() if isinstance(records, Mapping) else records
    out = {}
    for r in items:
        rid = str(_field(r, 'rid', 'id', 'record_id'))
        when = _field(r, 'when', 'arrival_at', 'arrival_time', 'arrival_day')
        if rid in out:
            raise ValueError(f'duplicate input record {rid}')
        out[rid] = {'time': _time(when), 'service': str(_field(r, 'service_key', 'service', default='UNKNOWN')) or 'UNKNOWN'}
    return out


def _component(gold):
    if hasattr(gold, 'component'):
        gold = gold.component
    elif 'component' in gold and isinstance(gold['component'], Mapping):
        gold = gold['component']
    return {str(k): str(v) for k, v in gold.items() if v not in (None, '', 'None')}


def _normalize_arm(name, arm, rec, comp):
    events = arm.get('events', arm.get('final_events', []))
    partition = {str(k): str(v) for k, v in arm['partition'].items()}
    by_source = {}
    duplicates = []
    final_events = []
    for i, event in enumerate(events):
        e = dict(event)
        src = str(_field(e, 'object_id', 'source_id', 'rid'))
        if src not in rec:
            raise ValueError(f'{name}: final event for out-of-scope record {src}')
        if src in by_source:
            duplicates.append(src)
        payload = e.get('payload', {})
        action = str(e['action']).upper()
        if action not in ('NEW', 'MERGE'):
            raise ValueError(f'{name}: unexpected final action {action}')
        rep = _field(payload, 'representative_id', default=_field(e, 'representative_id', 'target_record_id'))
        rep = str(rep) if rep is not None else None
        time = _time(_field(e, 'emitted_at', 'commit_time', 'commit_at'))
        truth = None
        if action == 'MERGE' and src in comp and rep in comp:
            truth = comp[src] == comp[rep]
        age = _field(payload, 'target_age_days', default=_field(e, 'target_age_days'))
        if age is None and rep in rec:
            age = time - rec[rep]['time']
        e.update(source=src, representative=rep, time=time,
                 order=(time, e.get('sequence', i)), truth=truth,
                 trigger=str(e.get('trigger', 'unknown')), action=action,
                 age=float(age) if age is not None else None,
                 latency=time - rec[src]['time'], service=rec[src]['service'])
        by_source[src] = e
        final_events.append(e)
    if duplicates:
        raise ValueError(f'{name}: {len(duplicates)} duplicate final events; counts would not represent unique decisions')
    if set(partition) != set(by_source):
        raise ValueError(f'{name}: partition/final-event source sets disagree')
    merges = [e for e in final_events if e['action'] == 'MERGE']
    if any(e['representative'] is None for e in merges):
        raise ValueError(f'{name}: MERGE missing scoring representative_id')
    ordered=sorted(final_events,key=lambda e:e['order'])
    for i,e in enumerate(ordered):
        e['operation_trigger']='immediate' if e['trigger']=='arrival' else e['trigger']
        previous=ordered[i-1] if i else None
        if (e['action']=='MERGE' and previous is not None and previous['action']=='NEW'
                and previous['trigger']=='claim' and previous['source']==e['representative']
                and previous['time']==e['time']
                and previous.get('payload',{}).get('issue_id')==e.get('target_id')
                and e.get('target_id') is not None):
            e['operation_trigger']='claim_on_arrival' if e['trigger']=='arrival' else 'claim_at_deadline' if e['trigger']=='deadline' else 'claim_other'
    return {'events': final_events, 'by_source': by_source, 'partition': partition,
            'merges': merges, 'snapshots': arm.get('candidate_snapshots', [])}


def _quantile(xs, p):
    if not xs:
        return None
    x = sorted(xs)
    k = (len(x) - 1) * p
    i, j = int(math.floor(k)), int(math.ceil(k))
    return x[i] + (x[j] - x[i]) * (k - i)


def _denominators(name, arm, rec):
    events, merges = arm['events'], arm['merges']
    correct = sum(e['truth'] is True for e in merges)
    wrong = sum(e['truth'] is False for e in merges)
    known = correct + wrong
    latency = [e['latency'] for e in events]
    held = [e for e in events if e.get('payload', {}).get('held', False)]
    late = []
    for e in held:
        due = e.get('payload', {}).get('due_at')
        if due is not None:
            late.append(e['time'] > _time(due) + 1e-9)
    row = dict(arm=name, records=len(rec), final_events=len(events),
               unique_final_records=len(arm['by_source']), new=len(events)-len(merges),
               merge=len(merges), known_merge=known, correct_merge=correct,
               wrong_merge=wrong, unknown_merge=len(merges)-known,
               pending=len(rec)-len(events), coverage=_ratio(known, len(merges)),
               fmp=_ratio(wrong, known), finalized_fraction=_ratio(len(events), len(rec)),
               held_records=len(held), held_fraction=_ratio(len(held), len(rec)),
               late_held_records=sum(late), due_logged_held_records=len(late),
               late_held_fraction=_ratio(sum(late), len(late)),
               latency_mean=_ratio(sum(latency), len(latency)),
               latency_p50=_quantile(latency,.5), latency_p90=_quantile(latency,.9),
               latency_p95=_quantile(latency,.95), latency_p99=_quantile(latency,.99),
               latency_max=max(latency,default=None))
    area=sum(e['latency'] for e in held)
    horizon=max((e['time'] for e in events),default=0)-min((r['time'] for r in rec.values()),default=0)
    row.update(pending_area_record_days=area,replay_horizon_days=horizon,
               mean_pending_over_replay_horizon=_ratio(area,horizon))
    for t in ('arrival','claim','deadline'):
        row[f'{t}_final_events'] = sum(e['trigger'] == t for e in events)
        row[f'{t}_held_final_events'] = sum(e['trigger'] == t for e in held)
    return row


def _trigger_for_pair(pair, arm):
    events = [arm['by_source'].get(x) for x in pair]
    if any(x is None for x in events):
        return 'unfinalized'
    return max(events, key=lambda x: x['order'])['trigger']


def _operation_for_pair(pair,arm):
    events=[arm['by_source'].get(x) for x in pair]
    if any(x is None for x in events):return 'unfinalized'
    return max(events,key=lambda x:x['order'])['operation_trigger']


def _rate_row(label, events, **kwargs):
    known = [e for e in events if e['truth'] is not None]
    wrong = sum(e['truth'] is False for e in known)
    return dict(arm=label, total_merge=len(events), known_merge=len(known),
                wrong_merge=wrong, correct_merge=len(known)-wrong,
                unknown_merge=len(events)-len(known), fmp=_ratio(wrong, len(known)), **kwargs)


def _stratified_rates(basename, name, base, other, common):
    rows = []
    standards = []
    for variable in ('service', 'target_age', 'service_x_target_age'):
        def stratum(e):
            if variable == 'service':
                return e['service']
            if variable == 'target_age':
                return _bucket(e['age'])
            return e['service'] + '|' + _bucket(e['age'])
        grouped = {}
        for label, arm in ((basename,base), (name,other)):
            cells = defaultdict(list)
            for e in arm['merges']:
                if e['truth'] is not None:
                    cells[stratum(e)].append(e)
            grouped[label] = cells
            for cell, es in sorted(cells.items()):
                rows.append(_rate_row(label, es, comparison=f'{name}_vs_{basename}',
                                      scope='all_known_merges', variable=variable, stratum=cell))
        shared = set(grouped[basename]) & set(grouped[name])
        weights = {s:len(grouped[basename][s])+len(grouped[name][s]) for s in shared}
        weight_sum = sum(weights.values())
        std = {}
        for label in (basename,name):
            num = sum(weights[s] * sum(e['truth'] is False for e in grouped[label][s]) / len(grouped[label][s]) for s in shared)
            std[label] = _ratio(num, weight_sum)
        standards.append(dict(comparison=f'{name}_vs_{basename}', variable=variable,
                              weighting='pooled_known_merge_distribution_on_shared_strata',
                              shared_strata=len(shared), pooled_support=weight_sum,
                              baseline_retained=sum(len(grouped[basename][s]) for s in shared),
                              other_retained=sum(len(grouped[name][s]) for s in shared),
                              baseline_total=sum(map(len,grouped[basename].values())),
                              other_total=sum(map(len,grouped[name].values())),
                              baseline_fmp_standardized=std[basename], other_fmp_standardized=std[name],
                              difference=(std[name]-std[basename]) if weight_sum else None))
    # Common-source support gives exactly the same source records to both arms.
    # Age is treatment-dependent; joint age strata expose rather than hide shifts.
    for variable in ('source_service','joint_target_age'):
        grouped = defaultdict(list)
        for src in common:
            b, o = base['by_source'][src], other['by_source'][src]
            cell = b['service'] if variable == 'source_service' else _bucket(b['age'])+' -> '+_bucket(o['age'])
            grouped[cell].append(src)
        for cell, srcs in sorted(grouped.items()):
            for label, arm in ((basename,base),(name,other)):
                rows.append(_rate_row(label,[arm['by_source'][s] for s in srcs],
                                      comparison=f'{name}_vs_{basename}',scope='same_source_known_merge',
                                      variable=variable,stratum=cell))
    return rows, standards


def analyze_bundle(records, gold, arms, baseline='arrival'):
    """Return aggregate-only analyses; raw records and IDs are never returned."""
    rec, comp = _records(records), _component(gold)
    normalized = {name:_normalize_arm(name, arm, rec, comp) for name,arm in arms.items()}
    if baseline not in normalized:
        raise ValueError(f'baseline {baseline!r} missing')
    by_comp = defaultdict(list)
    for rid in rec:
        if rid in comp:
            by_comp[comp[rid]].append(rid)
    positives = {tuple(sorted(p)) for ids in by_comp.values() for p in combinations(ids,2)}
    recovered = {name:{p for p in positives if p[0] in arm['partition'] and p[1] in arm['partition'] and arm['partition'][p[0]] == arm['partition'][p[1]]}
                 for name,arm in normalized.items()}
    base = normalized[baseline]
    pos_rows, wrong_rows, common_rows, strata_rows, standardized_rows = [],[],[],[],[]
    for name, arm in normalized.items():
        if name == baseline:
            continue
        count = Counter()
        for p in positives:
            in_base, in_other = p in recovered[baseline], p in recovered[name]
            category = 'common' if in_base and in_other else 'new' if in_other else 'lost' if in_base else 'neither'
            lag = _bucket(abs(rec[p[0]]['time']-rec[p[1]]['time']))
            bt = _trigger_for_pair(p, base) if in_base else 'not_recovered'
            ot = _trigger_for_pair(p, arm) if in_other else 'not_recovered'
            bo = _operation_for_pair(p,base) if in_base else 'not_recovered'
            oo = _operation_for_pair(p,arm) if in_other else 'not_recovered'
            count[lag,category,bt,ot,bo,oo] += 1
        for (lag,category,bt,ot,bo,oo), n in sorted(count.items()):
            pos_rows.append(dict(comparison=f'{name}_vs_{baseline}',lag_bucket=lag,category=category,
                                 baseline_later_final_trigger=bt,other_later_final_trigger=ot,
                                 baseline_later_operation=bo,other_later_operation=oo,count=n))
        wrong_base = {(e['source'],e['representative']):e for e in base['merges'] if e['truth'] is False}
        wrong_other = {(e['source'],e['representative']):e for e in arm['merges'] if e['truth'] is False}
        count = Counter()
        for p in set(wrong_base) | set(wrong_other):
            b, o = wrong_base.get(p), wrong_other.get(p)
            category = 'common' if b and o else 'new' if o else 'avoided'
            count[category,_bucket(b['age']) if b else 'not_wrong',_bucket(o['age']) if o else 'not_wrong',
                  b['trigger'] if b else 'not_wrong',o['trigger'] if o else 'not_wrong',
                  b['operation_trigger'] if b else 'not_wrong',o['operation_trigger'] if o else 'not_wrong'] += 1
        for (category,ba,oa,bt,ot,bo,oo), n in sorted(count.items()):
            wrong_rows.append(dict(comparison=f'{name}_vs_{baseline}',category=category,
                                   baseline_target_age=ba,other_target_age=oa,
                                   baseline_trigger=bt,other_trigger=ot,
                                   baseline_operation=bo,other_operation=oo,count=n))
        common = {e['source'] for e in base['merges'] if e['truth'] is not None} & {e['source'] for e in arm['merges'] if e['truth'] is not None}
        for label, aa in ((baseline,base),(name,arm)):
            known_total = sum(e['truth'] is not None for e in aa['merges'])
            common_rows.append(_rate_row(label,[aa['by_source'][s] for s in common],
                                         comparison=f'{name}_vs_{baseline}',support='same_source_known_merge',
                                         arm_known_merge_total=known_total,retained_fraction=_ratio(len(common),known_total)))
        sr, st = _stratified_rates(baseline,name,base,arm,common)
        strata_rows.extend(sr)
        standardized_rows.extend(st)
    trigger_rows = [];operation_rows=[]
    for name,arm in normalized.items():
        for trigger in sorted({e['trigger'] for e in arm['events']}):
            events = [e for e in arm['events'] if e['trigger']==trigger]
            merges = [e for e in events if e['action']=='MERGE']
            row = _rate_row(name,merges,trigger=trigger,final_events=len(events),new=len(events)-len(merges))
            row.update(latency_mean=_ratio(sum(e['latency'] for e in events),len(events)),latency_max=max(e['latency'] for e in events))
            trigger_rows.append(row)
        for operation in sorted({e['operation_trigger'] for e in arm['merges']}):
            es=[e for e in arm['merges'] if e['operation_trigger']==operation]
            operation_rows.append(_rate_row(name,es,operation=operation,
                                           raw_trigger=es[0]['trigger'],
                                           latency_mean=_ratio(sum(e['latency'] for e in es),len(es))))
    return dict(arm_denominators=[_denominators(name,arm,rec) for name,arm in normalized.items()],
                positive_pair_changes=pos_rows,wrong_merge_changes=wrong_rows,
                common_support=common_rows,stratified_fmp=strata_rows,
                standardized_fmp=standardized_rows,trigger_breakdown=trigger_rows,
                merge_operation_breakdown=operation_rows,
                metadata={'evaluation_records':len(rec),'workflow_known_records':sum(r in comp for r in rec),
                          'workflow_positive_pairs':len(positives),'baseline':baseline,
                          'positive_pair_trigger':'trigger of later record finalization; not direct retrieval or causal attribution',
                          'operation_trigger':'claim_on_arrival/claim_at_deadline requires immediately preceding same-time claim NEW for the merge representative and target issue; raw triggers are also retained',
                          'wrong_operation':'directed source record and scoring representative',
                          'gold_assumption':'cross-component negatives use the frozen workflow closed-world proxy',
                          'support_limitation':'same-source and shared-stratum supports are policy-dependent, nonrandom, descriptive sensitivity analyses',
                          'uncertainty':'one-stream analyses are descriptive; no independent-pair tests or p-values'})


def write_analysis(result, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out/'mechanism_analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,rows in result.items():
        if not isinstance(rows,list):
            continue
        with (out/(name+'.csv')).open('w',newline='',encoding='utf-8') as f:
            fields = list(dict.fromkeys(k for row in rows for k in row))
            writer = csv.DictWriter(f,fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--baseline',default='arrival')
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding='utf-8'))
    result = analyze_bundle(bundle['records'],bundle['gold'],bundle['arms'],args.baseline)
    write_analysis(result,args.out)
    print(json.dumps(result['arm_denominators'],indent=2))


if __name__ == '__main__':
    main()
