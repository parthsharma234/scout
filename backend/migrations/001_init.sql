PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  fetched_count INTEGER NOT NULL DEFAULT 0,
  written_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  published_at TEXT,
  fetched_at TEXT,
  title TEXT,
  url TEXT,
  raw_json TEXT NOT NULL,
  run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source, external_id),
  FOREIGN KEY(run_id) REFERENCES ingestion_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entity_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  first_seen_at TEXT,
  last_seen_at TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  trend_score REAL NOT NULL DEFAULT 0,
  mention_count_1h INTEGER NOT NULL DEFAULT 0,
  mention_count_24h INTEGER NOT NULL DEFAULT 0,
  activity_last_30d INTEGER NOT NULL DEFAULT 0,
  sources_json TEXT NOT NULL DEFAULT '[]',
  source_counts_json TEXT NOT NULL DEFAULT '{}',
  top_keywords_json TEXT NOT NULL DEFAULT '[]',
  quality_signals_json TEXT NOT NULL DEFAULT '[]',
  node_count INTEGER NOT NULL DEFAULT 0,
  last_enriched_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alias_key TEXT NOT NULL UNIQUE,
  alias_name TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  reason TEXT NOT NULL DEFAULT 'exact',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(entity_id) REFERENCES entity_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entity_mentions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  mention_key TEXT NOT NULL,
  entity_name TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  published_at TEXT,
  item_external_id TEXT,
  url TEXT,
  title TEXT,
  summary TEXT,
  keywords_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL,
  run_id INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES ingestion_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entity_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id TEXT NOT NULL UNIQUE,
  entity_id INTEGER,
  alias_key TEXT,
  entity_name TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_name TEXT,
  headline TEXT,
  url TEXT,
  summary TEXT,
  interactions INTEGER NOT NULL DEFAULT 0,
  views INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  published_at TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  node_type TEXT NOT NULL DEFAULT 'source_raw',
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(entity_id) REFERENCES entity_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entity_daily_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  mention_count INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  trend_score REAL NOT NULL DEFAULT 0,
  source_counts_json TEXT NOT NULL DEFAULT '{}',
  activity_score REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_id, date),
  FOREIGN KEY(entity_id) REFERENCES entity_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS enrichment_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  query TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  links_found INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(entity_id) REFERENCES entity_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS enrichment_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  snippet TEXT,
  provider TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  watchouts_json TEXT NOT NULL DEFAULT '[]',
  published_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_id, url),
  FOREIGN KEY(entity_id) REFERENCES entity_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipeline_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_source_key
  ON entity_mentions(source, mention_key);
CREATE INDEX IF NOT EXISTS idx_entity_nodes_entity_id
  ON entity_nodes(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_nodes_alias_key
  ON entity_nodes(alias_key);
CREATE INDEX IF NOT EXISTS idx_entity_daily_metrics_date
  ON entity_daily_metrics(date);
