# Corrected event-time research artifact

This release contains the corrected CPU implementation, 120 completed aggregate
replays, six fit-provenance reports, the frozen protocol, full matched-seed
summaries, mechanism and candidate diagnostics, and four reproducible figures.
The original San Diego and Chicago municipal snapshots are separate `v0.2.0`
Release ZIP assets. Their availability and public-download verification status
are tracked in [DATA.md](docs/DATA.md).

## Quick numerical check

Use Python 3.11 from the repository root:

```text
python -m pip install -r requirements.txt
python -m analysis.verify_manifest
python -m analysis.summarize_revision --run-dir reproductions/revision_v2 --out-dir build/revision_v2_summary
python -m analysis.reproduce_louisville --verify --out build/louisville_reanalysis.json
```

The summary must report `120` replays and `complete_core=True`. It recomputes
quality statistics from integer counts, including 60 primary runs (two months,
five fit seeds and six policies), 26 March calibration runs and 34 secondary runs.
The Louisville command must verify all 46 numerical targets. The 4,000 issue
resamples are deterministic at seed 20261027.

Aggregate-only reproduction does not reconstruct event-dependent timing,
mechanism or candidate traces. The released trace-derived aggregates remain
available for inspection; rebuilding them requires the verified San Diego input
and a new full run.

## Results and figures

| Output | Released evidence | Reproduction |
|---|---|---|
| Main mean/SD and paired intervals | `reports/summary_stats.csv`, `paired_contrasts.csv` | Aggregate summary command above |
| Absolute NEW/MERGE/C/W/U denominators | `reports/normalized_results.csv` | Aggregate summary command above |
| Exact deadline/held latency and occupancy | `reports/event_timing_summary.csv` | Full local replay, then summary |
| Logistic and threshold sensitivities | `results/`, `reports/normalized_results.csv` | Aggregate summary or full replay |
| Pair gain/loss and wrong-operation differences | `reports/mechanism_May/`, `mechanism_July/` | Full local replay, then `--mechanism` |
| Direct coverage and graph bounds | `reports/candidate_*.json` | Full local replay, then candidate analysis |
| Real-stream prefix and hash-seed controls | `reports/prefix_audit.json`, `hashseed_replay.json` | Audit commands in REPRODUCE.md |
| Manuscript Figures 2–5 | `figures/*.pdf`, `*.svg`, `*.png`, `*.source_data.csv` | Figure command below |
| Louisville agreement and lag rates | `derived_data/louisville/expected_summary.json` | Independent derivative check above |

Paths in the results table are relative to `reproductions/revision_v2/`, except
the Louisville derivative. The current five-figure manuscript maps to these
stable source filenames; the numeric filename prefixes are retained for
reproducibility:

| Manuscript figure number | Figure content | Source filename / prefix | Verification or regeneration |
|---|---|---|---|
| Figure 1 | Selected deferred path | `docs/paper/figures/fig0_concept.png` | AI-assisted concept schematic; checked against the state-transition algorithm for singleton pending state, target NEW before claimant MERGE, arrival/deadline claim triggers, deadline NEW/MERGE alternatives, and immutable final outputs. |
| Figure 2 | Research design | `reproductions/revision_v2/figures/fig1_workflow` | `analysis.render_revision_figures --figures workflow` |
| Figure 3 | Main comparison | `reproductions/revision_v2/figures/fig2_main_comparison` | `analysis.render_revision_figures --figures main` |
| Figure 4 | Threshold risk–recall curves | `reproductions/revision_v2/figures/fig3_risk_recall` | `analysis.render_revision_figures --figures threshold` |
| Figure 5 | Pair-set changes | `reproductions/revision_v2/figures/fig4_mechanism` | `analysis.render_revision_figures --figures mechanism` |

Paths in the figure mapping are relative to the repository root. Figures 2–5
have PDF, SVG, PNG and Source Data CSV exports under their listed prefixes.
Figure 1 is illustrative, not a data plot; its [exact generation prompts](docs/paper/figures/fig0_concept.prompt.txt)
and [provenance and internal semantic review](docs/paper/figures/fig0_concept.provenance.json)
accompany the image, and its manuscript caption discloses the AI assistance.
The published `v0.2.0` and `v0.2.1` tags remain fixed; this mapping documents the
current manuscript numbering without renaming the source files.

Generate all four vector/raster figures from released inputs with:

```text
python -m pip install -r requirements-figures.txt
python -m analysis.render_revision_figures --run-dir reproductions/revision_v2 --out build/revision_v2_figures
```

The plotting script enforces complete numerical inputs. External Nature figure
audit helpers are optional via `--qa-tools`; they are not bundled dependencies.
Without those helpers, geometry QA is explicitly marked unavailable. Numerical
regeneration and source-data export still run. Font fallback and PDF metadata can
change file hashes across platforms; compare source values and geometry rather
than requiring byte-identical figure output.

## Post-deadline audit and reporting supplement

The [v0.2.2 supplement](reproductions/round2/README.md) adds a complete-May
suffix-perturbation check at a cutoff after 138 actual seven-day deadline
re-scorings. It records full-prefix comparison, arrival/claim/deadline counts,
equal-time scheduling checks and the frozen protocol. It also exposes the July
hidden-Gate contrasts and the existing-run resource table. The original 120
replay results and the weekly audit remain separate and unchanged.

The resource table is reproducible from public aggregates:

```text
python -m analysis.round2_resources --run-dir reproductions/revision_v2 --code-root . --out-dir build/round2_resources
```

The supplement README provides the cache/model prerequisites and freeze/run
commands for the new audit. The manuscript is [available here](docs/paper/README.md).

## Full reconstruction

Follow [REPRODUCE.md](docs/REPRODUCE.md): create a new directory outside this
repository, copy only the frozen protocol, verify the input snapshot, then run
`prepare`, `fit`, `calibrate`, `suite`, summary/mechanism, candidate analysis and
audits. The actual revision used Windows, Python 3.11.9, 32 logical CPU cores and
about 33.6 GB installed RAM, with six workers and one numerical-library thread
per worker. These are observed resources, not minimum requirements. No GPU,
remote model API or proprietary model is required.

Complete replays preserve private intermediates locally: cached records, fitted
models, complete finalization events and candidate JSONL traces. The public
package contains no fitted model checkpoint. A user therefore fits the recorded
five GBT seeds and one logistic seed before replay. Runtime fields in individual
result JSONs document concurrent instrumented execution; they are not isolated
throughput benchmarks.

## Scope and provenance

The frozen protocol predates the corrected outcomes. Band/gate/cutoff settings
were inherited from already viewed historical work; May is exploratory and July
is retrospective validation. Historical E26/Chicago/Splink outputs remain
separately labelled and are not reruns of this corrected engine. Missing
historical library versions or first-view timestamps cannot be recovered from
the archived JSONs.

The anonymous Louisville graph reproduces agreement, eligibility-conditional
lag rates and issue bootstrap summaries. It omits the original fields needed to
rebuild annotation and candidate eligibility. The release does not claim that
this derivative independently reproduces those omitted preprocessing steps.

`analysis.package_revision` copies only an explicit aggregate/figure allowlist
from a completed local run. `analysis.build_manifest` scans Git-visible files,
refuses common private intermediates and known credential/local-user-path
patterns, and rebuilds `MANIFEST.json`. Neither command publishes files. The
manifest excludes itself and checks exact bytes of all listed release files.
