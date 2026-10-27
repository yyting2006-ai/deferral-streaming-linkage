"""Fetch public portal inputs locally and require the recorded snapshot digest."""
from __future__ import annotations
import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPECS = json.loads((ROOT / 'docs/input_snapshots.json').read_text())
PATHS = {
    'san_diego_2024': 'SanDiegoGetItDone/closed2024/get_it_done_requests_closed_2024_datasd.csv',
    'chicago_2026q1': 'Chicago311/2026Q1/chicago311_2026q1_six_fields.csv',
}

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def source_url(name):
    if name == 'san_diego_2024':
        return 'https://seshat.datasd.org/get_it_done_requests_closed/get_it_done_requests_closed_2024_datasd.csv'
    query = urllib.parse.urlencode({
        '$select':'sr_number,parent_sr_number,created_date,sr_type,street_address,duplicate',
        '$where':"created_date >= '2026-01-01T00:00:00' AND created_date < '2026-04-01T00:00:00'",
        '$order':'created_date,sr_number', '$limit':1000000,
    })
    return 'https://data.cityofchicago.org/resource/v6vf-nfxy.csv?' + query

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('snapshot',choices=PATHS)
    ap.add_argument('--data-dir',type=Path,default=ROOT/'data')
    ap.add_argument('--verify-only',action='store_true')
    args=ap.parse_args()
    path=args.data_dir/PATHS[args.snapshot]
    if not path.exists() and not args.verify_only:
        path.parent.mkdir(parents=True,exist_ok=True)
        request=urllib.request.Request(source_url(args.snapshot),headers={'User-Agent':'Deferral-Streaming-Linkage-Research/1.0'})
        try:
            with urllib.request.urlopen(request,timeout=120) as response, path.open('wb') as output:
                while block:=response.read(1<<20): output.write(block)
        except (urllib.error.URLError,OSError) as exc:
            raise SystemExit(f'Download failed: {exc}. Obtain the portal export manually; see docs/DATA.md.')
    if not path.is_file():
        raise SystemExit(f'Missing local input: {path}')
    actual=digest(path); expected=SPECS[args.snapshot]['sha256']
    print(json.dumps({'snapshot':args.snapshot,'bytes':path.stat().st_size,'sha256':actual,'matches_recorded_snapshot':actual==expected},indent=2))
    if actual!=expected:
        raise SystemExit('Snapshot mismatch: retain as a new input version; do not call this an exact reproduction.')

if __name__=='__main__': main()
