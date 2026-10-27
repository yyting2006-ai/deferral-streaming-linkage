# Input data and snapshot verification

Raw request text, street addresses and record-level assignments are obtained locally from their source portals. They are not included in this repository. The original input digests are recorded in `input_snapshots.json`.

## San Diego

- Source: [City of San Diego Get It Done](https://data.sandiego.gov/datasets/get-it-done-311/).
- Direct yearly file: <https://seshat.datasd.org/get_it_done_requests_closed/get_it_done_requests_closed_2024_datasd.csv>.
- Local path: `data/SanDiegoGetItDone/closed2024/get_it_done_requests_closed_2024_datasd.csv`.
- Historical snapshot: 135976733 bytes; SHA-256 `19ee8a3bee5ecf9c8e60a6110fdfd86848259d94d5869b3a8f136740f6a3d69f`.
- Fields read: `service_request_id`, `service_request_parent_id`, `date_requested`, `service_name`, `service_name_detail`, `street_address`, `zipcode`, `public_description`, `lat`, `lng`.

During package preparation on 2026-10-27, an HTTP HEAD request to the direct file returned 403. The package does not claim a freshly verified public download. If automated access fails, use the portal export and then `python fetch_data.py san_diego_2024 --verify-only`. A current export may differ from the historical digest.

Fit the scorer on January–February 2024; March calibrates the merge threshold; April is the selector sweep; May and June provide the reported evaluation windows. The loader creates an undirected component closure from resolved parent links. Unlinked records are outside the certified evaluation domain; they are not automatically negative singleton labels.

## Chicago

- Source: [Chicago 311 Service Requests](https://data.cityofchicago.org/Service-Requests/311-Service-Requests/v6vf-nfxy).
- Local path: `data/Chicago311/2026Q1/chicago311_2026q1_six_fields.csv`.
- Extract `created_date >= 2026-01-01T00:00:00` and `< 2026-04-01T00:00:00`, selecting `sr_number,parent_sr_number,created_date,sr_type,street_address,duplicate`.
- Historical six-column file: 47943071 bytes; SHA-256 `9549aa995038885e5277e8aacb96dc5497d0c2263fcd2cec3f05b3feaa8a2ae0`.

The portal may change its data or CSV ordering. The fetcher offers a current ordered six-column extract, then checks the historical hash; it does not assert byte-identical reproduction if that check fails. January–February train the scorer; March 1–7 and March 8–14 are the transfer-check windows. The selected extract has no free-text field.

## Louisville

Source: [Louisville Metro 311 Service Requests 2024](https://catalog.data.gov/dataset/louisville-metro-ky-metro-311-service-requests-2024). The study additionally uses a privacy-reviewed rendering and human adjudications for June 3–9 and June 10–16. Downloading the city source alone cannot reconstruct those annotations. The included `results/e35b_louisville.json` contains aggregate records, cluster and lag-bucket counts; no individual labels, request identifiers, addresses or narratives are redistributed here.

## Local directory override

PowerShell: `$env:PSID_DATA_DIR='D:/your-local-inputs'`.

POSIX shell: `export PSID_DATA_DIR=/your/local/inputs`.

The chosen directory must contain the San Diego and Chicago relative paths shown above. All downloaded data stay under the ignored `data/` directory by default.
