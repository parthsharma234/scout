import { useState, useEffect, useCallback } from 'react'

const rawApiBase = String(import.meta.env.VITE_API_URL || '').trim()
const API_BASE = rawApiBase ? rawApiBase.replace(/\/+$/, '') : ''

function buildApiUrl(path) {
  if (!path) return API_BASE || '/'
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${normalizedPath}`
}

/**
 * Generic fetch wrapper with loading / error state.
 */
async function apiFetch(path, options = {}) {
  let res
  try {
    res = await fetch(buildApiUrl(path), {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch {
    throw new Error('API unreachable. Start backend/search_api.py on port 8000.')
  }

  if (!res.ok) {
    const text = await res.text()
    const lower = String(text || '').toLowerCase()
    const isProxyConnectionError =
      res.status >= 500 &&
      (lower.includes('error occurred while trying to proxy') ||
        lower.includes('econnrefused') ||
        lower.includes('socket hang up') ||
        lower.includes('proxy error'))

    if (isProxyConnectionError) {
      throw new Error('API unreachable. Start backend/search_api.py on port 8000.')
    }

    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return res.json()
}

/**
 * useApi
 *
 * Polls GET /api/trends/ as a fallback when WebSocket is not connected.
 * Set `enabled` to false to disable polling (e.g. when WS is live).
 *
 * Returns:
 *   trends   — TrendEntity[]
 *   loading  — boolean
 *   error    — Error | null
 *   refetch  — manually trigger a refresh
 */
export function useTrends({ enabled = true, pollInterval = 10000, leaderboardMode = 'global_prominence' } = {}) {
  const [trends, setTrends]   = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const fetch = useCallback(async () => {
    if (!enabled) return
    setLoading(true)
    setError(null)
    try {
      const mode = encodeURIComponent(leaderboardMode || 'global_prominence')
      const data = await apiFetch(`/api/trends/?leaderboard=${mode}`)
      // Expected: { entities: TrendEntity[] } or TrendEntity[]
      setTrends(Array.isArray(data) ? data : (data.entities ?? []))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [enabled, leaderboardMode])

  useEffect(() => {
    fetch()
    if (!enabled || pollInterval <= 0) return
    const id = setInterval(fetch, pollInterval)
    return () => clearInterval(id)
  }, [fetch, enabled, pollInterval])

  return { trends, loading, error, refetch: fetch }
}

/**
 * useSourceStatus
 *
 * Polls GET /api/sources/ for agent health data.
 */
export function useSourceStatus({ pollInterval = 15000 } = {}) {
  const [sources, setSources] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const fetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch('/api/sources/')
      setSources(Array.isArray(data) ? data : (data.sources ?? []))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, pollInterval)
    return () => clearInterval(id)
  }, [fetch, pollInterval])

  return { sources, loading, error, refetch: fetch }
}

/**
 * useVelocityHistory
 *
 * Fetches historical velocity data for the chart on mount.
 * GET /api/velocity/?window=1h  (or 6h / 24h)
 */
export function useVelocityHistory(window = '1h') {
  const [data, setData]       = useState([])
  const [entities, setEntities] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const fetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch(`/api/velocity/?window=${window}`)
      // Expected: { points: [{time, EntityA: n, EntityB: n, ...}], entities: string[] }
      setData(res.points ?? [])
      setEntities(res.entities ?? [])
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [window])

  useEffect(() => { fetch() }, [fetch])

  return { data, entities, loading, error }
}

/**
 * searchNiche
 *
 * Calls the niche prompt endpoint:
 * POST /api/niche-search
 */
export async function searchNiche({
  query,
  limit = 12,
  minScore = 10,
  refresh = '',
  rebuildIndex = false,
  useNemotron = true,
} = {}) {
  if (!query || !String(query).trim()) {
    throw new Error('query is required')
  }

  return apiFetch('/api/niche-search', {
    method: 'POST',
    body: JSON.stringify({
      query: String(query).trim(),
      limit,
      min_score: minScore,
      refresh,
      rebuild_index: rebuildIndex,
      use_nemotron: useNemotron,
    }),
  })
}
