"""Build the release checksum manifest from Git-visible reviewed files.

Ignored/private outputs are never enumerated. Refuse sensitive file types,
unexpected top-level paths, known local-user paths, and credential-shaped text.
This is a local packaging aid, not a complete privacy or security guarantee.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"analysis", "audit", "docs", "experiments", "psid", "results",
               "tables", "tests", "derived_data", "reproductions"}
TOP_FILES = {".gitattributes", ".gitignore", "README.md", "LICENSE", "CITATION.cff",
             "ARTIFACT.md", "fetch_data.py", "requirements.txt",
             "requirements-baselines.txt", "requirements-figures.txt"}
FORBIDDEN_SUFFIX = {".pkl", ".pickle", ".joblib", ".npz", ".npy", ".pt", ".pth",
                    ".jsonl", ".xlsx", ".xls", ".docx", ".zip"}
TEXT_SUFFIX = {".py", ".md", ".txt", ".json", ".csv", ".cff", ".yml", ".yaml", ".svg"}
PATTERNS = {
    "local-user-path": re.compile(r"(?:[A-Z]:[/\\]Users[/\\]|E:[/\\]VScode[/\\])", re.I),
    "github-token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "api-key": re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def visible_files(root=ROOT):
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    return sorted(set(item.decode("utf-8") for item in output.split(b"\0") if item))


def inspect(root=ROOT):
    entries, findings = {}, []
    for name in visible_files(root):
        if name == "MANIFEST.json":
            continue
        relative, path = Path(name), root / name
        if not path.is_file():
            findings.append({"file": name, "issue": "missing file"})
            continue
        if relative.parts[0] not in DIRECTORIES and name not in TOP_FILES:
            findings.append({"file": name, "issue": "unexpected root path"})
        if relative.suffix.lower() in FORBIDDEN_SUFFIX or any(
                part in {"private", "data", "cache", "replays", ".venv", "__pycache__"}
                for part in relative.parts):
            findings.append({"file": name, "issue": "private or binary intermediate path"})
        payload = path.read_bytes()
        if relative.suffix.lower() in TEXT_SUFFIX or name in TOP_FILES:
            content = payload.decode("utf-8-sig")
            for label, pattern in PATTERNS.items():
                if pattern.search(content):
                    findings.append({"file": name, "issue": label})
        entries[name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    return entries, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    entries, findings = inspect()
    print(json.dumps({"checked_files": len(entries), "findings": findings}, indent=2))
    if findings:
        raise SystemExit(1)
    if not args.check_only:
        manifest = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "scope": "Git-visible code, aggregate evidence, figures, synthetic tests and authorized anonymous derivative; excludes this manifest itself",
                    "files": entries}
        (ROOT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
