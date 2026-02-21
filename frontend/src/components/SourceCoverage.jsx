import './SourceCoverage.css'

function EmptyCoverage() {
  return (
    <div className="sc-empty">
      <p>No source activity yet.</p>
      <span>Source distribution appears after entities are scored.</span>
    </div>
  )
}

function statusTone(status) {
  if (status === 'live') return 'badge badge--green'
  if (status === 'cached') return 'badge badge--amber'
  if (status === 'error' || status === 'rate_limited') return 'badge badge--red'
  return 'badge'
}

export default function SourceCoverage({ data = [], loading = false }) {
  const hasData = data.length > 0

  return (
    <section className="sc-root card">
      <div className="sc-header">
        <p className="section-label">Source Coverage</p>
        <span className="sc-sub mono">mentions and entity breadth</span>
      </div>

      <div className="sc-body">
        {loading && (
          <div className="sc-loading" aria-hidden="true">
            <div />
            <div />
            <div />
            <div />
            <div />
          </div>
        )}

        {!loading && !hasData && <EmptyCoverage />}

        {!loading && hasData && data.map((source) => (
          <article key={source.id} className="sc-row">
            <div className="sc-row-main">
              <div className="sc-label-wrap">
                <strong>{source.label}</strong>
                <span className={statusTone(source.status)}>{source.status}</span>
              </div>
              <div className="sc-track">
                <div className="sc-fill" style={{ width: `${Math.max(3, source.share)}%` }} />
              </div>
            </div>
            <div className="sc-metrics mono">
              <span>{source.mentions} mentions</span>
              <span>{source.entities} entities</span>
              <span>{source.share.toFixed(1)}%</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
