import { useEffect, useRef, useState } from 'react'

import hnEntitiesRaw from '@data/hn_data/hn_entities.json'
import hnNodesRaw from '@data/hn_data/hn_source_nodes.json'
import hnStateRaw from '@data/hn_data/hn_state.json'
import githubEntitiesRaw from '@data/github_data/github_entities.json'
import githubNodesRaw from '@data/github_data/github_source_nodes.json'
import githubStateRaw from '@data/github_data/github_state.json'
import productHuntEntitiesRaw from '@data/producthunt_data/producthunt_entities.json'
import productHuntNodesRaw from '@data/producthunt_data/producthunt_source_nodes.json'
import productHuntStateRaw from '@data/producthunt_data/producthunt_state.json'

const EMPTY_SENTIMENT = { positive: 0.6, neutral: 0.3, negative: 0.1 }

function toNumber(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function normalizeEntityKey(value = '') {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
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

function mergeTrends(...collections) {
  const map = new Map()

  collections.flat().forEach((trend) => {
    const key = normalizeEntityKey(trend.entity)
    if (!key) return

    const existing = map.get(key)
    if (!existing) {
      map.set(key, {
        ...trend,
        sources: [...new Set(trend.sources ?? [])],
        source_counts: { ...(trend.source_counts ?? {}) },
        top_keywords: [...new Set(trend.top_keywords ?? [])].slice(0, 8),
      })
      return
    }

    existing.trend_score += toNumber(trend.trend_score)
    existing.mention_count_1h += toNumber(trend.mention_count_1h)
    existing.mention_count_24h += toNumber(trend.mention_count_24h)
    existing.spike_detected = existing.spike_detected || Boolean(trend.spike_detected)
    existing.velocity_delta_pct = Number(((existing.velocity_delta_pct + toNumber(trend.velocity_delta_pct)) / 2).toFixed(2))
    existing.sources = [...new Set([...(existing.sources ?? []), ...(trend.sources ?? [])])]
    existing.top_keywords = [...new Set([...(existing.top_keywords ?? []), ...(trend.top_keywords ?? [])])].slice(0, 8)

    const mergedSourceCounts = { ...(existing.source_counts ?? {}) }
    Object.entries(trend.source_counts ?? {}).forEach(([sourceId, count]) => {
      mergedSourceCounts[sourceId] = toNumber(mergedSourceCounts[sourceId]) + toNumber(count)
    })
    existing.source_counts = mergedSourceCounts
  })

  const merged = Array.from(map.values())
  const maxScore = Math.max(...merged.map((trend) => toNumber(trend.trend_score, 0)), 1)

  return merged
    .map((trend) => ({
      ...trend,
      trend_score: Number(((toNumber(trend.trend_score, 0) / maxScore) * 100).toFixed(2)),
    }))
    .sort((a, b) => b.trend_score - a.trend_score)
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

function mergeSourceNodes(...collections) {
  const rows = collections.flat()
  const seen = new Set()
  return rows.filter((row) => {
    const key = row.id || `${row.source_id}-${normalizeEntityKey(row.entity)}-${row.url}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function buildSources({ hnState, githubState, productHuntState }, nowIso) {
  return [
    {
      id: 'hackernews',
      label: 'HN',
      status: 'live',
      items_ingested: toNumber(hnState?.processed_stories, 0),
      last_scraped: hnState?.last_run_finished_at ?? nowIso,
      error_message: undefined,
    },
    {
      id: 'github',
      label: 'GitHub',
      status: 'live',
      items_ingested: toNumber(githubState?.repos_written, 0),
      last_scraped: githubState?.last_run_finished_at ?? nowIso,
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
      status: toNumber(productHuntState?.posts_written, 0) > 0 ? 'live' : 'cached',
      items_ingested: toNumber(productHuntState?.posts_written, 0),
      last_scraped: productHuntState?.last_run_finished_at || nowIso,
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
    const parsedHnTrends = buildTrends(hnEntitiesRaw)
    const parsedGithubTrends = buildTrends(githubEntitiesRaw)
    const parsedProductHuntTrends = buildTrends(productHuntEntitiesRaw)
    const parsedTrends = mergeTrends(parsedHnTrends, parsedGithubTrends, parsedProductHuntTrends)

    const parsedHnNodes = buildSourceNodes(hnNodesRaw)
    const parsedGithubNodes = buildSourceNodes(githubNodesRaw)
    const parsedProductHuntNodes = buildSourceNodes(productHuntNodesRaw)
    const parsedNodes = mergeSourceNodes(parsedHnNodes, parsedGithubNodes, parsedProductHuntNodes)

    const nowIso = new Date().toISOString()

    setTrends(parsedTrends)
    setSourceNodes(parsedNodes)
    setSources(buildSources({
      hnState: hnStateRaw,
      githubState: githubStateRaw,
      productHuntState: productHuntStateRaw,
    }, nowIso))
    setLoading(false)

    const interval = setInterval(() => {
      if (pausedRef.current) return
      setSources(buildSources({
        hnState: hnStateRaw,
        githubState: githubStateRaw,
        productHuntState: productHuntStateRaw,
      }, new Date().toISOString()))
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
