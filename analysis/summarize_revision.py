"""Summarize the corrected replay suite with explicit matched scorer seeds.

Quality metrics are recomputed from integer counts. Zero decision denominators
produce null, even when the raw legacy helper encoded zero. Core contrasts use
the same five seeds and prespecified paired 95% t intervals (df=4). These are
pointwise fit-seed uncertainty summaries on a fixed stream, not significance
claims, equivalence tests, simultaneous intervals or city-population uncertainty.

When present, this suite's trusted local private event pickle files are read to
compute time-weighted pending occupancy; --mechanism also reads local data/gold
pickles. Outputs contain aggregates only. Do not use this script on a run
directory containing pickles from an untrusted source.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy.stats import t

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))

QUALITY=('f1','precision','recall','decision_fmp','decision_coverage','cluster_fmp')
TIMING=('records','new','merges','known_merges','correct_merges','wrong_merges','unknown_merges',
        'pending','final_events','held','held_fraction','peak_pending','arrival_events','claim_events',
        'deadline_events','deadline_resolutions','claim_finalizations','monthend_finalizations',
        'all_latency_mean','all_latency_median','all_latency_p95','all_latency_max',
        'held_latency_mean','held_latency_median','held_latency_p95','held_latency_max',
        'pending_area_record_days','replay_horizon_days','mean_pending_over_replay_horizon',
        'arrival_mean_ms','arrival_p95_ms','replay_wall_seconds','process_cpu_seconds','drain_seconds','trace_io_seconds')
CONTRASTS=(('band','arrival'),('gate5','arrival'),('gate5','cutoff10'),('band','cutoff10'),
           ('invisible_band','arrival'),('invisible_gate5','arrival'),
           ('band','invisible_band'),('gate5','invisible_gate5'))


def ratio(a,b):return a/b if b else None
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_row(r,path):
    job,m,d,s=r['job'],r['metrics'],r['decision'],r['protocol_summary']
    if m.get('capped'):raise ValueError(f'{path.name}: pair enumeration capped')
    tp,fp,npos=m['tp'],m['fp'],m['n_pos'];fn=npos-tp
    if fn<0:raise ValueError(f'{path.name}: recovered positives exceed gold positives')
    known=d['dec_correct']+d['dec_wrong'];merges=d['decisions']
    if known+d['dec_unverifiable']!=merges:raise ValueError(f'{path.name}: decision counts do not reconcile')
    if s['n_records']!=m['n_records']:raise ValueError(f'{path.name}: record counts do not reconcile')
    if not s['complete'] or not s['unique_finalizations'] or s['n_pending']:
        raise ValueError(f'{path.name}: incomplete or duplicate finalization')
    if s['action_counts'].get('MERGE',0)!=merges:raise ValueError(f'{path.name}: event/decision MERGE counts disagree')
    row={k:job[k] for k in ('run_id','window','family','seed','arm','tau')}
    row.update(f1=ratio(2*tp,2*tp+fp+fn),precision=ratio(tp,tp+fp),recall=ratio(tp,npos),
               cluster_fmp=ratio(fp,tp+fp),decision_fmp=ratio(d['dec_wrong'],known),
               decision_coverage=ratio(known,merges),records=m['n_records'],known_records=m['n_known'],
               true_positive_pairs=tp,false_positive_pairs=fp,gold_positive_pairs=npos,
               new=s['action_counts'].get('NEW',0),merges=merges,known_merges=known,
               correct_merges=d['dec_correct'],wrong_merges=d['dec_wrong'],unknown_merges=d['dec_unverifiable'],
               pending=s['n_pending'],final_events=s['n_events'],held=s['n_deferred'],
               held_fraction=ratio(s['n_deferred'],s['n_records']),peak_pending=s['peak_pending'],
               arrival_events=s['trigger_counts'].get('arrival',0),claim_events=s['trigger_counts'].get('claim',0),
               deadline_events=s['trigger_counts'].get('deadline',0),
               deadline_resolutions=s['deadline_resolutions'],claim_finalizations=s['claim_finalizations'],
               monthend_finalizations=s['monthend_finalizations'],
               candidate_trace_enabled=r.get('candidate_trace',{}).get('enabled',False),
               finished_utc=r.get('finished_utc'),result_file=path.name,result_sha256=digest(path))
    for label,field in (('all','all_record_latency_days'),('held','held_record_latency_days')):
        distribution=s[field]
        for stat in ('mean','median','p95','max'):
            row[f'{label}_latency_{stat}']=distribution[stat] if distribution['n'] else None
    for k,v in r.get('runtime',{}).items():
        if isinstance(v,(float,int)) or v is None:row[k]=v
    overrides=[]
    if not known and d.get('dec_fmp') is not None:overrides.append('dec_fmp raw zero-denominator encoding replaced by null')
    if not merges and d.get('dec_coverage') is not None:overrides.append('dec_coverage raw zero-denominator encoding replaced by null')
    if not (tp+fp) and m.get('fmp') is not None:overrides.append('cluster fmp raw zero-denominator encoding replaced by null')
    if m.get('f1') is None and row['f1'] is not None:overrides.append('F1 recomputed as 2TP/(2TP+FP+FN), defined when count denominator is positive')
    row['raw_encoding_overrides']='; '.join(overrides)
    return row


def add_event_timing(row,run):
    """Integrate held intervals exactly; boundaries are first arrival/last final."""
    path=run/'private'/f"{row['run_id']}_events.pkl"
    for k in ('pending_area_record_days','replay_horizon_days','mean_pending_over_replay_horizon'):
        row[k]=None
    if not path.exists():
        row['occupancy_status']='event trace not retained; horizon-dependent occupancy unavailable'
        return
    with path.open('rb') as f:events=pickle.load(f)['events']
    def seconds(value):
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('occupancy requires timezone-aware event times')
        return parsed.timestamp()
    if not events:return
    arrivals=[seconds(e['payload']['arrival_at']) for e in events]
    commits=[seconds(e['emitted_at']) for e in events]
    area=sum(c-a for e,a,c in zip(events,arrivals,commits) if e['payload']['held'])/86400
    horizon=(max(commits)-min(arrivals))/86400
    if len(events)!=row['records'] or len({e['object_id'] for e in events})!=len(events):
        raise AssertionError('occupancy trace contains incomplete/duplicate finalization')
    expected=(row['held_latency_mean'] or 0)*row['held']
    if not math.isclose(area,expected,rel_tol=1e-10,abs_tol=1e-9):
        raise AssertionError('held latency summary disagrees with integrated event intervals')
    row.update(pending_area_record_days=area,replay_horizon_days=horizon,
               mean_pending_over_replay_horizon=ratio(area,horizon),
               occupancy_status='sum held(c_i-a_i)/(last final - first arrival); includes month-end drain',
               timing_events_sha256=digest(path))


def descriptive(values):
    good=[float(v) for v in values if v is not None and math.isfinite(v)]
    return dict(n_valid=len(good),mean=float(np.mean(good)) if good else None,
                sd=float(np.std(good,ddof=1)) if len(good)>1 else None,
                minimum=min(good,default=None),maximum=max(good,default=None))


def paired(a,b,metric,seeds):
    sa,sb=set(a),set(b)
    if sa!=sb or sa!=set(seeds):raise AssertionError(f'unmatched seed sets: {sorted(sa)} vs {sorted(sb)}; expected {seeds}')
    va=[a[s][metric] for s in seeds];vb=[b[s][metric] for s in seeds]
    if any(v is None for v in va+vb):
        return dict(n_pairs=len(seeds),n_valid_pairs=sum(x is not None and y is not None for x,y in zip(va,vb)),
                    mean_difference=None,sd_difference=None,ci95_low=None,ci95_high=None,df=None,
                    status='undefined_metric_on_at_least_one_seed; no complete-case deletion')
    diffs=np.asarray(va)-np.asarray(vb);n=len(diffs)
    mean=float(diffs.mean());sd=float(diffs.std(ddof=1)) if n>1 else None
    # Below five matched fits, retain descriptive differences only.
    margin=float(t.ppf(.975,n-1))*sd/math.sqrt(n) if n>=5 else None
    return dict(n_pairs=n,n_valid_pairs=n,a_mean=float(np.mean(va)),b_mean=float(np.mean(vb)),
                mean_difference=mean,sd_difference=sd,ci95_low=mean-margin if margin is not None else None,
                ci95_high=mean+margin if margin is not None else None,df=n-1 if n>=5 else None,
                positive_differences=int((diffs>0).sum()),negative_differences=int((diffs<0).sum()),
                minimum_difference=float(diffs.min()),maximum_difference=float(diffs.max()),
                status='paired pointwise t interval; fit-seed variability only' if n>=5 else 'descriptive; fewer than five paired fits')


def write_csv(path,rows):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        keys=list(dict.fromkeys(k for r in rows for k in r))
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)


def summarize(run_dir,out_dir=None,allow_partial=False):
    run=Path(run_dir);out=Path(out_dir) if out_dir else run/'reports'
    protocol=json.loads((run/'experiment_protocol.json').read_text(encoding='utf-8'))
    expected_seeds=sorted(protocol['core']['seeds'])
    if len(expected_seeds)!=5:raise AssertionError('This frozen core analysis requires exactly five seeds')
    rows=[];manifest=[];seen=set()
    for path in sorted((run/'results').glob('*.json')):
        r=json.loads(path.read_text(encoding='utf-8'))
        if not all(k in r for k in ('job','metrics','decision','protocol_summary')):continue
        row=normalized_row(r,path);add_event_timing(row,run)
        key=tuple(row[k] for k in ('window','family','arm','seed'))
        if key in seen:raise ValueError(f'duplicate result group/seed: {key}')
        seen.add(key);rows.append(row)
        ident=r.get('cache_identity',{})
        manifest.append({k:row[k] for k in ('run_id','window','family','arm','seed','tau','finished_utc','result_file','result_sha256')}|
                        {'policy_json':json.dumps(r['job']['policy'],sort_keys=True),
                         'protocol_sha256':ident.get('protocol_sha256'),'model_sha256':ident.get('model_sha256'),
                         'preparation_manifest_sha256':ident.get('preparation_manifest_sha256'),
                         'timing_events_sha256':row.get('timing_events_sha256'),
                         'raw_encoding_overrides':row['raw_encoding_overrides']})
    groups=defaultdict(dict)
    for row in rows:groups[row['window'],row['family'],row['arm']][row['seed']]=row
    missing=[]
    for window in protocol['core']['windows']:
        for arm in protocol['core']['arms']:
            got=set(groups.get((window,'gbt',arm),{}))
            if got!=set(expected_seeds):missing.append({'window':window,'arm':arm,'expected_seeds':expected_seeds,'found_seeds':sorted(got)})
    if missing and not allow_partial:raise ValueError('Core suite incomplete: '+json.dumps(missing))
    stats=[];timing=[];contrasts=[]
    for (window,family,arm),values in sorted(groups.items()):
        seeds=sorted(values);is_core=window in protocol['core']['windows'] and family=='gbt' and arm in protocol['core']['arms']
        for metric in QUALITY+TIMING:
            ds=descriptive([values[s].get(metric) for s in seeds])
            row=dict(window=window,family=family,arm=arm,metric=metric,seeds=','.join(map(str,seeds)),n_runs=len(seeds),core=is_core,**ds)
            stats.append(row)
            if metric in TIMING:timing.append(row)
    for window in protocol['core']['windows']:
        for a,b in CONTRASTS:
            aa,bb=groups.get((window,'gbt',a)),groups.get((window,'gbt',b))
            if allow_partial and (aa is None or bb is None or set(aa)!=set(expected_seeds) or set(bb)!=set(expected_seeds)):continue
            for metric in QUALITY:
                contrasts.append(dict(window=window,family='gbt',arm_a=a,arm_b=b,metric=metric,
                                      seeds=','.join(map(str,expected_seeds)),**paired(aa,bb,metric,expected_seeds)))
    # Logistic comparisons retain the exact, much smaller seed cohort and are descriptive.
    for window in protocol['secondary']['logit']['windows']:
        for a,b in (('band','arrival'),('gate5','arrival'),('gate5','cutoff10'),('band','cutoff10')):
            aa,bb=groups.get((window,'logit',a)),groups.get((window,'logit',b))
            if aa is None or bb is None:continue
            seeds=[protocol['secondary']['logit']['seed']]
            for metric in QUALITY:
                contrasts.append(dict(window=window,family='logit',arm_a=a,arm_b=b,metric=metric,
                                      seeds=','.join(map(str,seeds)),**paired(aa,bb,metric,seeds)))
    result={'generated_utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':digest(run/'experiment_protocol.json'),
            'complete_core':not missing,'missing_core':missing,'expected_core_seeds':expected_seeds,
            'interpretation':{'intervals':'prespecified paired 95% t, df=4 for five scorer seeds; pointwise and unadjusted',
                              'scope':'variation of fitted scorer on each fixed stream, not independent-record or city uncertainty',
                              'no_significance_test':'no p-values, multiple-comparison rejection labels or equivalence claims',
                              'undefined':'FMP and coverage recomputed from exact counts; zero denominators null; raw helper encodings retained only in original JSON',
                              'timing':'concurrent local CPU workers; traced seed13 wall time includes serialization; no isolated-throughput inference',
                              'occupancy':'sum of held intervals c_i-a_i, divided by last-final minus first-arrival horizon; includes exact deadline drain; peak is recorded by engine'},
            'rows':rows,'summary_stats':stats,'paired_contrasts':contrasts,'event_timing_summary':timing,'result_manifest':manifest}
    out.mkdir(parents=True,exist_ok=True)
    (out/'results_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    for key in ('summary_stats','paired_contrasts','event_timing_summary','result_manifest'):write_csv(out/(key+'.csv'),result[key])
    write_csv(out/'normalized_results.csv',rows)
    return result


def mechanism(run_dir,windows):
    from revision_mechanism import analyze_bundle,write_analysis
    run=Path(run_dir);protocol=json.loads((run/'experiment_protocol.json').read_text(encoding='utf-8'))
    for window in windows:
        with (run/'private'/f'data_{window}.pkl').open('rb') as f:data=pickle.load(f)
        arms={}
        for arm in protocol['core']['arms']:
            path=run/'private'/f'{window}_gbt_s13_{arm}_events.pkl'
            with path.open('rb') as f:arms[arm]=pickle.load(f)
        result=analyze_bundle(data['records'],data['gold'],arms)
        result['metadata'].update(seed=13,window=window,source='corrected causal replay, six frozen policies')
        write_analysis(result,run/'reports'/f'mechanism_{window}')
        print(f'{window}: wrote six-arm mechanism analyses from complete private logs')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out-dir',type=Path)
    p.add_argument('--allow-partial',action='store_true',help='exploratory progress only; final outputs must have complete_core=true')
    p.add_argument('--mechanism',action='store_true');p.add_argument('--mechanism-only',action='store_true')
    p.add_argument('--mechanism-windows',nargs='+',default=['May','July'])
    a=p.parse_args()
    if not a.mechanism_only:
        r=summarize(a.run_dir,a.out_dir,a.allow_partial)
        print(f"Summarized {len(r['rows'])} runs; complete_core={r['complete_core']}; {len(r['paired_contrasts'])} metric contrasts")
    if a.mechanism or a.mechanism_only:mechanism(a.run_dir,a.mechanism_windows)


if __name__=='__main__':main()
