# GitHub Data Folder

This folder contains GitHub source outputs for Scout.

- `github_raw.jsonl`: filtered/enriched repository records from GitHub scrape input.
- `github_entities.json`: entity-level aggregation for rankings and source merge.
- `github_source_nodes.json`: node records for per-entity source web drill-down.
- `github_state.json`: transform run metadata and output counts.

Each JSON/JSONL output file includes a top-level `_meta.description`.
