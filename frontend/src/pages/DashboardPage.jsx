import { useEffect, useMemo, useState } from 'react'
import Header from '../components/Header'
import NodeGraph from '../components/NodeGraph'
import ClusterMap from '../components/ClusterMap'
import FundingTimeline from '../components/FundingTimeline'
import TrendLeaderboard from '../components/TrendLeaderboard'
import { useWebSocket } from '../hooks/useWebSocket'
import { fetchEntityNodes, searchNiche, useSourceStatus, useTrends } from '../hooks/useApi'
import './DashboardPage.css'

function scoreNode(node) {
  return node.interactions + node.views * 0.18
}

function normalizeEntityKey(value = '') {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
}

function formatDate(value) {
  if (!value) return 'N/A'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'N/A'
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export default function DashboardPage() {
  const [drilledEntity, setDrilledEntity] = useState(null)
  const [secondaryTab, setSecondaryTab] = useState(null)
  const [nicheQuery, setNicheQuery] = useState('')
  const [nicheLoading, setNicheLoading] = useState(false)
  const [nicheError, setNicheError] = useState('')
  const [nicheResults, setNicheResults] = useState([])
  const [nicheUsedNemotron, setNicheUsedNemotron] = useState(false)
  const [clusterScope, setClusterScope] = useState('top')
  const [apiEntityNodes, setApiEntityNodes] = useState([])
  const [apiEntityNodesLoading, setApiEntityNodesLoading] = useState(false)

  const {
    connected: wsConnected,
    trends: wsTrends,
    sources: wsSources,
  } = useWebSocket()

  const { trends: apiTrends, loading: trendsLoading } = useTrends({
    enabled: !wsConnected,
    pollInterval: 10000,
  })
  const { sources: apiSources } = useSourceStatus({ pollInterval: 15000 })

  const connected = wsConnected || apiTrends.length > 0
  const hasLiveData = connected
  const trendsRaw = apiTrends.length > 0 ? apiTrends : (wsConnected ? wsTrends : apiTrends)
  const trendsTopPool = useMemo(
    () => trendsRaw.filter((row) => !Boolean(row?.is_known_incumbent)),
    [trendsRaw],
  )
  const trendsTop = useMemo(() => {
    const base = trendsTopPool.length > 0 ? trendsTopPool : trendsRaw
    return [...base]
      .sort((a, b) => Number(b?.momentum_score ?? b?.trend_score ?? 0) - Number(a?.momentum_score ?? a?.trend_score ?? 0))
      .slice(0, 50)
  }, [trendsTopPool, trendsRaw])

  const nicheClusterTrends = useMemo(
    () =>
      nicheResults.map((row, idx) => ({
        entity_key: row.entity_key || normalizeEntityKey(row.entity),
        entity: row.entity,
        trend_score: Number(row.final_score || 0),
        mention_count_1h: Number(row.mention_count_1h || 0),
        mention_count_24h: Number(row.mention_count_24h || 0),
        spike_detected: Boolean(row.spike_detected),
        sources: Array.isArray(row.sources) ? row.sources : [],
        source_counts: row.source_counts ?? {},
        top_keywords: row.top_keywords ?? [],
        first_seen_at: row.first_seen_at ?? null,
        last_seen_at: row.last_seen_at ?? null,
        activity_last_30d: Number(row.activity_last_30d || 0),
        _rank: idx + 1,
      })),
    [nicheResults],
  )

  const trends = clusterScope === 'niche' && nicheClusterTrends.length > 0 ? nicheClusterTrends : trendsTop
  const sources = wsSources.length > 0 ? wsSources : apiSources

  const selectedTrend = [...trendsTop, ...nicheClusterTrends].find((item) => {
    if (!drilledEntity) return false
    const drilledKey = normalizeEntityKey(drilledEntity)
    if (item.entity_key && item.entity_key === drilledKey) return true
    return normalizeEntityKey(item.entity) === drilledKey
  })

  const sourceNodesForEntity = useMemo(() => {
    if (!drilledEntity) return []
    const selectedKey = selectedTrend?.entity_key || normalizeEntityKey(drilledEntity)
    const drilledKey = normalizeEntityKey(drilledEntity)
    const fromApi = (apiEntityNodes || []).map((node, idx) => ({
      id: node.id || `api-${selectedKey}-${idx}`,
      entity: drilledEntity,
      source_id: node.source_id || 'unknown',
      source_name: node.source_name || node.source_id || 'Source',
      headline: node.headline || 'Untitled source',
      url: node.url || '',
      summary: node.summary || 'No summary available.',
      interactions: Number(node.interactions || 0),
      views: Number(node.views || 0),
      node_type: node.node_type || 'source_raw',
    }))
    const fromNiche = nicheResults
      .find((row) => normalizeEntityKey(row.entity) === drilledKey)
      ?.top_nodes ?? []

    const normalizedNicheNodes = fromNiche.map((node, idx) => ({
      id: node.id || `niche-${drilledKey}-${idx}`,
      entity: drilledEntity,
      source_id: node.source_id || 'unknown',
      source_name: node.source_name || node.source_id || 'Source',
      headline: node.headline || 'Untitled source',
      url: node.url || '',
      summary: node.summary || 'No summary available.',
      interactions: Number(node.interactions || 0),
      views: Number(node.views || 0),
      node_type: node.node_type || 'source_raw',
    }))

    const combined = [...fromApi, ...normalizedNicheNodes]
    const deduped = Array.from(new Map(combined.map((node) => [node.id, node])).values())

    return deduped
      .sort((a, b) => scoreNode(b) - scoreNode(a))
      .slice(0, 20)
  }, [apiEntityNodes, nicheResults, drilledEntity, selectedTrend?.entity_key])

  const isLoading = !wsConnected && trendsLoading && trendsRaw.length === 0

  useEffect(() => {
    let cancelled = false
    async function loadEntityNodes() {
      if (!drilledEntity) {
        setApiEntityNodes([])
        return
      }
      const entityKey = selectedTrend?.entity_key || normalizeEntityKey(drilledEntity)
      if (!entityKey) {
        setApiEntityNodes([])
        return
      }
      setApiEntityNodesLoading(true)
      try {
        const payload = await fetchEntityNodes(entityKey, {
          includeEnriched: true,
          limit: 40,
        })
        if (!cancelled) {
          setApiEntityNodes(Array.isArray(payload?.nodes) ? payload.nodes : [])
        }
      } catch {
        if (!cancelled) setApiEntityNodes([])
      } finally {
        if (!cancelled) setApiEntityNodesLoading(false)
      }
    }
    loadEntityNodes()
    return () => {
      cancelled = true
    }
  }, [drilledEntity, selectedTrend?.entity_key])

  function handleSelectEntity(entity) {
    setDrilledEntity(entity)
    setSecondaryTab(null)
  }

  function handleBack() {
    setDrilledEntity(null)
    setSecondaryTab(null)
  }

  function handleShowTopCluster() {
    setClusterScope('top')
  }

  function handleShowNicheCluster() {
    if (nicheClusterTrends.length > 0) setClusterScope('niche')
  }

  async function handleRunNicheSearch() {
    if (!nicheQuery.trim() || nicheLoading) return
    setNicheLoading(true)
    setNicheError('')
    try {
      const payload = await searchNiche({
        query: nicheQuery.trim(),
        limit: 50,
        useNemotron: true,
        enrichOnDemand: true,
        enrichLimit: 5,
      })
      setNicheResults(payload.results ?? [])
      setNicheUsedNemotron(Boolean(payload.used_nemotron))
      setClusterScope('niche')
    } catch (error) {
      const rawMessage = String(error?.message || '').toLowerCase()
      if (rawMessage.includes('failed to fetch')) {
        setNicheError('API offline. Start backend/search_api.py on port 8000.')
      } else {
        setNicheError(error?.message || 'Search failed.')
      }
      setNicheResults([])
      setNicheUsedNemotron(false)
    } finally {
      setNicheLoading(false)
    }
  }

  const showCluster = !drilledEntity && !secondaryTab
  const showSourceWeb = !!drilledEntity && !secondaryTab
  const showLeaderboard = secondaryTab === 'leaderboard'
  const showTimeline = secondaryTab === 'timeline'

  return (
    <div className="db-root">
      <Header sources={sources} wsConnected={connected} />

      <nav className="db-nav">
        <div className="db-nav-left">
          {drilledEntity ? (
            <>
              <button type="button" className="db-back" onClick={handleBack}>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Overview
              </button>
              <span className="db-nav-sep" />
              <span className="db-drilled-entity">{drilledEntity}</span>
              {selectedTrend && (
                <>
                  <span className="db-drilled-stat">
                    <span className="db-stat-label">Score</span>
                    <span className="db-stat-value">{selectedTrend.trend_score.toFixed(1)}</span>
                  </span>
                  <span className="db-drilled-stat">
                    <span className="db-stat-label">Mentions/h</span>
                    <span className="db-stat-value">{selectedTrend.mention_count_1h}</span>
                  </span>
                  <span className="db-drilled-stat">
                    <span className="db-stat-label">First seen</span>
                    <span className="db-stat-value">{formatDate(selectedTrend.first_seen_at)}</span>
                  </span>
                  <span className="db-drilled-stat">
                    <span className="db-stat-label">Last seen</span>
                    <span className="db-stat-value">{formatDate(selectedTrend.last_seen_at)}</span>
                  </span>
                </>
              )}
            </>
          ) : (
            <span className="db-nav-title">Startup Overview</span>
          )}
        </div>

        <div className="db-nav-right">
          <button
            type="button"
            className={`db-nav-btn ${showLeaderboard ? 'db-nav-btn--active' : ''}`}
            onClick={() => setSecondaryTab(showLeaderboard ? null : 'leaderboard')}
          >
            Rankings
          </button>
          <button
            type="button"
            className={`db-nav-btn ${showTimeline ? 'db-nav-btn--active' : ''}`}
            onClick={() => setSecondaryTab(showTimeline ? null : 'timeline')}
          >
            Funding
          </button>
          <button
            type="button"
            className={`db-nav-btn ${clusterScope === 'top' && !secondaryTab && !drilledEntity ? 'db-nav-btn--active' : ''}`}
            onClick={handleShowTopCluster}
          >
            Top 50
          </button>
          <button
            type="button"
            className={`db-nav-btn ${clusterScope === 'niche' && !secondaryTab && !drilledEntity ? 'db-nav-btn--active' : ''}`}
            onClick={handleShowNicheCluster}
            disabled={nicheClusterTrends.length === 0}
          >
            Niche Map
          </button>
          <span className="db-entity-count mono">{trends.length} entities</span>
        </div>
      </nav>

      <main className="db-main">
        <div className="db-canvas">
          {showCluster && (
            <div className="db-view">
              <ClusterMap
                trends={trends}
                loading={isLoading}
                selectedEntity={drilledEntity}
                onSelectEntity={handleSelectEntity}
              />
            </div>
          )}

          {showSourceWeb && (
            <div className="db-view">
              <NodeGraph
                company={drilledEntity}
                nodes={sourceNodesForEntity}
                loading={sourceNodesForEntity.length === 0 && (isLoading || apiEntityNodesLoading)}
              />
            </div>
          )}

          {showLeaderboard && (
            <div className="db-view">
              <TrendLeaderboard
                entities={trends}
                loading={isLoading}
                selectedEntity={drilledEntity}
                onSelectEntity={handleSelectEntity}
                mode="table"
              />
            </div>
          )}

          {showTimeline && (
            <div className="db-view">
              <FundingTimeline data={[]} loading={isLoading} />
            </div>
          )}
        </div>

        <aside className="db-sidepanel">
          <section className="db-search-panel">
            <header className="db-search-head">
              <span className="section-label">Niche Query</span>
              {nicheUsedNemotron && <span className="db-search-chip mono">Nemotron</span>}
            </header>

            <div className="db-search-form">
              <input
                className="db-search-input"
                type="text"
                placeholder='Try: "weather intelligence startup"'
                value={nicheQuery}
                onChange={(event) => setNicheQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') handleRunNicheSearch()
                }}
              />
              <button
                type="button"
                className="db-search-btn"
                onClick={handleRunNicheSearch}
                disabled={nicheLoading || !nicheQuery.trim()}
              >
                {nicheLoading ? 'Searching...' : 'Search'}
              </button>
            </div>

            {nicheError && <p className="db-search-error">{nicheError}</p>}

            {!nicheError && nicheResults.length === 0 && (
              <p className="db-search-empty">Run a query to rank startups by niche.</p>
            )}

            {nicheResults.length > 0 && (
              <div className="db-search-results">
                {nicheResults.map((result, index) => (
                  <button
                    key={result.entity_key || `${result.entity}-${index}`}
                    type="button"
                    className="db-search-row"
                    onClick={() => handleSelectEntity(result.entity)}
                  >
                    <span className="db-search-rank mono">{String(index + 1).padStart(2, '0')}</span>
                    <span className="db-search-name">{result.entity}</span>
                    <span className="db-search-score mono">{Number(result.final_score || 0).toFixed(1)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </aside>
      </main>

      <footer className="db-statusbar">
        <span className="db-status-item mono">
          <span className={`db-status-dot ${connected ? 'db-status-dot--live' : 'db-status-dot--off'}`} />
          {wsConnected ? 'WS Live' : hasLiveData ? 'REST' : 'Offline'}
        </span>
        <span className="db-status-item mono">{drilledEntity || 'All entities'}</span>
        <span className="db-status-item mono">{trends.length} tracked</span>
      </footer>
    </div>
  )
}
