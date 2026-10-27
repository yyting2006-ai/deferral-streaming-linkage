# Academic prose revision — v0.2.7

This revision improves the English throughout the existing PVLDB manuscript.
It uses [academic-humanizer 0.3.3](https://github.com/AIScientists-Dev/academic-humanizer/tree/94b88b23703bed7df507acae7d6d5876209a0cdf),
with direct, neutral technical prose as the style target.

Eight manuscript source sections were revised: opening, related work, protocol,
experimental setup, results, audit/transfer, discussion/artifact, and conclusion.
The revision shortens clause chains, gives statements concrete subjects,
clarifies compressed terminology, and makes logical conditions explicit.
It retains the original section and paragraph structure.

Examples include:

- "We measure this design with an event-time engine" becomes "We evaluate this
  choice using an event-time engine".
- "No path carries previously committed members into a different issue" becomes
  "Previously committed members never move to a different issue".
- "that choice is persisted" becomes "that choice is recorded".
- The discussion explicitly names "the set of merge decisions underlying
  decision FMP" to distinguish statistical support from evidentiary support.
- The calibration fallback says "In that case, it also reports infeasibility",
  so splitting the original sentence preserves its conditional scope.

Numerical values, mathematical spans, citation keys, cross-references, formal
definitions, proofs, algorithms, complete figure/table environments and captions
are unchanged. The bibliography still contains 37 cited works. All figure,
table, data and official template files match v0.2.6 byte for byte. The plotting
package is the same archive released with v0.2.6.

An independent fresh-context internal review compared all changed prose for
semantic drift and evidence strength. Its one conditional-scope correction and
one optional terminology clarification were applied and rechecked. This is
internal quality assurance. Existing uncertainty, workflow-label, causality,
generalization and AI-illustration disclosures remain in the manuscript.

The accompanying `language_changes.diff` records the complete source-text
revision. The portable LaTeX archive contains the revised source and the
unchanged publication assets.
