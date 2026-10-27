# Inputs, snapshot verification and released derivatives

The author authorized release of the two existing public San Diego and Chicago input snapshots as versioned GitHub Release assets. They are separate downloads, not CSV files committed to Git. Original municipal source terms still apply. Private Louisville source renderings and annotation workbooks, fitted models, and record-level replay logs are not released. The Louisville derivative below uses randomized ids and omits source-identifying fields. Archive/member sizes and hashes are recorded in [input_snapshots.json](input_snapshots.json).

Release download verification completed on 27 October 2026. Both `v0.2.0` ZIP assets were downloaded anonymously and matched their recorded sizes and SHA-256 hashes. The released `fetch_data.py` was then run from a new public clone to download and extract both archives; the resulting San Diego and Chicago CSV sizes and hashes also matched. [release_verification.json](release_verification.json) records these checks and the public-clone numerical verification. The immutable `v0.2.0` tag remains at `ff21cf4d7e7158b1a731b1dfb166c72bf2a7382d`; this later verification record does not change its scientific code or evidence.

## San Diego: corrected primary experiment

- Preferred snapshot: [San Diego archived CSV ZIP](https://github.com/yyting2006-ai/deferral-streaming-linkage/releases/download/v0.2.0/get_it_done_requests_closed_2024_datasd.zip).
- Source: [City of San Diego Get It Done](https://data.sandiego.gov/datasets/get-it-done-311/).
- Direct file: [closed 2024 export](https://seshat.datasd.org/get_it_done_requests_closed/get_it_done_requests_closed_2024_datasd.csv).
- Relative input path: `SanDiegoGetItDone/closed2024/get_it_done_requests_closed_2024_datasd.csv`.
- Archived size: 135,976,733 bytes.
- SHA-256: `19ee8a3bee5ecf9c8e60a6110fdfd86848259d94d5869b3a8f136740f6a3d69f`.
- Fields: `service_request_id`, `service_request_parent_id`, `date_requested`, `service_name`, `service_name_detail`, `street_address`, `zipcode`, `public_description`, `lat`, `lng`.

Using the default ignored `data/` directory:

```text
python fetch_data.py san_diego_2024 --source release
python fetch_data.py san_diego_2024 --verify-only
```

The default source is the versioned Release ZIP. The fetcher verifies its recorded ZIP hash and size, requires exactly the expected CSV member, rejects traversal/additional/special members, and verifies the extracted CSV hash and size before moving it to the input path. It does not overwrite an existing CSV.

For the original source fallback, run `python fetch_data.py san_diego_2024 --source portal`. On 27 October 2026 the direct portal's HTTP HEAD request returned 403. If portal access fails, use a manual export and verify it locally. Changed portal content or ordering can fail the archived digest; preserve a mismatch separately and do not call it an exact snapshot reproduction. The Release path preserves the archived bytes independently of portal mutability, subject to actual asset availability confirmed above.

Corrected E37 fits on the first 22,000 sorted January--February requests, calibrates in March, reports April sensitivity, and evaluates fixed policies on May and July. Month membership uses original local calendar dates. Naive timestamps are interpreted as `America/Los_Angeles` and converted to UTC; ordering and deadlines use UTC. Historical E26 also has a June replication, which retains its historical implementation label.

Parent links form undirected components over the retrospective annual export. These are workflow labels, not verified physical-incident identity. Evaluation labels only records participating in resolved links; cross-component negatives rely on a closed-world workflow assumption. Unlinked records remain unknown in evaluation. Training separately treats remaining sampled candidates, including unknown pairs, as weak negatives. The export does not establish when each link became available, so request-date separation does not prove that labels were known at a historical deployment cutoff.

## Chicago: retained transfer evidence

- Preferred snapshot: [Chicago archived six-field CSV ZIP](https://github.com/yyting2006-ai/deferral-streaming-linkage/releases/download/v0.2.0/chicago311_2026q1_six_fields.zip).
- Source: [Chicago 311 Service Requests](https://data.cityofchicago.org/Service-Requests/311-Service-Requests/v6vf-nfxy).
- Relative path: `Chicago311/2026Q1/chicago311_2026q1_six_fields.csv`.
- Filter: `created_date >= 2026-01-01T00:00:00` and `< 2026-04-01T00:00:00`.
- Columns: `sr_number,parent_sr_number,created_date,sr_type,street_address,duplicate`.
- Archived size: 47,943,071 bytes.
- SHA-256: `9549aa995038885e5277e8aacb96dc5497d0c2263fcd2cec3f05b3feaa8a2ae0`.

```text
python fetch_data.py chicago_2026q1 --source release
python fetch_data.py chicago_2026q1 --verify-only
```

The default Release ZIP supplies the archived bytes with the same strict archive/member verification as San Diego. `--source portal` instead requests the current ordered six-column export, whose content or ordering may differ; a mismatch is not exact reproduction. Historical transfer uses January--February training and March 1--7 / March 8--14 checks. The extract lacks free text. Chicago is not silently included in corrected San Diego E37; a new Chicago replay needs an explicitly specified protocol.

## Louisville: anonymous numerical-reanalysis derivative

City source: [Louisville Metro 311 Service Requests 2024](https://catalog.data.gov/dataset/louisville-metro-ky-metro-311-service-requests-2024). The study also used A/B clustering and an adjudication workbook for two June waves. Original requests and private workbooks are not bundled.

The author authorized the minimal derivative in [`derived_data/louisville/`](../derived_data/louisville/). It contains 327 random node ids, relabelled A/B clusters, pending flags, coarse waves, final labels or unknown status, and 1,940 precomputed eligible pair edges with lag bins. There are 292 confirmed final records and 35 unadjudicable records. Original identifiers, dates, service labels, addresses, narratives and annotator identities are omitted.

```text
python -m analysis.reproduce_louisville --verify --out build/louisville_reanalysis.json
```

The script independently recomputes agreement counts, positive-pair Dice, adjusted Rand index, four lag-bin rates, wave breakdown and issue-resampling intervals without downloads/private inputs. Eligible edges were selected locally from service agreement and address/narrative token overlap. Those omitted fields prevent independent reconstruction of candidate selection. This is numerical reanalysis conditional on the graph, not a claim that all annotation/preprocessing steps are independently reproducible. Unknown final labels are not singleton negatives. See the [annotation, limitations and rights statement](../derived_data/louisville/README.md).

## Local paths and private outputs

The loader defaults to repository `data/`. Override it consistently:

```powershell
$env:PSID_DATA_DIR = 'D:/municipal-inputs'
python fetch_data.py san_diego_2024 --data-dir 'D:/municipal-inputs' --verify-only
```

```bash
export PSID_DATA_DIR=/absolute/path/to/municipal-inputs
python fetch_data.py san_diego_2024 --data-dir "$PSID_DATA_DIR" --verify-only
```

The selected directory must contain the listed relative paths. The fetcher uses its own `--data-dir`, so supply it explicitly when overriding the loader. Keep new E37 runs outside the repository. Their `private/` folders contain record identities, complete candidate membership and models; do not release them. Frozen protocols, reviewed aggregate JSON/CSV and manifests belong in `reproductions/revision_v2/`.
