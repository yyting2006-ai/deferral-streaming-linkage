"""Recompute Louisville agreement and issue-resampling summaries from released data.

No private files, network, original text, dates, services, addresses or identifiers
are needed. Candidate eligibility is precomputed and cannot be reconstructed from
this deliberately minimal anonymous graph.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'derived_data'/'louisville'


def choose2(n):
    return n*(n-1)//2


def agreement(records):
    labels=[(r['annotator_a_cluster'],r['annotator_b_cluster']) for r in records]
    cells=Counter(labels);aa=Counter(a for a,b in labels);bb=Counter(b for a,b in labels)
    both=sum(choose2(n) for n in cells.values())
    sa,sb=sum(choose2(n) for n in aa.values()),sum(choose2(n) for n in bb.values())
    n=choose2(len(records));expected=sa*sb/n;den=(sa+sb)/2-expected
    neither=n-sa-sb+both
    return {'records':len(records),'pairs':n,'both_positive':both,
            'a_positive_b_negative':sa-both,'a_negative_b_positive':sb-both,'both_negative':neither,
            'raw_pair_agreement':(both+neither)/n,'positive_pair_dice':2*both/(sa+sb),
            'adjusted_rand_index':(both-expected)/den if den else 1.0}


def compute(path,draws=4000,seed=20261027):
    j=json.loads(Path(path).read_text(encoding='utf-8'))
    records=j['records'];byid={r['id']:r for r in records}
    if len(byid)!=len(records):raise ValueError('duplicate anonymous record IDs')
    confirmed=[r for r in records if r['final_cluster'] is not None]
    clusters=j['bootstrap_cluster_order']
    if len(set(clusters))!=len(clusters) or set(clusters)!={r['final_cluster'] for r in confirmed}:
        raise ValueError('resampling-unit list disagrees with final partition')
    ci={g:i for i,g in enumerate(clusters)}
    grouped=defaultdict(list);wave_counts=defaultdict(lambda:Counter());seen=set()
    for p in j['eligible_pairs']:
        key=frozenset((p['a'],p['b']))
        if len(key)!=2 or key in seen:raise ValueError('duplicate or self candidate pair')
        seen.add(key)
        a,b=byid[p['a']],byid[p['b']]
        if a['final_cluster'] is None or b['final_cluster'] is None:raise ValueError('unadjudicable candidate endpoint')
        ca,cb=ci[a['final_cluster']],ci[b['final_cluster']]
        grouped[p['lag_bin']].append((ca,cb,int(ca==cb)))
        wave=str(a['wave']) if a['wave']==b['wave'] else 'cross_wave'
        wave_counts[wave,p['lag_bin']]['pairs']+=1
        wave_counts[wave,p['lag_bin']]['same']+=int(ca==cb)
    rng=np.random.default_rng(seed)
    weights=rng.multinomial(len(clusters),np.repeat(1/len(clusters),len(clusters)),size=draws)
    buckets={}
    for bucket in ('0-1','2-3','4-6','7-13'):
        edges=np.asarray(grouped[bucket],dtype=int);a,b,y=edges.T
        edge_weights=np.where(a==b,weights[:,a],weights[:,a]*weights[:,b])
        totals=edge_weights.sum(axis=1)
        rates=np.divide(edge_weights@y,totals,out=np.full(draws,np.nan),where=totals>0)
        buckets[bucket]={'pairs':len(edges),'same':int(y.sum()),'rate':float(y.mean()),
                         'issue_bootstrap_q025':float(np.nanquantile(rates,.025)),
                         'issue_bootstrap_q975':float(np.nanquantile(rates,.975)),
                         'valid_draws':int(np.isfinite(rates).sum())}
    return {'input_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'ab_all_provisional_rows':agreement(records),
            'ab_excluding_either_pending':agreement([r for r in records if not r['annotator_a_pending'] and not r['annotator_b_pending']]),
            'lag_analysis':{'records':len(confirmed),'issues':len(clusters),
                            'waves':dict(Counter(str(r['wave']) for r in confirmed)),
                            'buckets':buckets,'wave_breakdown':[dict(wave=w,lag_bucket=b,**v) for (w,b),v in sorted(wave_counts.items())]},
            'resampling':{'draws':draws,'seed':seed,
                          'interpretation':'issue-resampling sensitivity intervals; not equivalence or population-generalizable confidence'}}


def verify(actual,expected):
    """Verify all released numerical assertions, with only floating-point tolerance."""
    comparisons=0
    def compare(a,e,path):
        nonlocal comparisons
        if isinstance(e,dict):
            for k,v in e.items():compare(a[k],v,path+'.'+k)
        elif isinstance(e,list):
            if len(a)!=len(e):raise AssertionError(path+': list length mismatch')
            for i,v in enumerate(e):compare(a[i],v,f'{path}[{i}]')
        elif isinstance(e,(float,int)):
            if not np.isclose(a,e,rtol=0,atol=1e-12):raise AssertionError(f'{path}: {a!r} != {e!r}')
            comparisons+=1
        elif a!=e:raise AssertionError(f'{path}: {a!r} != {e!r}')
    compare(actual,expected,'summary')
    return comparisons


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=DATA/'anonymous_issue_graph.json')
    p.add_argument('--out',type=Path)
    p.add_argument('--verify',action='store_true')
    p.add_argument('--draws',type=int,default=4000)
    p.add_argument('--seed',type=int,default=20261027)
    a=p.parse_args();r=compute(a.data,a.draws,a.seed)
    if a.verify:
        n=verify(r,json.loads((DATA/'expected_summary.json').read_text(encoding='utf-8')))
        print(f'PASS: {n} published numerical values match independently recomputed values')
    if a.out:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        a.out.write_text(json.dumps(r,indent=2),encoding='utf-8')
    print(json.dumps(r,indent=2))


if __name__=='__main__':main()
