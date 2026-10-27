"""Fetch authorized snapshot assets (default) or mutable source portal exports.

Release archives are accepted only when archive SHA256, sole member name,
member size, and extracted CSV SHA256 all match the recorded specification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parent
SPECS = json.loads((ROOT / 'docs/input_snapshots.json').read_text())
PATHS = {
    'san_diego_2024': 'SanDiegoGetItDone/closed2024/get_it_done_requests_closed_2024_datasd.csv',
    'chicago_2026q1': 'Chicago311/2026Q1/chicago311_2026q1_six_fields.csv',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
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


def expected_path(data_dir, member):
    base = Path(data_dir).resolve()
    destination = (base / member).resolve()
    if not destination.is_relative_to(base) or destination == base:
        raise ValueError('Snapshot member escapes the selected data directory')
    return destination


def extract_verified(archive, data_dir, specification):
    """Extract only one exact expected regular file; never call extractall()."""
    expected_member = specification['member']
    destination = expected_path(data_dir, expected_member)
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite existing input: {destination}')
    if digest(archive) != specification['zip_sha256']:
        raise ValueError('Release archive checksum mismatch')
    base = Path(data_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) != 1 or entries[0].filename != expected_member:
            raise ValueError('Archive must contain exactly the recorded member path')
        info = entries[0]
        file_kind = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or file_kind not in (0, stat.S_IFREG):
            raise ValueError('Snapshot member must be a regular file')
        if info.file_size != specification['bytes']:
            raise ValueError('Archive member size differs from the recorded snapshot')
        with tempfile.TemporaryDirectory(prefix='.snapshot-extract-', dir=base) as temporary:
            staged = Path(temporary) / 'verified.csv'
            total = 0
            with zipped.open(info, 'r') as source, staged.open('wb') as target:
                while block := source.read(1 << 20):
                    total += len(block)
                    if total > specification['bytes']:
                        raise ValueError('Extracted member exceeds the recorded size')
                    target.write(block)
            if total != specification['bytes'] or digest(staged) != specification['sha256']:
                raise ValueError('Extracted CSV checksum or size mismatch')
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Recheck after creating parents; existing symlinks cannot redirect
            # the write beyond the explicitly selected input directory.
            if expected_path(base, expected_member) != destination:
                raise ValueError('Input destination changed during extraction')
            if destination.exists():
                raise FileExistsError(f'Refusing to overwrite existing input: {destination}')
            os.replace(staged, destination)
    return destination


def download(url, path):
    request = urllib.request.Request(url, headers={'User-Agent':'Deferral-Streaming-Linkage-Research/2.0'})
    with urllib.request.urlopen(request, timeout=120) as response, Path(path).open('wb') as output:
        shutil.copyfileobj(response, output, length=1 << 20)


def fetch_snapshot(name, data_dir, source='release'):
    spec = SPECS[name]
    base = Path(data_dir).resolve()
    destination = expected_path(base, PATHS[name])
    if destination.exists():
        return destination
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.snapshot-download-', dir=base) as temporary:
        staged = Path(temporary) / ('snapshot.zip' if source == 'release' else 'snapshot.csv')
        download(spec['release_url'] if source == 'release' else source_url(name), staged)
        if source == 'release':
            if staged.stat().st_size != spec['zip_bytes']:
                raise ValueError('Downloaded archive size mismatch')
            return extract_verified(staged, base, spec)
        if staged.stat().st_size != spec['bytes'] or digest(staged) != spec['sha256']:
            raise ValueError('Portal snapshot mismatch; preserve a different export separately as a new input version')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if expected_path(base, PATHS[name]) != destination or destination.exists():
            raise ValueError('Input destination changed during download')
        os.replace(staged, destination)
    return destination


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('snapshot',choices=PATHS)
    ap.add_argument('--data-dir',type=Path,default=ROOT/'data')
    ap.add_argument('--source',choices=('release','portal'),default='release')
    ap.add_argument('--verify-only',action='store_true')
    args=ap.parse_args()
    path=expected_path(args.data_dir,PATHS[args.snapshot])
    if not path.exists() and not args.verify_only:
        try:
            path=fetch_snapshot(args.snapshot,args.data_dir,args.source)
        except (urllib.error.URLError,OSError,ValueError,BadZipFile) as exc:
            raise SystemExit(f'Download/verification failed: {exc}. See docs/DATA.md for source access and manual verification.')
    if not path.is_file():
        raise SystemExit(f'Missing local input: {path}')
    actual=digest(path); spec=SPECS[args.snapshot]
    matches=actual==spec['sha256'] and path.stat().st_size==spec['bytes']
    print(json.dumps({'snapshot':args.snapshot,'bytes':path.stat().st_size,'sha256':actual,'matches_recorded_snapshot':matches},indent=2))
    if not matches:
        raise SystemExit('Snapshot mismatch: retain as a new input version; do not call this an exact reproduction.')

if __name__=='__main__': main()
