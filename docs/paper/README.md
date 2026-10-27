# Manuscript and concept illustration

[Paper PDF](main.pdf) · [Figure 1 PNG](figures/fig0_concept.png) ·
[LaTeX source ZIP](https://github.com/yyting2006-ai/deferral-streaming-linkage/releases/download/v0.2.1/pvldb_revision_source.zip)

The manuscript has 12 pages and five figures. Figure 1 on page 2 depicts the
selected deferred path: a request remains retrievable while pending, followed
by an immutable final decision. The caption identifies the AI-assisted schematic;
[exact prompts](figures/fig0_concept.prompt.txt) and [provenance](figures/fig0_concept.provenance.json)
accompany the image.

![Selected deferred path](figures/fig0_concept.png)

The public source ZIP contains the manuscript, bibliography, official template
files, all five figures, tables, prompts and a checksum manifest. An isolated
build produced 12 pages with identical page text, zero overfull boxes and zero
undefined references. See [verification](source_package_verification.json) and
[download checksums](SHA256SUMS.txt).

This documentation release preserves the code and empirical results of
[v0.2.0](https://github.com/yyting2006-ai/deferral-streaming-linkage/releases/tag/v0.2.0).
The municipal input snapshots remain attached to that release.
