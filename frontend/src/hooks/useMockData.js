import { useEffect, useRef, useState } from 'react'

import hnEntitiesRaw from '@data/hn_data/hn_entities.json'
import hnNodesRaw from '@data/hn_data/hn_source_nodes.json'
import hnStateRaw from '@data/hn_data/hn_state.json'

const EMPTY_SENTIMENT = { positive: 0.6, neutral: 0.3, negative: 0.1 }

function toNumber(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function buildTrends(payload) {
  const entities = Array.isArray(payload?.entities) ? payload.entities : []
  return entities.map((item) => ({
    entity: item.entity,
    trend_score: toNumber(item.trend_score, 0),
    velocity_delta_pct: toNumber(item.velocity_delta_pct, 0),
    sentiment: item.sentiment ?? EMPTY_SENTIMENT,
    mention_count_1h: toNumber(item.mention_count_1h, 0),
    mention_count_24h: toNumber(item.mention_count_24h, 0),
    spike_detected: Boolean(item.spike_detected),
    sources: Array.isArray(item.sources) ? item.sources : ['hackernews'],
    top_keywords: Array.isArray(item.top_keywords) ? item.top_keywords : [],
    source_counts: item.source_counts ?? { hackernews: toNumber(item.stories, 1) },
  }))
}

function buildSourceNodes(payload) {
  const rows = Array.isArray(payload?.source_nodes) ? payload.source_nodes : []
  return rows.map((row) => ({
    id: row.id,
    entity: row.entity,
    source_id: row.source_id ?? 'hackernews',
    source_name: row.source_name ?? 'Hacker News',
    headline: row.headline ?? 'Untitled source',
    url: row.url ?? '',
    summary: row.summary ?? 'No summary available.',
    interactions: toNumber(row.interactions, 0),
    views: toNumber(row.views, 0),
  }))
}

function buildSources(state, nowIso) {
  return [
    {
      id: 'hackernews',
      label: 'HN',
      status: 'live',
      items_ingested: toNumber(state?.processed_stories, 0),
      last_scraped: state?.last_run_finished_at ?? nowIso,
      error_message: undefined,
    },
    {
      id: 'reddit',
      label: 'Reddit',
      status: 'cached',
      items_ingested: 0,
      last_scraped: nowIso,
      error_message: undefined,
    },
    {
      id: 'techcrunch',
      label: 'RSS',
      status: 'cached',
      items_ingested: 0,
      last_scraped: nowIso,
      error_message: undefined,
    },
    {
      id: 'twitter',
      label: 'Twitter',
      status: 'cached',
      items_ingested: 0,
      last_scraped: nowIso,
      error_message: undefined,
    },
    {
      id: 'producthunt',
      label: 'PH',
      status: 'cached',
      items_ingested: 0,
      last_scraped: nowIso,
      error_message: undefined,
    },
  ]
}

export function useMockData({ paused = false } = {}) {
  const [trends, setTrends] = useState([])
  const [sources, setSources] = useState([])
  const [sourceNodes, setSourceNodes] = useState([])
  const [loading, setLoading] = useState(true)
  const pausedRef = useRef(paused)

  useEffect(() => {
    pausedRef.current = paused
  }, [paused])

  useEffect(() => {
    const parsedTrends = buildTrends(hnEntitiesRaw)
    const parsedNodes = buildSourceNodes(hnNodesRaw)
    const nowIso = new Date().toISOString()

    setTrends(parsedTrends)
    setSourceNodes(parsedNodes)
    setSources(buildSources(hnStateRaw, nowIso))
    setLoading(false)

    const interval = setInterval(() => {
      if (pausedRef.current) return
      setSources(buildSources(hnStateRaw, new Date().toISOString()))
    }, 15000)

    return () => clearInterval(interval)
  }, [])

  return {
    trends,
    velocityData: [],
    velocityEntities: [],
    heatmapData: [],
    alerts: [],
    sources,
    fundingData: [],
    sourceNodes,
    loading,
    connected: !loading,
  }
}
