"""Verify every tracked release file against its SHA-256 manifest."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    manifest=json.loads((ROOT/'MANIFEST.json').read_text())
    failed=[]
    for name,expected in manifest['files'].items():
        p=ROOT/name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected['sha256']:
            failed.append(name)
    print(json.dumps({'checked_files':len(manifest['files']),'failed':failed},indent=2))
    raise SystemExit(bool(failed))

if __name__=='__main__': main()
