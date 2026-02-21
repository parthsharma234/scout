import { useState, useEffect, useRef, useCallback } from 'react'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/trends/'
const RECONNECT_DELAY_MS = 3000
const MAX_RECONNECT_ATTEMPTS = 10

/**
 * useWebSocket
 *
 * Connects to the Django Channels WebSocket at ws://localhost:8000/ws/trends/
 * and provides real-time trend data to the dashboard.
 *
 * Expected server message shape:
 * {
 *   type: 'trend_update' | 'spike_alert' | 'source_status',
 *   payload: { ... }
 * }
 *
 * Returns:
 *   connected    — boolean, true when WS is open
 *   trends       — latest array of trend entities from 'trend_update' messages
 *   alerts       — array of spike alert objects from 'spike_alert' messages
 *   sources      — array of source status objects from 'source_status' messages
 *   velocityData — time-series data for the velocity chart
 */
export function useWebSocket() {
  const [connected, setConnected]       = useState(false)
  const [trends, setTrends]             = useState([])
  const [alerts, setAlerts]             = useState([])
  const [sources, setSources]           = useState([])
  const [velocityData, setVelocityData] = useState([])

  const wsRef              = useRef(null)
  const reconnectAttempts  = useRef(0)
  const reconnectTimer     = useRef(null)
  const alertIdCounter     = useRef(0)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      reconnectAttempts.current = 0
    }

    ws.onmessage = (event) => {
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        console.warn('[Scout WS] Failed to parse message:', event.data)
        return
      }

      const { type, payload } = msg

      switch (type) {
        case 'trend_update':
          // payload: { entities: TrendEntity[] }
          if (Array.isArray(payload?.entities)) {
            setTrends(payload.entities)
          }
          break

        case 'velocity_update':
          // payload: { point: { time: string, [entityName]: number, ... } }
          if (payload?.point) {
            setVelocityData(prev => {
              const updated = [...prev, payload.point]
              // Keep last 60 data points
              return updated.slice(-60)
            })
          }
          break

        case 'spike_alert':
          // payload: SpikeAlert
          if (payload?.entity) {
            setAlerts(prev => [
              { ...payload, id: payload.id ?? `alert-${++alertIdCounter.current}` },
              ...prev,
            ].slice(0, 20)) // keep last 20 alerts
          }
          break

        case 'source_status':
          // payload: { sources: SourceStatus[] }
          if (Array.isArray(payload?.sources)) {
            setSources(payload.sources)
          }
          break

        default:
          console.debug('[Scout WS] Unknown message type:', type)
      }
    }

    ws.onerror = (err) => {
      console.error('[Scout WS] Error:', err)
    }

    ws.onclose = () => {
      setConnected(false)
      wsRef.current = null

      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts.current += 1
        const delay = RECONNECT_DELAY_MS * Math.min(reconnectAttempts.current, 5)
        console.info(`[Scout WS] Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`)
        reconnectTimer.current = setTimeout(connect, delay)
      } else {
        console.error('[Scout WS] Max reconnect attempts reached.')
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connected, trends, alerts, sources, velocityData }
}
