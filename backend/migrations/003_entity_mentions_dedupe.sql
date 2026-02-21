DELETE FROM entity_mentions
WHERE id NOT IN (
  SELECT MIN(id)
  FROM entity_mentions
  GROUP BY source, mention_key, COALESCE(item_external_id, ''), COALESCE(url, ''), COALESCE(published_at, '')
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_mentions_unique_event
ON entity_mentions(
  source,
  mention_key,
  COALESCE(item_external_id, ''),
  COALESCE(url, ''),
  COALESCE(published_at, '')
);
