# Louisville anonymous derived issue graph

The author authorized release of this minimal anonymous derivative on 27 October 2026. It contains 327 freshly randomized record identifiers, relabelled A/B provisional and final issue clusters, pending-adjudication flags, two coarse wave labels, and 1,940 precomputed eligible pairs with lag-bin labels. Of the 327 records, 292 have confirmed final clusters and 35 remain unadjudicable. There are 226 confirmed issues.

The release omits original identifiers, exact dates, service labels, addresses, narratives, annotator identities, and free-text notes. The original workbooks and source requests are not included. The resampling-unit order is a list of the random final-cluster labels; it fixes the association between pseudorandom bootstrap weights and issue units so the reported intervals can be reproduced exactly.

## Scope of reproduction

From the repository root, after installing the declared Python dependencies, run:

```text
python analysis/reproduce_louisville.py --verify --out build/louisville_reanalysis.json
```

The standalone script reads only this public file. It recomputes A/B pair counts, positive-pair Dice agreement, adjusted Rand index, the same-issue rates in four lag bins, wave-specific counts, and issue-resampling intervals. `expected_summary.json` records independently checked numerical targets. The default 4,000 resamples use seed 20261027.

Candidate eligibility was computed locally from matching nonempty service and a token Jaccard similarity of at least 0.10 between address-plus-narrative token sets. Those input fields are deliberately absent, so this graph reproduces the reported numerical analyses **conditional on the supplied candidate set**; it does not independently reproduce text cleaning, privacy screening or candidate selection. Binary pair labels are derived from the final partition. No unadjudicable row is treated as a singleton negative.

## Annotation and interpretation

Existing A/B instructions require independent clustering of the same continuing problem on the same object, with shared cluster labels across the two weeks. Shared service, address or month alone is insufficient. A completed problem followed by a new incident should form another cluster. Uncertain rows are marked for adjudication. A third returned workbook provides the final partition and explicit unadjudicable status. Local hash checks verified that the A/B inputs, adjudication workbook and derived gold files agree. The files themselves do not independently authenticate the annotators' identities or compliance with the independence instructions.

Across all 327 provisional rows, positive-pair Dice agreement is 0.3863 and adjusted Rand index is 0.3845. Raw pair agreement is 0.9946 because almost all pairs are negative; it is not an appropriate summary of strong reliability. Excluding rows marked pending by either annotator gives a selected subset of 258 records, with Dice 0.4873 and adjusted Rand index 0.4855.

Pairs share records and issues. Resampling therefore operates on final issues, retaining their records together: within-issue pairs receive the issue copy count, while between-issue pairs receive the product of the endpoint issue copy counts. The reported 2.5%/97.5% quantiles are dependence-sensitivity intervals. Dependence through services or waves may remain, and only two waves are represented. The data span at most 13 days and do not establish flatness, zero temporal effects or transfer to a 30-day window.

## Rights and use

The repository's code license applies to the reproduction script. This data README records the author's authorization to publish the minimal derivative; it does not grant rights in third-party source data, relicense the original city records, or authorize redistribution of omitted private annotation materials. No additional open-data license is asserted here. Users should determine any permission needed for uses beyond reviewing and reproducing the published numerical analysis.

The source-city data and returned annotation workbooks remain subject to their applicable terms and access restrictions. Nothing in this directory authorizes re-identification or linkage of the randomized nodes to individual requests.
