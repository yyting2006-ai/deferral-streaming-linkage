# What Does Deferral Buy?

Research artifact for *What Does Deferral Buy? Measuring Provisional Identity in Streaming Linkage*. The package contains the replay implementation, recorded aggregate results, table reconstruction and a timestamp-aware emission-prefix comparator.

## Quick verification

Use Python 3.11. These checks do not require downloading municipal records:

```bash
python -m pip install -r requirements.txt
python analysis/make_tables.py
python analysis/check_results.py
python -m unittest discover -s audit -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python analysis/verify_manifest.py
```

`make_tables.py` rebuilds the LaTeX tables from the saved aggregate JSONs. `check_results.py` reports paired seed effects and checks the counts used in the mechanism analysis. The synthetic audit tests exercise additional/missing emissions, emission timestamps, ordering, targets and shared randomness. Their success is not a real-data certification of output-prefix safety.

## Optional data figures

Install the optional plotting dependency with python -m pip install -r requirements-figures.txt, then run python analysis/make_figures.py. The script reconstructs both data figures from 92 saved aggregate observations in analysis/figure_data, checks every value against its recorded numeric summary, and exports PDF/SVG/PNG/TIFF under build/figures. Build outputs are ignored by Git.

~~~bash
python -m pip install -r requirements-figures.txt
python analysis/make_figures.py
~~~

The generator uses only NumPy and Matplotlib; no external style-library code is bundled or required. Its local geometry check covers axes bounds and overlap. Text layout, font availability and complete visual quality still need visual inspection on a new platform. Arial is used when available, otherwise DejaVu Sans.

## Contents

| Directory | Contents |
|---|---|
| `psid/` | Record schema, loaders, candidate retrieval, pair scoring and metrics |
| `experiments/` | Replay, ablation and external-baseline entry points |
| `results/` | Recorded aggregate results; no record-level assignments or request text |
| `analysis/` | Table regeneration and aggregate consistency checks |
| `audit/` | Complete emission-prefix comparator and synthetic fault tests |
| `tests/` | Synthetic data-loader and gold/metric smoke tests |
| `docs/` | Input specifications, snapshot digests and replay instructions |
| `tables/` | Generated LaTeX tables |

## Replaying the primary experiment

The analysis/evidence directory contains the revised matched-seed statistics, merge-count denominators, set differences, selection completeness and findings.

Obtain the public San Diego input using [the data instructions](docs/DATA.md). `PSID_DATA_DIR` can override the default `data/` directory.

```bash
python fetch_data.py san_diego_2024
mkdir replays
python experiments/e26_decision_timing.py --window may --seed 1 --deltas 0,0.5 --deadlines 7 --fresh --out replays/may_seed1.json
```

This command fits the scorer on January–February and replays May. The delta-zero arm is arrival-time commitment; delta 0.5 is the score-band deferral policy. [REPRODUCE.md](docs/REPRODUCE.md) lists the remaining policy settings and comparisons. The downloadable portal is mutable: the fetcher checks the recorded snapshot digest and fails on a mismatch, rather than treating a different snapshot as an exact reproduction.

## Verification scope

The replay uses a destination-counter limit of 20 to screen eligible targets. Its counter is not exact post-merge membership, and the merge operation does not enforce a post-merge size ceiling. Some stored sweeps exceed 20 members. The Splink capped-component comparison separately enforces a true component cap of 20; these controls are not identical.

The Splink comparison table reports observed maximum cluster size after projection onto the workflow-labelled evaluation domain K. Its pipeline values are 19 in both arms; these are not full-stream cluster-size bounds. The native/capped Splink maxima use the same labelled-domain metric.

The historical candidate index combines exact-address and block channels conditioned on service with IDF-token scoring. The implementation and revised evidence notes define the measured retrieval procedure. Earlier conceptual illustrations showed five retrieval channels that are not implemented by this code.

The local preparation verified table reconstruction, aggregate consistency and synthetic tests. It did not rerun the complete city/seed grid or recreate the historical environment in a new virtual environment. The frozen per-seed effects measure scorer-fitting variation conditional on a city and window. Several selector settings were examined using May before their April sweep; the artifact preserves those exploratory results.

The measured implementation advances deadlines on arriving-day changes and an end-of-window flush. A nominal D=7 setting has a saved maximum latency of 14 days; it is not a hard seven-day delivery guarantee. In the seed-13 band log, 571 source records appear more than once, with 722 extra merge rows and up to six rows for one record. Logged merge operations therefore cannot be interpreted as independently certified, irreversible final commits. Complete timestamped finalization logs are absent.

The Louisville human-label analysis is represented by aggregate counts. The privacy-reviewed record rendering and adjudication materials are not in this package, so that analysis cannot be independently rerun from this package alone. The original pair-log audit is retained as historical code/results; the complete comparator requires timestamped emission logs that the historical replay did not retain. These limitations remain open for a complete EA&B reproducibility submission.

## License

The code is released under the existing project MIT license (`LICENSE`). Municipal source data remain subject to their portals' terms. This repository does not redistribute the raw municipal request records.
