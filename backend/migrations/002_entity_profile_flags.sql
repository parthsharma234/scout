ALTER TABLE entity_profiles
ADD COLUMN is_known_incumbent INTEGER NOT NULL DEFAULT 0;

ALTER TABLE entity_profiles
ADD COLUMN momentum_score REAL NOT NULL DEFAULT 0;
