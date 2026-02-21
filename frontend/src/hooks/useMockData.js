import { useEffect, useRef, useState } from 'react'

import hnStateRaw from '@data/hn_data/hn_state.json'
import githubStateRaw from '@data/github_data/github_state.json'
import productHuntStateRaw from '@data/producthunt_data/producthunt_state.json'
import finalEntitiesTopRaw from '@data/final_entity/final_entities_top50.json'
import finalNodesRaw from '@data/final_entity/final_source_nodes.json'

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

function normalizeScoreRows(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return []
  const values = rows.map((row) => toNumber(row._rawScore, 0))
  const low = Math.min(...values, 0)
  const high = Math.max(...values, 1)
  return rows.map((row) => {
    const normalized = high <= low ? 100 : ((row._rawScore - low) / (high - low)) * 100
    return { ...row, _normalizedScore: normalized }
  })
}

function buildTrends(payload, leaderboardMode = 'global_prominence') {
  const entities = Array.isArray(payload?.entities) ? payload.entities : []
  const mode = leaderboardMode === 'niche_opportunity' ? 'niche_opportunity' : 'global_prominence'
  const scored = normalizeScoreRows(entities.map((item) => ({
    entity: item.entity,
    _rawScore: toNumber(item.global_prominence_score, toNumber(item.relevance_score, toNumber(item.trend_score, 0))),
    _rawNiche: toNumber(
      item.niche_opportunity_score,
      toNumber(item.velocity_delta_pct, 0) * 0.8 + toNumber(item.mention_count_1h, 0) * 2.5 + toNumber(item.confidence, 0) * 20,
    ),
    velocity_delta_pct: toNumber(item.velocity_delta_pct, 0),
    sentiment: item.sentiment ?? EMPTY_SENTIMENT,
    mention_count_1h: toNumber(item.mention_count_1h, 0),
    mention_count_24h: toNumber(item.mention_count_24h, 0),
    spike_detected: Boolean(item.spike_detected),
    sources: Array.isArray(item.sources) ? item.sources : ['hackernews'],
    top_keywords: Array.isArray(item.top_keywords) ? item.top_keywords : [],
    source_counts: item.source_counts ?? { hackernews: toNumber(item.stories, 1) },
  })))
  const nicheNorm = normalizeScoreRows(scored.map((row) => ({ ...row, _rawScore: row._rawNiche })))
  const withScores = scored.map((row, idx) => {
    const globalScore = toNumber(row._normalizedScore, 0)
    const nicheScore = toNumber(nicheNorm[idx]?._normalizedScore, 0)
    const trendScore = mode === 'niche_opportunity' ? nicheScore : globalScore
    return {
      ...row,
      trend_score: trendScore,
      global_prominence_score: globalScore,
      niche_opportunity_score: nicheScore,
      leaderboard_mode: mode,
    }
  })
  return withScores
    .sort((a, b) => toNumber(b.trend_score, 0) - toNumber(a.trend_score, 0))
    .map((row) => {
      const { _rawScore, _rawNiche, _normalizedScore, ...clean } = row
      return clean
    })
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

function dedupeSourceNodes(rows) {
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

export function useMockData({ paused = false, leaderboardMode = 'global_prominence' } = {}) {
  const [trends, setTrends] = useState([])
  const [sources, setSources] = useState([])
  const [sourceNodes, setSourceNodes] = useState([])
  const [loading, setLoading] = useState(true)
  const pausedRef = useRef(paused)

  useEffect(() => {
    pausedRef.current = paused
  }, [paused])

  useEffect(() => {
    const parsedTrends = buildTrends(finalEntitiesTopRaw, leaderboardMode).slice(0, 50)
    const topEntityKeys = new Set(parsedTrends.map((trend) => normalizeEntityKey(trend.entity)))
    const parsedNodes = dedupeSourceNodes(
      buildSourceNodes(finalNodesRaw).filter((node) => topEntityKeys.has(normalizeEntityKey(node.entity))),
    )

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
  }, [leaderboardMode])

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
