# HN Data Folder

This folder contains Hacker News output files used by Scout dashboard mocks.

- `hn_raw.jsonl`: story-level collection output (first line is `_meta`, remaining lines are story rows).
- `hn_entities.json`: aggregated entity rankings for cluster map + leaderboard.
- `hn_source_nodes.json`: source-web nodes used when drilling into a selected entity.
- `hn_state.json`: run metadata and counts from the last pipeline execution.

Each JSON/JSONL file includes a top-level `_meta.description` explaining what it collects.
