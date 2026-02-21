import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './Header.css'

const DEFAULT_SOURCES = [
  { id: 'hackernews', label: 'HN', status: 'live' },
  { id: 'github', label: 'GitHub', status: 'live' },
  { id: 'reddit', label: 'Reddit', status: 'live' },
  { id: 'techcrunch', label: 'RSS', status: 'live' },
  { id: 'twitter', label: 'Twitter', status: 'cached' },
  { id: 'producthunt', label: 'PH', status: 'cached' },
]

function useClock() {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return now.toLocaleTimeString('en-US', { hour12: false })
}

function chipTone(status) {
  if (status === 'live') return 'badge badge--green'
  if (status === 'cached') return 'badge badge--amber'
  if (status === 'error' || status === 'rate_limited') return 'badge badge--red'
  return 'badge'
}

export default function Header({ sources = [], wsConnected = false }) {
  const navigate = useNavigate()
  const time = useClock()
  const displaySources = sources.length > 0 ? sources : DEFAULT_SOURCES

  return (
    <header className="hdr-root">
      <div className="hdr-left">
        <button className="hdr-brand" onClick={() => navigate('/')}>
          Scout
        </button>
        <span className="hdr-divider" />
        <span className="hdr-title mono">Intelligence Dashboard</span>
      </div>

      <div className="hdr-center">
        {displaySources.map((source) => (
          <span key={source.id} className={chipTone(source.status)}>
            {source.label}
          </span>
        ))}
      </div>

      <div className="hdr-right">
        <span className={`hdr-connection ${wsConnected ? 'hdr-connection--live' : ''}`}>
          {wsConnected ? 'Live' : 'Syncing'}
        </span>
        <span className="hdr-clock mono">{time}</span>
      </div>
    </header>
  )
}
