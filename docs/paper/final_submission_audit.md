# Final submission audit — v0.2.9

The manuscript was checked against all 20 numbered topics across the two
provided review rounds: eight major and seven minor topics in round one,
and five minor topics in round two. Each topic's subrequirements were checked
against the paper, implementation and recorded results. Three fresh-context
internal audits covered the major topics, minor topics and all 37 citations.

## Corrections in this release

- May hidden Band/Gate mean pending occupancy is now reported as 1,145.1 and
  475.1. The source values are 1145.148503980236 and 475.14965127134934.
- The historical Chicago maximum gain of 0.0038 is explicitly scoped to the
  threshold-0.025 development sweep. It is not the maximum over every historical
  exploratory configuration.
- The additional statement about 326 review judgments was removed because the
  released anonymous derivative does not contain those judgment edges. The
  reported agreement, final clusters and lag analysis remain reproducible from
  the released graph.
- Reproduction documentation distinguishes legacy zero-denominator sentinels
  in one original result JSON from the canonical count-based normalized ratios.
  Generator documentation and new-result descriptive metadata now describe this
  distinction accurately. Replay, fitting and metric algorithms are unchanged.
- The first two authors' affiliation addresses were corrected to Lingshui,
  Hainan, China following author confirmation.

The 120 original replay results retain their recorded checksums. No new
experiment was run and the main comparisons, tables and figures are unchanged.

## Evidence checked

Independent arithmetic from 120 replay records verifies partition metrics,
decision denominators, action/event totals, capacity and delay bounds. The
five-seed main contrasts and the July hidden-Gate contrasts reproduce their
reported intervals. Positive-pair set changes and wrong-operation counts agree
with the underlying records. The Louisville derivative reproduces 46 recorded
analysis targets. Local saved event logs verify the 138 pre-cutoff deadline
events, complete output-prefix equality, timer ordering and the corrected
occupancy rounding.

The final validation ran 69 tests from `tests/` and nine complete-prefix
comparator tests from `audit/`, all successfully. The 37 cited works were
checked against original publications or authoritative sources; none required
replacement. This audit did not retrain models or repeat the complete 120-run
raw-stream experiment, and it does not certify annotator conduct.

## Review-topic closure map

| Topic | Current paper/evidence home |
| --- | --- |
| R1 M01: commitment and causal prefix | Sections 3–4 and 7; Algorithms 1–2; prefix comparator tests |
| R1 M02: selection chronology | Section 5; frozen protocol and stage/run manifests |
| R1 M03: waiting and visibility | Sections 6.1, 6.3, 6.5 and 9; set and trigger analyses |
| R1 M04: labels and denominators | Section 3; Table 3; shared-support analyses |
| R1 M05: threshold alternatives and scorer | Sections 5–6; Figure 4; Table 2 |
| R1 M06: candidate scope and external systems | Section 6.4; Table 4; Discussion |
| R1 M07: delayed-matching literature | Related work, online matching with delays |
| R1 M08: executable/reproducible protocol | Algorithm 1; Tables 5–6; artifact section; encoding clarification |
| R1 N01: numerical units | Abstract and Section 6.1; paired-effect Figure 3 |
| R1 N02: seed sets | Table 1 and Section 5; per-run records |
| R1 N03: timing and pending counts | Section 6.5; Tables 5–6; corrected rounding |
| R1 N04: transfer and annotation | Section 8; Table 7; issue-resampling code |
| R1 N05: grammar and rhetoric | Revised full text |
| R1 N06: headers, references, metadata | Figures 3 and 5; official template boilerplate |
| R1 N07: figure readability | Full-width schematics and vector data figures |
| R2 N01: post-deadline prefix check | Section 7; round-two saved audit |
| R2 N02: July hidden Gate | Section 6.1; paired contrasts |
| R2 N03: resource summary | Table 6 and Section 6.5 |
| R2 N04: workflow terminology and bound units | Figure 4; Table 4 |
| R2 N05: figure mapping | `ARTIFACT.md` manuscript/source mapping |

## Submission-format check

The initial EA&B manuscript uses the unmodified official Volume 20 class,
style and bibliography files at commit
`39c95f5c6fcbe652a83be24e4eff8f2134cd3fbc`. It has 13 PDF pages; all body content
ends on page 12 and only references continue to page 13. Authors, the category
suffix and required first-page blocks are present. All font programs are
embedded. Compilation and visual inspection find no overfull boxes, undefined
references, clipped material or collisions. The portable source ZIP is checked
by an isolated compilation.

Official rules checked on 2026-10-27:
[submission guidelines](https://vldb.org/2027/submission-guidelines.html),
[formatting guidelines](https://vldb.org/2027/formatting-guidelines.html),
[template](https://github.com/vldbproceedings/VLDB-Template), and
[reproducibility](https://vldb.org/pvldb/reproducibility).

CMT registration, conflicts, submission counts, reviewer nomination,
originality/related-submission declarations and final author approval remain
author actions. No CMT submission or venue certification is claimed.
