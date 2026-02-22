import { useEffect, useState } from 'react'
import ClusterMap from '../components/ClusterMap'
import NodeGraph from '../components/NodeGraph'
import './DashboardPage.css' // We can reuse

export default function HomePage() {
  const [trends, setTrends] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  
  const [drilledEntity, setDrilledEntity] = useState(null)
  
  useEffect(() => {
    async function fetchTop50() {
      try {
        const res = await fetch('http://localhost:8000/api/top50')
        if (!res.ok) throw new Error('Failed to fetch from API')
        const data = await res.json()
        setTrends(data.entities || [])
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    fetchTop50()
  }, [])

  function handleSelectEntity(entityName) {
    setDrilledEntity(entityName)
  }

  function handleBack() {
    setDrilledEntity(null)
  }

  const selectedTrend = trends.find(t => t.entity === drilledEntity)
  const sourceNodes = selectedTrend?.node_graph || []
  const showCluster = !drilledEntity
  const showSourceWeb = !!drilledEntity

  return (
    <div className="db-root" style={{ background: '#0a0a0a' }}>
      <header className="hp-nav-wrap" style={{ position: 'sticky', top: 0, zIndex: 100, borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
        <nav className="hp-nav">
          <div className="hp-brand">
            <span className="hp-brand-icon" />
            Scout 
            <span className="mono" style={{ fontSize: '10px', marginLeft: 12, opacity: 0.6 }}>Top 50 Discoveries</span>
          </div>
        </nav>
      </header>

      <nav className="db-nav" style={{ padding: '0 24px', height: 48, display: 'flex', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
        <div className="db-nav-left" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {drilledEntity ? (
            <>
              <button type="button" className="db-back" onClick={handleBack} style={{ background: 'none', border: 'none', color: '#60a5fa', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: '13px' }}>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Back to Map
              </button>
              <span className="db-nav-sep" style={{ opacity: 0.2 }}>|</span>
              <span className="db-drilled-entity" style={{ fontWeight: 600, color: '#fff' }}>{drilledEntity}</span>
              {selectedTrend && (
                <>
                  <span className="db-drilled-stat" style={{ fontSize: '12px', marginLeft: 16 }}>
                    <span style={{ opacity: 0.6, marginRight: 6 }}>Score:</span> 
                    <span style={{ color: '#fff', fontWeight: 600 }}>{selectedTrend.trend_score}</span>
                  </span>
                  <span className="db-drilled-stat" style={{ fontSize: '12px', marginLeft: 12 }}>
                    <span style={{ opacity: 0.6, marginRight: 6 }}>Stage:</span> 
                    <span style={{ color: '#fff', fontWeight: 600 }}>{selectedTrend.stage}</span>
                  </span>
                </>
              )}
            </>
          ) : (
            <span className="db-nav-title" style={{ fontSize: '14px', fontWeight: 500, color: '#fff' }}>Global Startup Map</span>
          )}
        </div>
      </nav>

      <main className="db-main" style={{ display: 'flex', flex: 1, minHeight: 'calc(100vh - 120px)' }}>
        <div className="db-canvas" style={{ flex: 1, position: 'relative' }}>
          {loading && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#666' }}>
              Loading API data...
            </div>
          )}
          {error && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444' }}>
              Error: {error}
            </div>
          )}
          
          {showCluster && !loading && !error && (
             <ClusterMap
               trends={trends}
               loading={false}
               selectedEntity={drilledEntity}
               onSelectEntity={handleSelectEntity}
             />
          )}

          {showSourceWeb && (
            <NodeGraph
              company={drilledEntity}
              nodes={sourceNodes}
              loading={false}
            />
          )}
        </div>
        
        {/* Simplified side-panel to show details of selected startup */}
        {showSourceWeb && selectedTrend && (
          <aside className="db-sidepanel" style={{ width: 340, borderLeft: '1px solid rgba(255,255,255,0.05)', padding: 24, background: 'rgba(255,255,255,0.01)' }}>
             <h3 style={{ margin: '0 0 16px', color: '#fff', fontSize: '18px' }}>{selectedTrend.entity}</h3>
             <p style={{ margin: '0 0 24px', color: '#94a3b8', fontSize: '14px', lineHeight: 1.5 }}>
               {selectedTrend.one_liner}
             </p>
             
             <div style={{ marginBottom: 20 }}>
               <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#666', marginBottom: 8 }}>Vertical</div>
               <div style={{ display: 'inline-block', padding: '4px 10px', background: 'rgba(59,130,246,0.1)', color: '#60a5fa', borderRadius: 4, fontSize: '13px' }}>
                 {selectedTrend.vertical}
               </div>
             </div>
             
             <div style={{ marginBottom: 20 }}>
               <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#666', marginBottom: 8 }}>Sources Detected</div>
               {selectedTrend.sources.map((s, i) => (
                 <div key={i} style={{ fontSize: '13px', color: '#e2e8f0', marginBottom: 4 }}>• {s}</div>
               ))}
             </div>
          </aside>
        )}
      </main>
    </div>
  )
}
