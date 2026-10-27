"""Compare complete timestamped emission prefixes under a shared RNG state.

Standalone stdlib-only comparator. It does not infer emission events from final
partitions or pair-only logs. CLI: python compare_prefix.py A.json B.json --cutoff
2024-05-01T12:00:00+00:00. JSON format is illustrated in test_compare_prefix.py.
Each run has a common_randomness SHA256 fingerprint and an events array with
emitted_at, object_id, action, target_id (nullable), and optional payload.
"""
from __future__ import annotations
import argparse, hashlib, json, random
from datetime import datetime, timezone
from pathlib import Path

def absolute_time(value: str) -> datetime:
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('emitted_at and cutoff require an explicit timezone')
    return dt.astimezone(timezone.utc)

def canonical_event(row: dict) -> tuple:
    for key in ('emitted_at','object_id','action','target_id'):
        if key not in row:raise ValueError(f'missing required event field: {key}')
    if not isinstance(row['object_id'],str) or not isinstance(row['action'],str):
        raise ValueError('object_id and action must be stable strings')
    if row['target_id'] is not None and not isinstance(row['target_id'],str):
        raise ValueError('target_id must be a stable string or null')
    # Retain additional fields in the equality test, rather than silently ignoring
    # consequential payload changes. Input ordering is retained for equal times.
    content={k:v for k,v in row.items() if k!='emitted_at'}
    return (absolute_time(row['emitted_at']),
            json.dumps(content,sort_keys=True,separators=(',',':'),ensure_ascii=False))

def full_prefix(run: dict, cutoff: datetime) -> list[tuple]:
    rows=[canonical_event(row) for row in run['events']]
    if any(rows[i][0]>rows[i+1][0] for i in range(len(rows)-1)):
        raise ValueError('event log is not in emission order')
    # Both runs independently use the SAME absolute cutoff. No k-row slicing.
    return [row for row in rows if row[0]<=cutoff]

def compare(run_a: dict, run_b: dict, cutoff: str) -> dict:
    ra=run_a.get('common_randomness');rb=run_b.get('common_randomness')
    if not ra or ra!=rb:
        return {'valid_comparison':False,'equal':None,
                'reason':'common initial randomness state is missing or differs'}
    T=absolute_time(cutoff)
    a,b=full_prefix(run_a,T),full_prefix(run_b,T)
    first=next((i for i in range(min(len(a),len(b))) if a[i]!=b[i]),None)
    if first is None and len(a)!=len(b):first=min(len(a),len(b))
    return {'valid_comparison':True,'equal':a==b,'cutoff_utc':T.isoformat(),
            'a_prefix_count':len(a),'b_prefix_count':len(b),
            'first_different_index_0':first,
            'compared_fields':'length, order, absolute emission time, object, action, target and payload'}

def shared_randomness(seed: int) -> tuple[object,str]:
    """Capture an initial Python RNG state; production must include every RNG.

    A real replay envelope should fingerprint all additional model/library RNG
    states and immutable scorer/config snapshots, not just this helper's state.
    """
    state=random.Random(seed).getstate()
    fingerprint=hashlib.sha256(json.dumps(state,separators=(',',':')).encode()).hexdigest()
    return state,fingerprint

def cloned_rng(state: object) -> random.Random:
    rng=random.Random();rng.setstate(state);return rng

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('a',type=Path);ap.add_argument('b',type=Path)
    ap.add_argument('--cutoff',required=True);ap.add_argument('--out',type=Path)
    args=ap.parse_args()
    result=compare(json.loads(args.a.read_text(encoding='utf-8')),
                   json.loads(args.b.read_text(encoding='utf-8')),args.cutoff)
    text=json.dumps(result,indent=2)
    if args.out:args.out.write_text(text+'\n',encoding='utf-8')
    print(text)
    raise SystemExit(0 if result['valid_comparison'] and result['equal'] else 1)
