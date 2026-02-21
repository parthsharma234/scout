import './SourceMonitor.css'

const SOURCE_META = {
  hackernews: { label: 'Hacker News', short: 'HN', color: '#ff8b3d' },
  github: { label: 'GitHub', short: 'GH', color: '#8b5cf6' },
  reddit: { label: 'Reddit', short: 'RD', color: '#ff7340' },
  techcrunch: { label: 'Tech RSS', short: 'TC', color: '#37d67a' },
  twitter: { label: 'Twitter/X', short: 'TW', color: '#5bc7ff' },
  producthunt: { label: 'Product Hunt', short: 'PH', color: '#ff9865' },
}

function statusLabel(status) {
  if (status === 'live') return 'Live'
  if (status === 'cached') return 'Cached'
  if (status === 'error') return 'Error'
  if (status === 'rate_limited') return 'Rate limited'
  return 'Unknown'
}

function tone(status) {
  if (status === 'live') return 'badge badge--green'
  if (status === 'cached') return 'badge badge--amber'
  if (status === 'error' || status === 'rate_limited') return 'badge badge--red'
  return 'badge'
}

function formatAgo(value) {
  if (!value) return '--'
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value)) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

function SourceRow({ source }) {
  const {
    id,
    status = 'unknown',
    last_scraped,
    items_ingested = 0,
    error_message,
  } = source

  const meta = SOURCE_META[id] ?? { label: id, short: id?.slice(0, 2)?.toUpperCase(), color: '#93c5fd' }

  return (
    <article className="sm-row">
      <div className="sm-main">
        <span className="sm-dot" style={{ backgroundColor: meta.color }} />
        <div>
          <strong>{meta.label}</strong>
          {error_message ? <span className="sm-error mono">{error_message}</span> : null}
        </div>
      </div>
      <div className="sm-meta">
        <span className={tone(status)}>{statusLabel(status)}</span>
        <span className="sm-stat mono">{items_ingested.toLocaleString()} items</span>
        <span className="sm-stat mono">{formatAgo(last_scraped)}</span>
      </div>
    </article>
  )
}

function PlaceholderRow({ id }) {
  const source = SOURCE_META[id]
  return (
    <article className="sm-row">
      <div className="sm-main">
        <span className="sm-dot" style={{ backgroundColor: source.color }} />
        <div>
          <strong>{source.label}</strong>
        </div>
      </div>
      <div className="sm-meta">
        <span className="badge">Pending</span>
      </div>
    </article>
  )
}

export default function SourceMonitor({ sources = [] }) {
  const hasData = sources.length > 0

  return (
    <section className="sm-root card">
      <header className="sm-header">
        <p className="section-label">Source Monitor</p>
      </header>

      <div className="sm-body">
        {hasData
          ? sources.map((source) => <SourceRow key={source.id} source={source} />)
          : Object.keys(SOURCE_META).map((id) => <PlaceholderRow key={id} id={id} />)
        }
      </div>
    </section>
  )
}
