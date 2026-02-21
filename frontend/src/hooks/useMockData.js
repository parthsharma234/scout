import { useState, useEffect, useRef } from 'react'

// Import CSV files as raw text (Vite handles this with ?raw)
import trendsRaw from '@data/trends.csv?raw'
import velocityRaw from '@data/velocity.csv?raw'
import heatmapRaw from '@data/heatmap.csv?raw'
import alertsRaw from '@data/alerts.csv?raw'
import sourcesRaw from '@data/sources.csv?raw'
import fundingRaw from '@data/funding.csv?raw'
import sourceNodesRaw from '@data/source_nodes.csv?raw'

function parseCSV(raw) {
  const lines = raw.trim().split('\n')
  const headers = lines[0].split(',')
  return lines.slice(1).map(line => {
    const values = []
    let current = ''
    let inQuotes = false
    for (const ch of line) {
      if (ch === '"') { inQuotes = !inQuotes; continue }
      if (ch === ',' && !inQuotes) { values.push(current); current = ''; continue }
      current += ch
    }
    values.push(current)
    const obj = {}
    headers.forEach((h, i) => { obj[h.trim()] = (values[i] ?? '').trim() })
    return obj
  })
}

function buildTrends(rows) {
  return rows.map(r => ({
    entity:             r.entity,
    trend_score:        parseFloat(r.trend_score),
    velocity_delta_pct: parseFloat(r.velocity_delta_pct),
    sentiment: {
      positive: parseFloat(r.sentiment_positive),
      neutral:  parseFloat(r.sentiment_neutral),
      negative: parseFloat(r.sentiment_negative),
    },
    mention_count_1h:  parseInt(r.mention_count_1h, 10),
    mention_count_24h: parseInt(r.mention_count_24h, 10),
    spike_detected:    r.spike_detected === 'true',
    sources:           r.sources.split(','),
    top_keywords:      r.top_keywords.split(','),
    source_counts:     {}, // filled by heatmap data
  }))
}

function buildVelocity(rows) {
  const allKeys = Object.keys(rows[0]).filter(k => k !== 'time')
  const points = rows.map(r => {
    const point = { time: r.time }
    allKeys.forEach(k => { point[k] = parseFloat(r[k]) })
    return point
  })
  return { points, entities: allKeys }
}

function buildHeatmap(rows) {
  const sources = ['hackernews', 'reddit', 'techcrunch', 'twitter', 'producthunt']
  return rows.map(r => {
    const counts = {}
    sources.forEach(s => { counts[s] = parseInt(r[s], 10) || 0 })
    return { entity: r.entity, sources: counts }
  })
}

function buildAlerts(rows) {
  const now = Date.now()
  return rows.map(r => ({
    id:                  r.id,
    entity:              r.entity,
    message:             r.message,
    velocity_multiplier: parseFloat(r.velocity_multiplier),
    sources:             r.sources.split(','),
    timestamp:           new Date(now - parseInt(r.timestamp_offset_min, 10) * 60000).toISOString(),
  }))
}

function buildSources(rows) {
  const now = Date.now()
  return rows.map(r => ({
    id:             r.id,
    status:         r.status,
    items_ingested: parseInt(r.items_ingested, 10),
    last_scraped:   new Date(now - parseInt(r.last_scraped_offset_sec, 10) * 1000).toISOString(),
    error_message:  r.error_message || undefined,
  }))
}

function buildFunding(rows) {
  return rows.map(r => ({
    entity:         r.entity,
    round:          r.round,
    amount_m:       parseFloat(r.amount_m),
    date:           r.date,
    valuation_b:    parseFloat(r.valuation_b),
    lead_investor:  r.lead_investor,
  }))
}

function buildSourceNodes(rows) {
  return rows.map(r => ({
    id:           r.id,
    entity:       r.entity,
    source_id:    r.source_id,
    source_name:  r.source_name,
    headline:     r.headline,
    url:          r.url,
    summary:      r.summary,
    interactions: parseInt(r.interactions, 10) || 0,
    views:        parseInt(r.views, 10) || 0,
  }))
}

/**
 * useMockData — provides realistic data to the dashboard when no backend is running.
 * Simulates live WebSocket behavior with periodic updates.
 */
export function useMockData({ paused = false } = {}) {
  const [trends, setTrends]         = useState([])
  const [velocityData, setVelocity] = useState([])
  const [velocityEntities, setVelocityEntities] = useState([])
  const [heatmapData, setHeatmap]   = useState([])
  const [alerts, setAlerts]         = useState([])
  const [sources, setSources]       = useState([])
  const [fundingData, setFunding]   = useState([])
  const [sourceNodes, setSourceNodes] = useState([])
  const [loading, setLoading]       = useState(true)
  const tickRef = useRef(0)
  const pausedRef = useRef(paused)

  // Keep ref in sync so interval callback sees current value
  useEffect(() => { pausedRef.current = paused }, [paused])

  useEffect(() => {
    // Parse all CSVs
    const trendRows   = parseCSV(trendsRaw)
    const velRows     = parseCSV(velocityRaw)
    const heatRows    = parseCSV(heatmapRaw)
    const alertRows   = parseCSV(alertsRaw)
    const sourceRows  = parseCSV(sourcesRaw)
    const fundingRows = parseCSV(fundingRaw)
    const sourceNodeRows = parseCSV(sourceNodesRaw)

    const parsedTrends = buildTrends(trendRows)
    const { points, entities } = buildVelocity(velRows)
    const parsedHeatmap = buildHeatmap(heatRows)
    const parsedAlerts  = buildAlerts(alertRows)
    const parsedSources = buildSources(sourceRows)
    const parsedFunding = buildFunding(fundingRows)
    const parsedSourceNodes = buildSourceNodes(sourceNodeRows)

    // Merge heatmap source_counts into trends
    const heatLookup = {}
    parsedHeatmap.forEach(h => { heatLookup[h.entity] = h.sources })
    parsedTrends.forEach(t => {
      if (heatLookup[t.entity]) t.source_counts = heatLookup[t.entity]
    })

    // Start with a subset of velocity data and grow it
    const initialPoints = points.slice(0, 12)

    setTrends(parsedTrends)
    setVelocity(initialPoints)
    setVelocityEntities(entities)
    setHeatmap(parsedHeatmap)
    setAlerts(parsedAlerts)
    setSources(parsedSources)
    setFunding(parsedFunding)
    setSourceNodes(parsedSourceNodes)
    setLoading(false)

    // Simulate live velocity ticks
    let idx = 12
    const interval = setInterval(() => {
      if (pausedRef.current) return

      if (idx < points.length) {
        setVelocity(prev => [...prev, points[idx]])
        idx++
      } else {
        // Generate synthetic continuation
        tickRef.current++
        const mins = 3 * 60 + tickRef.current * 5
        const h = String(Math.floor(mins / 60)).padStart(2, '0')
        const m = String(mins % 60).padStart(2, '0')
        const lastPoint = points[points.length - 1]
        const newPoint = { time: `${h}:${m}` }
        entities.forEach(e => {
          const base = lastPoint[e] || 20
          newPoint[e] = Math.max(1, base + Math.round((Math.random() - 0.4) * 16))
        })
        setVelocity(prev => [...prev.slice(-55), newPoint])
      }

      // Randomly jitter trend scores slightly
      setTrends(prev => prev.map(t => ({
        ...t,
        trend_score: +(t.trend_score + (Math.random() - 0.48) * 0.6).toFixed(1),
        mention_count_1h: t.mention_count_1h + (Math.random() > 0.6 ? 1 : 0),
      })))

      // Refresh source timestamps
      setSources(buildSources(sourceRows))
    }, 3000)

    return () => clearInterval(interval)
  }, [])

  return {
    trends,
    velocityData,
    velocityEntities,
    heatmapData,
    alerts,
    sources,
    fundingData,
    sourceNodes,
    loading,
    connected: !loading, // simulate WS connected
  }
}
