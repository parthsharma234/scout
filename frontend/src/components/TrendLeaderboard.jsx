import { useMemo } from 'react'
import './TrendLeaderboard.css'

const CAT_COLORS = {
  'OpenAI': '#3b82f6', 'Anthropic': '#3b82f6', 'Mistral AI': '#3b82f6',
  'Cursor': '#38bdf8', 'Groq': '#3b82f6', 'Runway': '#f472b6',
  'Perplexity': '#3b82f6', 'Scale AI': '#3b82f6', 'Databricks': '#3b82f6',
  'Stripe': '#fbbf24', 'Vercel': '#38bdf8', 'Supabase': '#38bdf8',
  'Linear': '#38bdf8', 'Replit': '#38bdf8', 'Warp': '#38bdf8',
  'Anduril': '#4ade80', 'SpaceX': '#4ade80', 'Figma': '#38bdf8',
  'Notion': '#38bdf8', 'Rippling': '#fbbf24', 'Cohere': '#3b82f6',
  'Plaid': '#fbbf24', 'Ramp': '#fbbf24', 'Cloudflare': '#38bdf8',
  'ElevenLabs': '#3b82f6', 'Neon': '#38bdf8', 'Resend': '#38bdf8',
  'Midjourney': '#f472b6', 'Hugging Face': '#3b82f6', 'Together AI': '#3b82f6',
}

export default function TrendLeaderboard({
  entities = [],
  loading = false,
  selectedEntity,
  onSelectEntity,
  mode = 'compact',
  leaderboardMode = 'global_prominence',
  onLeaderboardModeChange,
}) {
  const isTable = mode === 'table'
  const maxScore = useMemo(
    () => Math.max(...entities.map((e) => Number(e.trend_score ?? 0)), 1),
    [entities],
  )

  return (
    <section className={`lb-root ${isTable ? 'lb-root--table' : 'surface'}`}>
      {isTable && (
        <header className="lb-header lb-header--table">
          <div>
            <p className="section-label">Rankings</p>
            <h3 className="lb-title">Entity Leaderboard</h3>
          </div>
          <div className="lb-header-actions">
            <div className="lb-mode-toggle" role="tablist" aria-label="Leaderboard mode">
              <button
                type="button"
                className={`lb-mode-btn ${leaderboardMode === 'global_prominence' ? 'lb-mode-btn--active' : ''}`}
                onClick={() => onLeaderboardModeChange?.('global_prominence')}
              >
                Global Prominence
              </button>
              <button
                type="button"
                className={`lb-mode-btn ${leaderboardMode === 'niche_opportunity' ? 'lb-mode-btn--active' : ''}`}
                onClick={() => onLeaderboardModeChange?.('niche_opportunity')}
              >
                Niche Opportunity
              </button>
            </div>
            <span className="lb-count mono">{entities.length} tracked</span>
          </div>
        </header>
      )}

      {!isTable && (
        <header className="lb-header">
          <p className="section-label">Leaderboard</p>
          <span className="lb-count mono">{entities.length}</span>
        </header>
      )}

      {isTable && (
        <div className="lb-table-head">
          <span className="lb-th lb-th--rank">#</span>
          <span className="lb-th lb-th--name">Entity</span>
          <span className="lb-th lb-th--mentions">Mentions/h</span>
          <span className="lb-th lb-th--sources">Sources</span>
          <span className="lb-th lb-th--score">Score</span>
        </div>
      )}

      <div className="lb-list">
        {loading && Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="lb-shimmer" style={{ animationDelay: `${i * 60}ms` }} />
        ))}

        {!loading && entities.length === 0 && (
          <div className="lb-empty">No ranked entities yet.</div>
        )}

        {!loading && entities.map((entity, index) => {
          const isActive = selectedEntity === entity.entity
          const score = Number(entity.trend_score ?? 0)
          const color = CAT_COLORS[entity.entity] || '#60a5fa'
          const spike = entity.spike_detected
          const barWidth = (score / maxScore) * 100

          if (isTable) {
            return (
              <button
                key={entity.entity}
                className={`lb-table-row ${isActive ? 'lb-table-row--active' : ''}`}
                onClick={() => onSelectEntity?.(entity.entity)}
              >
                <span className="lb-td lb-td--rank mono">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="lb-td lb-td--name">
                  <span className="lb-dot" style={{ background: color }} />
                  <span className="lb-entity-name">{entity.entity}</span>
                  {spike && <span className="lb-spike" />}
                </span>
                <span className="lb-td lb-td--mentions mono">
                  {entity.mention_count_1h ?? '—'}
                </span>
                <span className="lb-td lb-td--sources mono">
                  {entity.sources?.length ?? '—'}
                </span>
                <span className="lb-td lb-td--score">
                  <span className="lb-score-bar-track">
                    <span className="lb-score-bar" style={{ width: `${barWidth}%`, background: color }} />
                  </span>
                  <span className="lb-score-value">{score.toFixed(1)}</span>
                </span>
              </button>
            )
          }

          return (
            <button
              key={entity.entity}
              className={`lb-row ${isActive ? 'lb-row--active' : ''}`}
              onClick={() => onSelectEntity?.(entity.entity)}
            >
              <span className="lb-rank mono">{String(index + 1).padStart(2, '0')}</span>
              <span className="lb-dot" style={{ background: color }} />
              <span className="lb-name">{entity.entity}</span>
              {spike && <span className="lb-spike" />}
              <span className="lb-score">{score.toFixed(1)}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
