import { useEffect, useMemo, useState } from 'react'
import Header from '../components/Header'
import NodeGraph from '../components/NodeGraph'
import ClusterMap from '../components/ClusterMap'
import FundingTimeline from '../components/FundingTimeline'
import TrendLeaderboard from '../components/TrendLeaderboard'
import { useWebSocket } from '../hooks/useWebSocket'
import { searchNiche, useTrends } from '../hooks/useApi'
import { useMockData } from '../hooks/useMockData'
import './DashboardPage.css'

function scoreNode(node) {
  return node.interactions + node.views * 0.18
}

function normalizeEntityKey(value = '') {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
}

export default function DashboardPage() {
  const [drilledEntity, setDrilledEntity] = useState(null)
  const [secondaryTab, setSecondaryTab] = useState(null)
  const [nicheQuery, setNicheQuery] = useState('')
  const [nicheLoading, setNicheLoading] = useState(false)
  const [nicheError, setNicheError] = useState('')
  const [nicheResults, setNicheResults] = useState([])
  const [nicheUsedNemotron, setNicheUsedNemotron] = useState(false)

  const {
    connected: wsConnected,
    trends: wsTrends,
    sources: wsSources,
  } = useWebSocket()

  const { trends: apiTrends, loading: trendsLoading } = useTrends({
    enabled: !wsConnected,
    pollInterval: 10000,
  })

  const mock = useMockData()

  const hasLiveData = wsConnected || apiTrends.length > 0
  const connected = wsConnected || mock.connected

  const trends = wsConnected
    ? wsTrends
    : apiTrends.length > 0
      ? apiTrends
      : mock.trends

  const sources = wsSources.length > 0 ? wsSources : mock.sources

  const sourceNodesForEntity = useMemo(() => {
    if (!drilledEntity) return []
    const drilledKey = normalizeEntityKey(drilledEntity)
    const fromData = mock.sourceNodes.filter((node) => normalizeEntityKey(node.entity) === drilledKey)
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
    }))

    const combined = [...fromData, ...normalizedNicheNodes]
    const deduped = Array.from(new Map(combined.map((node) => [node.id, node])).values())

    return deduped
      .sort((a, b) => scoreNode(b) - scoreNode(a))
      .slice(0, 20)
  }, [mock.sourceNodes, nicheResults, drilledEntity])

  const selectedTrend = trends.find((item) => normalizeEntityKey(item.entity) === normalizeEntityKey(drilledEntity))
  const isLoading = trendsLoading && !connected && mock.loading

  function handleSelectEntity(entity) {
    setDrilledEntity(entity)
    setSecondaryTab(null)
  }

  function handleBack() {
    setDrilledEntity(null)
    setSecondaryTab(null)
  }

  async function handleRunNicheSearch() {
    if (!nicheQuery.trim() || nicheLoading) return
    setNicheLoading(true)
    setNicheError('')
    try {
      const payload = await searchNiche({
        query: nicheQuery.trim(),
        limit: 10,
        useNemotron: true,
      })
      setNicheResults(payload.results ?? [])
      setNicheUsedNemotron(Boolean(payload.used_nemotron))
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
                loading={isLoading}
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
              <FundingTimeline data={mock.fundingData} loading={isLoading} />
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
          {wsConnected ? 'WS Live' : hasLiveData ? 'REST' : 'Demo'}
        </span>
        <span className="db-status-item mono">{drilledEntity || 'All entities'}</span>
        <span className="db-status-item mono">{trends.length} tracked</span>
      </footer>
    </div>
  )
}
