# Empirical figure readability revision — v0.2.6

Figures 3–5 are rebuilt from the existing frozen outputs. They use 9.5–11 pt type
at 180 mm figure width, editable vector text, direct annotations and a restrained
palette. The paper includes them at its full two-column text width.

| Figure | New visual form | Data and interpretation |
|---|---|---|
| 3 | Horizontal matched profiles plus paired-effect plots | All 40 raw F1 observations; mean ± sample SD above; all 20 same-seed Band/Gate − Cutoff differences and mean with pointwise 95% t intervals below. Intervals describe fit-seed variability, not population uncertainty. |
| 4 | Full risk–recall paths above matched decision-region enlargements | All 39 original threshold rows retained; 38 finite observations drawn in full views. March logistic τ = .99 has undefined risk. Connections follow threshold order; zooms add no fitted points or smoothing. |
| 5 | Horizontal diverging count comparisons | All 191 mechanism source rows retained; loss/introduction to the left and recovery/avoidance to the right. Tiny last-bin wrong-operation counts and true zeros remain visible or explicitly labeled. |

All numerical source CSVs match the previous figure exports. Four displayed
paired intervals are checked against the frozen contrast table. No scientific
experiment, model, policy, label or threshold was changed.

The three PDFs pass a 9.5 pt text-floor check, rendered collision checks and panel
alignment checks. A separate internal reviewer checked the data, interval units,
zoom scope and small counts. This is internal quality assurance, not external
peer review. The two original ImageGen schematics are retained unchanged.

Recreate the current plots with:

```
python -m analysis.render_readable_figures --run-dir reproductions/revision_v2 --out build/readable_figures
```

The optional `--qa-tools` flag accepts the nature-figure scripts directory for
rendered alignment, text and collision audits. External QA tooling is not
bundled; omitted-tool status is explicit. PDFs/SVGs and source CSVs are under
`reproductions/revision_v2/figures`, with paired differences in the additional
`fig2_main_comparison.paired_differences.csv` file. The Release also provides a
self-contained plotting ZIP with all required public aggregate inputs.
