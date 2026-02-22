import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
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

export default function Header({ sources = [], wsConnected = false, showBackBtn = false }) {
  const navigate = useNavigate()
  const time = useClock()
  const displaySources = sources.length > 0 ? sources : DEFAULT_SOURCES

  const handleLogout = async () => {
    const { error } = await supabase.auth.signOut()
    if (error) {
      console.error('Error logging out:', error.message)
    } else {
      navigate('/login')
    }
  }

  return (
    <header className="hdr-root">
      <div className="hdr-left">
        {showBackBtn && (
          <>
            <button
              className="hdr-logout"
              onClick={() => navigate('/dashboard')}
              style={{ padding: '4px 10px', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="19" y1="12" x2="5" y2="12"></line>
                <polyline points="12 19 5 12 12 5"></polyline>
              </svg>
              Back
            </button>
            <span className="hdr-divider" />
          </>
        )}
        <button className="hdr-brand" onClick={() => navigate('/')}>
          Scout
        </button>
        <span className="hdr-divider" />
        <span className="hdr-title mono">Intelligence Dashboard</span>
      </div>

      <div className="hdr-center">
      </div>

      <div className="hdr-right">
        <span className={`hdr-connection ${wsConnected ? 'hdr-connection--live' : ''}`}>
          {wsConnected ? 'Live' : 'Syncing'}
        </span>
        <span className="hdr-clock mono">{time}</span>
        <button className="hdr-logout" onClick={() => navigate('/profile')} style={{ marginRight: '8px' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
          Profile
        </button>
        <button className="hdr-logout" onClick={handleLogout}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
          Logout
        </button>
      </div>
    </header>
  )
}
