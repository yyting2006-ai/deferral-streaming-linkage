"""Recompute headline seed effects and mechanism counts from aggregate files."""
import csv
import json
import statistics
from pathlib import Path
RES=Path(__file__).resolve().parents[1]/'results'

def pick(filename,delta,visible=True):
    d=json.loads((RES/filename).read_text(encoding='utf-8'))
    return next(r for r in d['rows'] if r['delta']==delta and r.get('visible',True)==visible and (delta==0 or r['deadline_days']==7))

def summarize(a,b):
    x=[r['pair_f1'] for r in a]; y=[r['pair_f1'] for r in b]
    assert len(x)==len(y)
    delta=[v-u for u,v in zip(x,y)]
    return {'n_seeds':len(x),'arrival_f1':statistics.mean(x),'deferred_f1':statistics.mean(y),'paired_delta_mean':statistics.mean(delta),'paired_delta_sd':statistics.stdev(delta)}

def check():
    base=['e26_decision_timing.json','e26_seed1.json','e26_seed2.json','e26_may_s3.json','e26_may_s4.json','e26_seed5.json','e26_seed6.json','e26_seed7.json','e26_seed8.json']
    deferred=['e26_may_cliff.json','e26_may_d05_s1.json','e26_may_d05_s2.json',*base[3:]]
    may=summarize([pick(f,0) for f in base],[pick(f,.5) for f in deferred])
    june_files=['e26_june.json']+[f'e26_june_s{s}.json' for s in range(1,5)]
    june=summarize([pick(f,0) for f in june_files],[pick(f,.5) for f in june_files])
    inv=['e26_ablation_invisible.json']+[f'e26_invis_s{s}.json' for s in range(1,5)]
    ablation=summarize([pick(f,0) for f in base[:5]],[pick(f,.5,False) for f in inv])
    assert abs(may['paired_delta_mean']-.037101)<1e-10
    assert abs(june['paired_delta_mean']-.015011)<1e-10
    assert abs(ablation['paired_delta_mean']-(-.0029858))<1e-10
    strata=json.loads((RES/'e30_lag_stratified.json').read_text())
    wrong=strata['wrong_merges_by_lag']
    net=sum(r['wrong_base']-r['wrong_defer'] for r in wrong)
    assert net==262
    evidence=Path(__file__).resolve().parent/'evidence'
    with (evidence/'positive_pair_set_counts.csv').open(encoding='utf-8',newline='') as handle:
        positive_rows=list(csv.DictReader(handle))
    original_pos={r['bucket']:r for r in strata['positive_pairs_by_lag']}
    for row in positive_rows:
        for key in ('both','base_only','defer_only','neither','n'):
            assert int(row[key])==original_pos[row['bucket']][key]
    with (evidence/'wrong_merge_pair_set_counts.csv').open(encoding='utf-8',newline='') as handle:
        wrong_rows=list(csv.DictReader(handle))
    original_wrong={r['bucket']:r for r in wrong}
    for row in wrong_rows:
        source=original_wrong[row['target_age_bucket']]
        assert int(row['both'])+int(row['arrival_only'])==source['wrong_base']
        assert int(row['both'])+int(row['band_only'])==source['wrong_defer']
    lv=json.loads((RES/'e35b_louisville.json').read_text())
    assert abs(lv['p01']-lv['buckets']['0-1']['same']/lv['buckets']['0-1']['pairs'])<1e-12
    assert abs(lv['p713']-lv['buckets']['7-13']['same']/lv['buckets']['7-13']['pairs'])<1e-12
    return {'may_band':may,'june_band':june,'matched_invisible_ablation':ablation,'wrong_merges_net_reduction':net,'stratified_csv_counts':'passed','louisville_bucket_arithmetic':'passed','scope':'Recomputation from recorded aggregates; no raw-data replay.'}

if __name__=='__main__': print(json.dumps(check(),indent=2))
