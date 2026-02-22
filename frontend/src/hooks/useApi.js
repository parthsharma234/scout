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
export function useTrends({ enabled = true, pollInterval = 10000 } = {}) {
  const [trends, setTrends] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetch = useCallback(async () => {
    if (!enabled) return
    setLoading(true)
    setError(null)
    try {
      console.log('[Scout Debug] Fetching /api/trends/ ...')
      const data = await apiFetch('/api/trends/')
      console.log('[Scout Debug] Raw API response:', JSON.stringify(data).substring(0, 500))
      // Expected: { entities: TrendEntity[] } or TrendEntity[]
      const entities = Array.isArray(data) ? data : (data.entities ?? [])
      console.log('[Scout Debug] Parsed', entities.length, 'entities from API')
      setTrends(entities)
    } catch (err) {
      console.error('[Scout Debug] API fetch FAILED:', err.message)
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [enabled])

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
  const [error, setError] = useState(null)

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
  const [data, setData] = useState([])
  const [entities, setEntities] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

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
  enrichOnDemand = true,
  enrichLimit = 5,
  queryProfile = {},
  dimensionPriorityRank = {},
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
      enrich_on_demand: enrichOnDemand,
      enrich_limit: enrichLimit,
      query_profile: queryProfile,
      dimension_priority_rank: dimensionPriorityRank,
    }),
  })
}

export async function fetchEntityNodes(entityKey, { includeEnriched = true, limit = 40 } = {}) {
  if (!entityKey) return { nodes: [] }
  const params = new URLSearchParams({
    include_enriched: includeEnriched ? 'true' : 'false',
    limit: String(limit),
  })
  return apiFetch(`/api/entity/${encodeURIComponent(entityKey)}/nodes?${params.toString()}`)
}

export async function fetchEntityHistory(entityKey, { windowDays = 180 } = {}) {
  if (!entityKey) return { history: [] }
  const params = new URLSearchParams({
    window_days: String(windowDays),
  })
  return apiFetch(`/api/entity/${encodeURIComponent(entityKey)}/history?${params.toString()}`)
}

export async function getPipelineStatus() {
  return apiFetch('/api/pipeline/status')
}

export async function triggerPipelineRun({ mode = 'manual', doBackfill = true, doEnrichment = true } = {}) {
  return apiFetch('/api/pipeline/run', {
    method: 'POST',
    body: JSON.stringify({
      mode,
      do_backfill: doBackfill,
      do_enrichment: doEnrichment,
    }),
  })
}

export function useUserProfile(userId) {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchProfile = useCallback(async () => {
    if (!userId) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch(`/api/user/profile?user_id=${userId}`)
      setProfile(data)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [userId])

  const updateProfile = async (data) => {
    return apiFetch('/api/user/profile', {
      method: 'POST',
      headers: { 'X-User-ID': userId },
      body: JSON.stringify(data),
    })
  }

  useEffect(() => { fetchProfile() }, [fetchProfile])

  return { profile, loading, error, refetch: fetchProfile, updateProfile }
}

export function useUserBookmarks(userId) {
  const [bookmarks, setBookmarks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetch = useCallback(async () => {
    if (!userId) {
      setBookmarks([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch(`/api/user/bookmarks?user_id=${userId}`)
      setBookmarks(data.bookmarks || [])
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [userId])

  const toggleBookmark = async (entityKey) => {
    if (!userId) return
    try {
      const res = await apiFetch('/api/user/bookmarks', {
        method: 'POST',
        headers: { 'X-User-ID': userId },
        body: JSON.stringify({ entity_key: entityKey }),
      })
      fetch() // Refetch after toggle
      return res
    } catch (err) {
      console.error('Failed to toggle bookmark:', err)
      throw err
    }
  }

  const isBookmarked = useCallback((key) => {
    return bookmarks.some(b => b.entity_key === key)
  }, [bookmarks])

  useEffect(() => { fetch() }, [fetch])

  return { bookmarks, loading, error, refetch: fetch, toggleBookmark, isBookmarked }
}
