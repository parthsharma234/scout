import { useState } from 'react'
import './SpikeAlerts.css'

function timeAgo(timestamp) {
  if (!timestamp) return 'now'
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(timestamp)) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

function AlertCard({ alert, onDismiss }) {
  const { id, entity, message, sources = [], velocity_multiplier, timestamp } = alert

  return (
    <article className="sa-card">
      <div className="sa-head">
        <div className="sa-title-wrap">
          <strong className="sa-entity">{entity}</strong>
          {velocity_multiplier ? <span className="sa-multiplier mono">{velocity_multiplier}x velocity</span> : null}
        </div>
        <button type="button" className="sa-dismiss" onClick={() => onDismiss(id)} aria-label="Dismiss alert">
          x
        </button>
      </div>

      {message ? <p className="sa-message">{message}</p> : null}

      <div className="sa-footer">
        <div className="sa-sources">
          {sources.map((source) => (
            <span key={source} className={`source-tag ${source}`}>
              {source === 'hackernews' ? 'HN' : source}
            </span>
          ))}
        </div>
        <span className="sa-time mono">{timeAgo(timestamp)}</span>
      </div>
    </article>
  )
}

function EmptyAlerts() {
  return (
    <div className="sa-empty">
      <p>No active alerts.</p>
      <span>Threshold events will appear here in real time.</span>
    </div>
  )
}

export default function SpikeAlerts({ alerts = [], maxVisible = 8 }) {
  const [dismissedIds, setDismissedIds] = useState(new Set())

  const visible = alerts
    .filter((alert) => !dismissedIds.has(alert.id))
    .slice(0, maxVisible)

  const dismiss = (id) => {
    setDismissedIds((previous) => new Set([...previous, id]))
  }

  return (
    <section className="sa-root surface">
      <header className="sa-header">
        <p className="section-label">Alerts</p>
        <span className="sa-count mono">{visible.length}</span>
      </header>

      <div className="sa-body">
        {visible.length === 0 ? <EmptyAlerts /> : visible.map((alert) => (
          <AlertCard key={alert.id} alert={alert} onDismiss={dismiss} />
        ))}
      </div>
    </section>
  )
}
