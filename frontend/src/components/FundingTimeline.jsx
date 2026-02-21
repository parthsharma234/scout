import { useState, useMemo, useRef } from 'react'
import './FundingTimeline.css'

const CAT_COLORS = {
  ai: '#d4706a',
  data: '#5ab8a0',
  fintech: '#c9a84c',
  devtools: '#6b8acd',
  defense: '#7bc67e',
  media: '#b07cc6',
}

const CATEGORIES = {
  'OpenAI': 'ai',
  'Anthropic': 'ai',
  'Mistral AI': 'ai',
  'Cursor': 'ai',
  'Groq': 'ai',
  'Runway': 'media',
  'Perplexity': 'ai',
  'Scale AI': 'ai',
  'Databricks': 'data',
  'Stripe': 'fintech',
  'Vercel': 'devtools',
  'Supabase': 'devtools',
  'Linear': 'devtools',
  'Replit': 'devtools',
  'Warp': 'devtools',
  'Anduril': 'defense',
}

function formatAmount(m) {
  if (m >= 1000) return `$${(m / 1000).toFixed(1)}B`
  return `$${m}M`
}

function dotSize(amount) {
  if (amount >= 5000) return 16
  if (amount >= 1000) return 13
  if (amount >= 200) return 10
  if (amount >= 50) return 8
  return 6
}

export default function FundingTimeline({ data = [], loading = false }) {
  const [hoveredEvent, setHoveredEvent] = useState(null)
  const scrollRef = useRef(null)

  const { years, entityRows, minYear, maxYear } = useMemo(() => {
    if (data.length === 0) return { years: [], entityRows: [], minYear: 2012, maxYear: 2025 }

    const allDates = data.map(d => {
      const parts = d.date.split('-')
      return parseInt(parts[0])
    })
    const min = Math.min(...allDates)
    const max = Math.max(...allDates)
    const yrs = []
    for (let y = min; y <= max; y++) yrs.push(y)

    const byEntity = {}
    data.forEach(d => {
      if (!byEntity[d.entity]) byEntity[d.entity] = []
      byEntity[d.entity].push(d)
    })

    const rows = Object.entries(byEntity)
      .map(([entity, events]) => ({
        entity,
        category: CATEGORIES[entity] || 'ai',
        events: events.sort((a, b) => a.date.localeCompare(b.date)),
        latestValuation: Math.max(...events.map(e => e.valuation_b)),
      }))
      .sort((a, b) => b.latestValuation - a.latestValuation)

    return { years: yrs, entityRows: rows, minYear: min, maxYear: max }
  }, [data])

  function dateToPercent(dateStr) {
    const [y, m] = dateStr.split('-').map(Number)
    const yearSpan = maxYear - minYear + 1
    const offset = (y - minYear) + ((m - 1) / 12)
    return (offset / yearSpan) * 100
  }

  if (loading || data.length === 0) {
    return (
      <div className="ft-root">
        <div className="ft-empty">
          <span className="section-label">No funding data available</span>
        </div>
      </div>
    )
  }

  return (
    <div className="ft-root">
      <header className="ft-header">
        <span className="section-label">Funding Timeline</span>
        <span className="ft-hint mono">Hover for details</span>
      </header>

      <div className="ft-scroll" ref={scrollRef}>
        <div className="ft-grid" style={{ minWidth: `${years.length * 80}px` }}>
          {/* Year markers */}
          <div className="ft-years">
            <div className="ft-entity-label" />
            {years.map(y => (
              <div key={y} className="ft-year mono">{y}</div>
            ))}
          </div>

          {/* Entity rows */}
          {entityRows.map(row => {
            const color = CAT_COLORS[row.category] || '#666'
            return (
              <div key={row.entity} className="ft-row">
                <div className="ft-entity-label">
                  <span className="ft-entity-name">{row.entity}</span>
                  <span className="ft-entity-val mono" style={{ color }}>{formatAmount(row.latestValuation * 1000)}</span>
                </div>
                <div className="ft-track">
                  {/* Grid lines for years */}
                  {years.map(y => (
                    <div
                      key={y}
                      className="ft-gridline"
                      style={{ left: `${((y - minYear) / (maxYear - minYear + 1)) * 100}%` }}
                    />
                  ))}

                  {/* Connection line between events */}
                  {row.events.length > 1 && (
                    <div
                      className="ft-connector"
                      style={{
                        left: `${dateToPercent(row.events[0].date)}%`,
                        width: `${dateToPercent(row.events[row.events.length - 1].date) - dateToPercent(row.events[0].date)}%`,
                        background: color,
                        opacity: 0.15,
                      }}
                    />
                  )}

                  {/* Event dots */}
                  {row.events.map((event, i) => {
                    const left = dateToPercent(event.date)
                    const size = dotSize(event.amount_m)
                    const isHovered = hoveredEvent === `${row.entity}-${i}`
                    return (
                      <div
                        key={i}
                        className={`ft-dot ${isHovered ? 'ft-dot--hovered' : ''}`}
                        style={{
                          left: `${left}%`,
                          width: `${size}px`,
                          height: `${size}px`,
                          background: color,
                          borderColor: isHovered ? color : 'var(--bg-surface)',
                        }}
                        onMouseEnter={() => setHoveredEvent(`${row.entity}-${i}`)}
                        onMouseLeave={() => setHoveredEvent(null)}
                      >
                        {isHovered && (
                          <div className="ft-dot-tooltip" style={{ transform: `translateX(${left > 80 ? '-80%' : left < 20 ? '0%' : '-50%'})` }}>
                            <strong>{event.round}</strong>
                            <span className="mono">{formatAmount(event.amount_m)}</span>
                            <span className="ft-dot-val mono">{event.valuation_b > 0 ? `${event.valuation_b}B val` : ''}</span>
                            <span className="ft-dot-lead">{event.lead_investor}</span>
                            <span className="ft-dot-date mono">{event.date}</span>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
