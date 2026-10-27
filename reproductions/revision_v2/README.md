# Corrected event-time evidence

`experiment_protocol.json` is the frozen configuration. `results/` contains
120 completed replay aggregates and six fit reports. `reports/` includes exact
integer-count summaries, matched five-seed contrasts, time/denominator audits,
mechanism decompositions and all 12 traced seed-13 candidate analyses. `figures/`
contains four exports with their source-data CSVs.

`package_manifest.json` records the exact copied source files and SHA-256 hashes.
The combined `reports/candidate_analysis.json` is assembled from the twelve
per-policy reports; its constituents are individually included in that manifest.
The repository-level `MANIFEST.json` also covers this combined report.

Use [ARTIFACT.md](../../ARTIFACT.md) for a short numerical check and the mapping
between figures/tables and files, or [REPRODUCE.md](../../docs/REPRODUCE.md) for a
full fresh-run procedure. Raw source records, private replay logs, trained models
and manuscript files are not in this directory.
