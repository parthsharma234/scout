import { useEffect, useMemo, useState } from 'react'
import Header from '../components/Header'
import NodeGraph from '../components/NodeGraph'
import ClusterMap from '../components/ClusterMap'
import FundingTimeline from '../components/FundingTimeline'
import TrendLeaderboard from '../components/TrendLeaderboard'
import { useWebSocket } from '../hooks/useWebSocket'
import { useTrends } from '../hooks/useApi'
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

    const deduped = Array.from(new Map(fromData.map((node) => [node.id, node])).values())

    return deduped
      .sort((a, b) => scoreNode(b) - scoreNode(a))
      .slice(0, 20)
  }, [mock.sourceNodes, drilledEntity])

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
