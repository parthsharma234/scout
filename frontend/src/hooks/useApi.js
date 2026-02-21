import { useState, useEffect, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * Generic fetch wrapper with loading / error state.
 */
async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
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
  const [trends, setTrends]   = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const fetch = useCallback(async () => {
    if (!enabled) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch('/api/trends/')
      // Expected: { entities: TrendEntity[] } or TrendEntity[]
      setTrends(Array.isArray(data) ? data : (data.entities ?? []))
    } catch (err) {
      setError(err)
      console.error('[Scout API] /api/trends/ failed:', err)
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
