# Product Hunt Data Folder

This folder contains Product Hunt source outputs for Scout.

- `producthunt_raw.jsonl`: raw Product Hunt post rows with engagement metrics and entity candidates.
- `producthunt_entities.json`: entity-level aggregation for rankings and source merge.
- `producthunt_source_nodes.json`: source-web nodes for Product Hunt drill-down.
- `producthunt_state.json`: pipeline run metadata and output counts.

Each JSON/JSONL output file includes a top-level `_meta.description`.
