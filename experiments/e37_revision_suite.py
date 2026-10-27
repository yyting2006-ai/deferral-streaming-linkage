"""Corrected causal protocol: auditable local preparation, calibration and replay.

Use --run-dir outside the repository: raw records, fitted models, events and
candidate snapshots are private. Only aggregate results/reports are publishable.
The protocol JSON is frozen before preparation, and selected calibration points
are persisted before May/July jobs are submitted. CPU worker count is bounded.
"""
from __future__ import annotations
import os
for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
import argparse, csv, hashlib, json, math, pickle, platform, sys, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from psid.data import load_san_diego, DATA
from psid.gold import build_gold
from psid.train_cand import build_candidate_pairs
from psid.methods import StreamConfig, FEATURE_NAMES
from psid.causal import CausalClusterer
from psid.metrics import evaluate_partition
from psid.decision_metrics import decision_fmp
from experiments.e11d_gbdt import GBT
from experiments.logit_scorer import Logit
from analysis.candidate_analysis import CandidateTraceWriter

def stamp(): return datetime.now(timezone.utc).isoformat()
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024), b''): h.update(b)
    return h.hexdigest()
def json_finite(data):
    """Undefined metrics serialize as null, never as a fabricated zero."""
    if isinstance(data, dict): return {k:json_finite(v) for k,v in data.items()}
    if isinstance(data, (list,tuple)): return [json_finite(v) for v in data]
    if isinstance(data, (float,np.floating)) and not math.isfinite(data): return None
    if isinstance(data, np.generic): return data.item()
    return data
def dump_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(json_finite(data),indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def save_pickle(path,data):
    with Path(path).open('wb') as f: pickle.dump(data,f,protocol=5)
def load_pickle(path):
    with Path(path).open('rb') as f: return pickle.load(f)
def log(s): print(f'[{stamp()}] {s}',flush=True)
def core_hashes(names):
    root=Path(__file__).resolve().parents[1]
    return {name:digest(root/name) for name in names}
def fit_identity(run,family,seed):
    return {'family':family,'seed':seed,
      'protocol_sha256':digest(run/'experiment_protocol.json'),
      'training_features_sha256':digest(run/'private'/'training_features.npz'),
      'sources':core_hashes(('psid/methods.py','psid/train_cand.py','psid/schema.py',
                            'experiments/e11d_gbdt.py','experiments/logit_scorer.py'))}
def replay_identity(run,job):
    return {'job':job,'protocol_sha256':digest(run/'experiment_protocol.json'),
      'preparation_manifest_sha256':digest(run/'reports'/'preparation_manifest.json'),
      'model_sha256':digest(run/'private'/f"model_{job['family']}_{job['seed']}.pkl"),
      'sources':core_hashes(('psid/causal.py','psid/methods.py','psid/schema.py',
                            'psid/metrics.py','psid/decision_metrics.py','psid/gold.py'))}
def require_cache_identity(found,expected,label):
    if found != expected:
        raise RuntimeError(f'{label}: cached inputs/configuration/source do not match; use a fresh run directory or explicitly regenerate the stale artifact')

def prepare(run_dir):
    run=Path(run_dir); private=run/'private'; private.mkdir(exist_ok=True,parents=True)
    protocol=json.loads((run/'experiment_protocol.json').read_text(encoding='utf-8'))
    log('Loading San Diego; interpreting local timestamps and converting to UTC')
    records,links=load_san_diego()
    local=ZoneInfo('America/Los_Angeles'); windows={k:[] for k in protocol['windows']}; training=[]
    for rec in records:
        day=rec.when.date().isoformat()
        rec.when=rec.when.replace(tzinfo=local).astimezone(timezone.utc)
        if '2024-01-01'<=day<='2024-02-29': training.append(rec)
        for name,(a,b) in protocol['windows'].items():
            if a<=day<=b: windows[name].append(rec)
    # Link lag metadata and mutable Record timestamps must share one time axis.
    gold=build_gold('san_diego',records,links)
    for name,rs in windows.items():
        rs.sort(key=lambda r:(r.when,r.rid)); save_pickle(private/f'data_{name}.pkl',{'records':rs,'gold':gold})
    training.sort(key=lambda r:(r.when,r.rid))
    log(f'Building candidate features from first 22000 of {len(training)} training records')
    X,y=build_candidate_pairs(training,gold,max_records=22000,seed=13)
    np.savez_compressed(private/'training_features.npz',X=X,y=y)
    import scipy, sklearn
    root=Path(__file__).resolve().parents[1]
    raw=DATA/'SanDiegoGetItDone'/'closed2024'/'get_it_done_requests_closed_2024_datasd.csv'
    manifest={'prepared_at_utc':stamp(),'protocol_sha256':digest(run/'experiment_protocol.json'),
      'raw_data_sha256':digest(raw),'raw_records':len(records),'raw_links':len(links),
      'window_records':{k:len(v) for k,v in windows.items()},'training_records_available':len(training),
      'training_pairs':len(y),'positive_training_pairs':int(y.sum()),'feature_names':FEATURE_NAMES,
      'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'scipy':scipy.__version__,'sklearn':sklearn.__version__,
      'source_hashes':{str(p.relative_to(root)).replace('\\','/'):digest(p) for base in ('psid','experiments','analysis') for p in sorted((root/base).glob('*.py'))}}
    dump_json(run/'reports'/'preparation_manifest.json',manifest); log(f'Prepared {len(y)} pairs, {int(y.sum())} positive')

def fit_job(run_dir,family,seed):
    run=Path(run_dir); path=run/'private'/f'model_{family}_{seed}.pkl'
    report=run/'results'/f'fit_{family}_{seed}.json'; identity=fit_identity(run,family,seed)
    if path.exists():
        if not report.exists(): raise RuntimeError(f'{path.name}: model exists without provenance report')
        previous=json.loads(report.read_text(encoding='utf-8'))
        require_cache_identity(previous.get('cache_identity'),identity,path.name)
        if previous.get('model_sha256')!=digest(path): raise RuntimeError(f'{path.name}: model checksum mismatch')
        return {'fit':str(path.name),'cached':True}
    a=np.load(run/'private'/'training_features.npz'); t=time.perf_counter()
    model=(GBT if family=='gbt' else Logit)(a['X'],a['y'],seed=seed)
    save_pickle(path,model)
    result={'family':family,'seed':seed,'fit_seconds':time.perf_counter()-t,'model_sha256':digest(path),'finished_utc':stamp(),'cache_identity':identity}
    dump_json(report,result); return result

def replay_job(run_dir,job):
    run=Path(run_dir); name=job['run_id']; output=run/'results'/f'{name}.json'
    identity=replay_identity(run,job)
    if output.exists():
        previous=json.loads(output.read_text(encoding='utf-8'))
        require_cache_identity(previous.get('cache_identity'),identity,name)
        if job.get('retain_events',True) and not (run/'private'/f'{name}_events.pkl').exists():
            raise RuntimeError(f'{name}: required private event artifact missing')
        if job.get('trace',False) and not (run/'private'/f'{name}_candidates.jsonl').exists():
            raise RuntimeError(f'{name}: required private candidate trace missing')
        return {'run_id':name,'cached':True}
    family,seed,window=job['family'],job['seed'],job['window']
    data=load_pickle(run/'private'/f'data_{window}.pkl'); rs,gold=data['records'],data['gold']
    model=load_pickle(run/'private'/f'model_{family}_{seed}.pkl')
    cfg=StreamConfig(t_merge=job['tau'],t_pending=0,window_days=60,k_candidates=60,max_cluster=20)
    cl=CausalClusterer(cfg,model,trace_candidates=job.get('trace',False),**job['policy'])
    trace_path=run/'private'/f'{name}_candidates.jsonl'
    writer=CandidateTraceWriter(trace_path) if job.get('trace',False) else None
    t=time.perf_counter(); cpu=time.process_time(); sample=[]; trace_io_seconds=0.0
    try:
        for rec in rs:
            start=time.perf_counter(); cl.on_record(rec); sample.append((time.perf_counter()-start)*1000)
            if writer:
                io=time.perf_counter(); writer.drain(cl); trace_io_seconds+=time.perf_counter()-io
        drain=time.perf_counter(); cl.finish(); drain_seconds=time.perf_counter()-drain
        if writer:
            io=time.perf_counter(); writer.drain(cl); trace_io_seconds+=time.perf_counter()-io
    finally:
        if writer: writer.close()
    elapsed=time.perf_counter()-t; cpu_seconds=time.process_time()-cpu
    summary=cl.summary(); ev=evaluate_partition(cl.partition(),gold,{r.rid for r in rs}); dec=decision_fmp(cl.merge_log,gold)
    assert summary['complete'] and summary['unique_finalizations'] and summary['max_cluster_size']<=20 and not summary['n_pending']
    result={'job':job,'finished_utc':stamp(),'metrics':ev,'decision':dec,'protocol_summary':summary,'cache_identity':identity,
      'undefined_metric_encoding':'null means the metric denominator is zero; it is not a zero score',
      'candidate_trace':{'enabled':bool(writer),'rows':writer.rows if writer else 0,'candidate_instances':writer.candidate_instances if writer else 0},
      'runtime':{'replay_wall_seconds':elapsed,'process_cpu_seconds':cpu_seconds,'drain_seconds':drain_seconds,'trace_io_seconds':trace_io_seconds,'arrival_mean_ms':float(np.mean(sample)) if sample else None,'arrival_p95_ms':float(np.quantile(sample,.95)) if sample else None, 'measurement_note':'Concurrent local CPU workers; wall time includes trace serialization when enabled. Arrival timing excludes serialization but includes due timers and snapshot construction. These are not isolated throughput benchmarks.'}}
    if job.get('retain_events',True):
        save_pickle(run/'private'/f'{name}_events.pkl',{'events':cl.events,'partition':cl.partition(),'candidate_snapshots':[],
          'candidate_trace_path':str(trace_path.resolve()) if writer else None,'merge_log':cl.merge_log})
    dump_json(output,result)
    return json_finite({'run_id':name,'f1':ev['f1'],'recall':ev['recall'],'fmp':dec['dec_fmp'],'wall_s':round(elapsed,2)})

def parallel(call,jobs,workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(call,*args) for args in jobs]
        for f in as_completed(futures): log(json.dumps(f.result(),ensure_ascii=False))

def make_job(family,seed,window,arm,policy,tau,**kw):
    return {'run_id':f'{window}_{family}_s{seed}_{arm}','family':family,'seed':seed,'window':window,'arm':arm,'policy':policy,'tau':tau,**kw}

def calibrate(run_dir,workers):
    run=Path(run_dir); p=json.loads((run/'experiment_protocol.json').read_text(encoding='utf-8')); jobs=[]
    for fam in ('gbt','logit'):
        for tau in p['threshold']['grid']:
            jobs.append((run_dir,make_job(fam,13,'March',f'tau{tau:g}',{'delta':0},tau,retain_events=False)))
    parallel(replay_job,jobs,workers)
    selection={'frozen_at_utc':stamp(),'protocol_sha256':digest(run/'experiment_protocol.json'),'rule':p['threshold']['selection'],'families':{}}
    for fam in ('gbt','logit'):
        rows=[json.loads((run/'results'/f'March_{fam}_s13_tau{tau:g}.json').read_text()) for tau in p['threshold']['grid']]
        volume=[r for r in rows if r['decision']['decisions']>=.01*r['metrics']['n_records'] and r['decision']['dec_correct']+r['decision']['dec_wrong']>=100]
        feasible=[r for r in volume if r['decision']['dec_fmp']<=.4]
        if not volume: raise RuntimeError('No nondegenerate calibration point; protocol amendment required before July')
        def rank_metric(row,key):
            value=row['metrics'][key]
            return float(value) if value is not None else -math.inf
        chosen=max(feasible,key=lambda r:(rank_metric(r,'recall'),rank_metric(r,'f1'),r['job']['tau'])) if feasible else min(volume,key=lambda r:(r['decision']['dec_fmp'],-rank_metric(r,'recall'),-rank_metric(r,'f1'),-r['job']['tau']))
        selection['families'][fam]={'tau':chosen['job']['tau'],'risk_feasible':bool(feasible),'selected_run_id':chosen['job']['run_id'],'metrics':chosen['metrics'],'decision':chosen['decision']}
    dump_json(run/'reports'/'calibration_selection.json',selection); log(json.dumps(selection))

def suite(run_dir,workers):
    run=Path(run_dir); p=json.loads((run/'experiment_protocol.json').read_text(encoding='utf-8')); sel=json.loads((run/'reports'/'calibration_selection.json').read_text())
    jobs=[]; tau=sel['families']['gbt']['tau']; ltau=sel['families']['logit']['tau']
    for w in p['core']['windows']:
        for seed in p['core']['seeds']:
            for arm in p['core']['arms']:
                jobs.append(make_job('gbt',seed,w,arm,p['policies'][arm],tau,trace=(seed==13)))
        for arm in p['secondary']['logit']['arms']:
            jobs.append(make_job('logit',13,w,arm,p['policies'][arm],ltau))
    for t in p['threshold']['grid']:
        jobs.append(make_job('gbt',13,'May',f'tau{t:g}',{'delta':0},t,retain_events=False))
    for arm in ('band','gate5'):
        for d in (3,14):
            cfg=dict(p['policies'][arm]); cfg['D']=d
            jobs.append(make_job('gbt',13,'May',f'{arm}_D{d}',cfg,tau))
    for d in (.3,.5,.7): jobs.append(make_job('gbt',13,'April',f'band_delta{d}',{'delta':d,'D':7},tau))
    for h in (2,3,5): jobs.append(make_job('gbt',13,'April',f'gate_h{h}',{'delta':.5,'D':7,'defer_rule':'gate','stale_min':h},tau))
    for x in (5,10,14): jobs.append(make_job('gbt',13,'April',f'cutoff{x}',{'delta':0,'max_lag':x},tau))
    dump_json(run/'reports'/'run_schedule.json',{'scheduled_before_execution_at_utc':stamp(),'selection_sha256':digest(run/'reports'/'calibration_selection.json'),'jobs':jobs})
    parallel(replay_job,[(run_dir,j) for j in jobs],workers)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('stage',choices=['prepare','fit','calibrate','suite']); ap.add_argument('--run-dir',required=True); ap.add_argument('--workers',type=int,default=6); a=ap.parse_args()
    if a.stage=='prepare': prepare(a.run_dir)
    elif a.stage=='fit': parallel(fit_job,[(a.run_dir,'gbt',s) for s in (1,2,3,4,13)]+[(a.run_dir,'logit',13)],a.workers)
    elif a.stage=='calibrate': calibrate(a.run_dir,a.workers)
    else: suite(a.run_dir,a.workers)
if __name__=='__main__': main()
