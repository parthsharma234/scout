import { useNavigate } from 'react-router-dom'
import { useEffect, useState, useRef, useCallback } from 'react'
import './HomePage.css'

/* ---- Static data ---- */

const FLOATING_NODES = [
  { entity: 'OpenAI', score: '96.4', x: 8, y: 18, size: 'lg', delay: 0 },
  { entity: 'Anthropic', score: '91.8', x: 82, y: 14, size: 'lg', delay: 0.4 },
  { entity: 'Stripe', score: '84.2', x: 68, y: 62, size: 'md', delay: 0.8 },
  { entity: 'Cursor', score: '73.1', x: 14, y: 68, size: 'md', delay: 1.2 },
  { entity: 'Databricks', score: '79.5', x: 88, y: 42, size: 'md', delay: 0.6 },
  { entity: 'Perplexity', score: '76.3', x: 5, y: 44, size: 'sm', delay: 1.0 },
  { entity: 'Groq', score: '47.8', x: 92, y: 72, size: 'sm', delay: 1.6 },
  { entity: 'Vercel', score: '68.7', x: 76, y: 82, size: 'sm', delay: 1.4 },
  { entity: 'Mistral AI', score: '65.4', x: 22, y: 86, size: 'sm', delay: 1.8 },
  { entity: 'Anduril', score: '62.8', x: 42, y: 8, size: 'sm', delay: 2.0 },
]

const CONNECTIONS = [
  [0, 5], [0, 9], [1, 4], [1, 6], [2, 4], [2, 7],
  [3, 5], [3, 8], [4, 6], [7, 8],
]

const PIPELINE_STEPS = [
  {
    num: '01',
    title: 'Ingest',
    copy: 'Agents continuously poll Hacker News, Reddit, Product Hunt, Twitter, and RSS feeds with adaptive retry logic and rate-limit awareness.',
  },
  {
    num: '02',
    title: 'Extract',
    copy: 'NLP pipelines identify startup entities, products, and key people. Entity normalization resolves aliases and keeps naming consistent across sources.',
  },
  {
    num: '03',
    title: 'Score',
    copy: 'Mention velocity, source diversity, and sentiment analysis combine into a single composite trend score across 1h, 6h, and 24h windows.',
  },
  {
    num: '04',
    title: 'Alert',
    copy: 'Spike detection flags breakout moments in real-time. The dashboard updates via WebSocket so your team can act while the information is early.',
  },
]

const PROBLEM_POINTS = [
  { text: 'Scattered across dozens of feeds and newsletters', icon: '01' },
  { text: 'No way to tell signal from noise in real-time', icon: '02' },
  { text: 'By the time you see it, everyone else has too', icon: '03' },
]

const FEATURES = [
  {
    title: 'Entity Bubbles',
    description: 'Interactive bubble chart where size = traction. See the whole market at a glance.',
    label: 'MAP',
  },
  {
    title: 'Network Drill-down',
    description: 'Click any entity to see its web of sources, keywords, and recent mentions.',
    label: 'GRAPH',
  },
  {
    title: 'Velocity Tracking',
    description: 'Monitor how fast an entity gains mentions. Catch acceleration before it peaks.',
    label: 'SPEED',
  },
  {
    title: 'Spike Detection',
    description: 'Automated alerts when velocity exceeds normal thresholds. Know when something breaks out.',
    label: 'ALERT',
  },
  {
    title: 'Funding Timeline',
    description: 'Track rounds from Seed to Series D. See how capital events correlate with traction.',
    label: 'DATA',
  },
  {
    title: 'Multi-source Coverage',
    description: 'Cross-reference signals across 5+ channels. Multi-source signals are stronger signals.',
    label: 'DEPTH',
  },
]

/* ---- Hooks ---- */

function useScrollReveal() {
  const ref = useRef(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) setVisible(true) },
      { threshold: 0.15 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  return { ref, visible }
}

/* ---- Sub-components ---- */

function FloatingNode({ node }) {
  return (
    <div
      className={`hp-float-node hp-float-node--${node.size}`}
      style={{ left: `${node.x}%`, top: `${node.y}%`, animationDelay: `${node.delay}s` }}
    >
      <span className="hp-float-dot" />
      <div className="hp-float-info">
        <span className="hp-float-name">{node.entity}</span>
        <span className="hp-float-score mono">{node.score}</span>
      </div>
    </div>
  )
}

function NodeLines() {
  return (
    <svg className="hp-lines" viewBox="0 0 100 100" preserveAspectRatio="none">
      {CONNECTIONS.map(([a, b], i) => (
        <line
          key={i}
          x1={FLOATING_NODES[a].x} y1={FLOATING_NODES[a].y}
          x2={FLOATING_NODES[b].x} y2={FLOATING_NODES[b].y}
          stroke="rgba(59,130,246,0.06)" strokeWidth="0.12" strokeDasharray="0.5 0.5"
          style={{ animationDelay: `${i * 0.15}s` }} className="hp-line"
        />
      ))}
    </svg>
  )
}

function RevealSection({ children, className = '', delay = 0 }) {
  const { ref, visible } = useScrollReveal()
  return (
    <div
      ref={ref}
      className={`hp-reveal ${visible ? 'hp-reveal--visible' : ''} ${className}`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  )
}

/* Animated counter */
function AnimatedNumber({ value, suffix = '' }) {
  const [display, setDisplay] = useState(0)
  const { ref, visible } = useScrollReveal()

  useEffect(() => {
    if (!visible) return
    const target = parseInt(value, 10)
    const duration = 1200
    const start = performance.now()
    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(Math.round(eased * target))
      if (progress < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }, [visible, value])

  return <span ref={ref}>{display}{suffix}</span>
}

/* Mock dashboard preview for the product showcase */
function DashboardPreview() {
  const { ref, visible } = useScrollReveal()
  const bubbles = [
    { name: 'OpenAI', r: 48, x: 35, y: 38, color: '#60a5fa' },
    { name: 'Anthropic', r: 42, x: 62, y: 32, color: '#60a5fa' },
    { name: 'Stripe', r: 35, x: 72, y: 60, color: '#fbbf24' },
    { name: 'Cursor', r: 30, x: 28, y: 62, color: '#60a5fa' },
    { name: 'Databricks', r: 32, x: 52, y: 65, color: '#34d399' },
    { name: 'Perplexity', r: 28, x: 18, y: 40, color: '#60a5fa' },
    { name: 'Groq', r: 22, x: 85, y: 45, color: '#60a5fa' },
    { name: 'Vercel', r: 24, x: 80, y: 75, color: '#38bdf8' },
  ]

  return (
    <div ref={ref} className={`hp-preview ${visible ? 'hp-preview--visible' : ''}`}>
      <div className="hp-preview-chrome">
        <div className="hp-preview-dots">
          <span /><span /><span />
        </div>
        <div className="hp-preview-tabs mono">
          <span className="hp-preview-tab hp-preview-tab--active">Signal Map</span>
          <span className="hp-preview-tab">Timeline</span>
          <span className="hp-preview-tab">Leaderboard</span>
        </div>
      </div>
      <div className="hp-preview-body">
        <svg viewBox="0 0 100 100" className="hp-preview-svg">
          {bubbles.map((b, i) => (
            <g key={b.name} style={{ animationDelay: `${300 + i * 80}ms` }} className="hp-preview-bubble">
              <circle cx={b.x} cy={b.y} r={b.r / 3} fill={b.color} opacity="0.08" />
              <circle cx={b.x} cy={b.y} r={b.r / 3} fill="none" stroke={b.color} strokeWidth="0.4" opacity="0.25" />
              <text x={b.x} y={b.y} textAnchor="middle" dominantBaseline="middle"
                fill="rgba(240,240,245,0.6)" fontSize="3" fontFamily="var(--font-body)" fontWeight="500">
                {b.name}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  )
}

/* ---- Main component ---- */

export default function HomePage() {
  const navigate = useNavigate()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 100)
    return () => clearTimeout(t)
  }, [])

  return (
    <div className={`hp-root ${visible ? 'hp-root--visible' : ''}`}>
      {/* Nav */}
      <header className="hp-nav-wrap">
        <nav className="hp-nav">
          <button className="hp-brand" onClick={() => navigate('/')}>
            <span className="hp-brand-icon" />
            Scout
          </button>
          <div className="hp-nav-links">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
          </div>
          <button className="hp-nav-cta" onClick={() => navigate('/dashboard')}>
            Open App <span className="hp-arrow">&rarr;</span>
          </button>
        </nav>
      </header>

      <main>
        {/* ═══ HERO ═══ */}
        <section className="hp-hero">
          <div className="hp-hero-bg"><div className="hp-gradient-wash" /></div>
          <NodeLines />
          {FLOATING_NODES.map((node) => (
            <FloatingNode key={node.entity} node={node} />
          ))}
          <div className="hp-hero-content">
            <div className="hp-hero-badge mono">
              <span className="hp-hero-badge-dot" />
              Early-stage market intelligence
            </div>
            <h1>
              Find emerging startups<br />
              <span className="hp-hero-accent">before they trend.</span>
            </h1>
            <p className="hp-hero-sub">
              Scout surfaces companies gaining traction across developer and founder
              channels — from first mention to breakout moment.
            </p>
            <div className="hp-hero-actions">
              <button className="hp-btn-primary" onClick={() => navigate('/dashboard')}>
                Open Dashboard <span className="hp-arrow">&rarr;</span>
              </button>
              <a className="hp-btn-secondary" href="#product">
                See how it works
              </a>
            </div>
          </div>
          <div className="hp-scroll-hint">
            <div className="hp-scroll-line" />
          </div>
        </section>

        {/* ═══ METRICS ═══ */}
        <section className="hp-metrics">
          <RevealSection className="hp-metrics-inner">
            <div className="hp-metric">
              <span className="hp-metric-value"><AnimatedNumber value="5" /></span>
              <span className="hp-metric-label">Live sources</span>
            </div>
            <div className="hp-metric">
              <span className="hp-metric-value"><AnimatedNumber value="30" suffix="s" /></span>
              <span className="hp-metric-label">Collection interval</span>
            </div>
            <div className="hp-metric">
              <span className="hp-metric-value"><AnimatedNumber value="16" /></span>
              <span className="hp-metric-label">Tracked entities</span>
            </div>
            <div className="hp-metric">
              <span className="hp-metric-value"><AnimatedNumber value="3" /></span>
              <span className="hp-metric-label">Velocity windows</span>
            </div>
          </RevealSection>
        </section>

        {/* ═══ PROBLEM ═══ */}
        <section className="hp-problem" id="product">
          <RevealSection>
            <span className="hp-overline mono">The problem</span>
            <h2>Startup intelligence is broken.</h2>
            <p className="hp-problem-desc">
              Every day, thousands of signals about emerging companies appear across Hacker News,
              Reddit, Twitter, and niche RSS feeds. By the time they hit your inbox, the window has closed.
            </p>
          </RevealSection>
          <div className="hp-problem-points">
            {PROBLEM_POINTS.map((p, i) => (
              <RevealSection key={i} delay={i * 120} className="hp-problem-point">
                <span className="hp-problem-num mono">{p.icon}</span>
                <p>{p.text}</p>
              </RevealSection>
            ))}
          </div>
        </section>

        {/* ═══ PRODUCT SHOWCASE ═══ */}
        <section className="hp-showcase">
          <RevealSection>
            <span className="hp-overline mono">The product</span>
            <h2>One ranked view of every signal that matters.</h2>
            <p className="hp-showcase-desc">
              Scout collects, normalizes, and scores startup mentions in real-time — then
              presents them as an interactive map where bubble size equals traction.
            </p>
          </RevealSection>
          <DashboardPreview />
        </section>

        {/* ═══ HOW IT WORKS ═══ */}
        <section className="hp-workflow" id="how">
          <RevealSection>
            <span className="hp-overline mono">How it works</span>
            <h2>From raw feeds to ranked signal in four steps.</h2>
          </RevealSection>
          <div className="hp-steps">
            {PIPELINE_STEPS.map((step, i) => (
              <RevealSection key={step.num} delay={i * 100} className="hp-step">
                <div className="hp-step-indicator">
                  <span className="hp-step-num mono">{step.num}</span>
                  {i < PIPELINE_STEPS.length - 1 && <div className="hp-step-line" />}
                </div>
                <div className="hp-step-body">
                  <h3>{step.title}</h3>
                  <p>{step.copy}</p>
                </div>
              </RevealSection>
            ))}
          </div>
        </section>

        {/* ═══ FEATURES GRID ═══ */}
        <section className="hp-features" id="features">
          <RevealSection>
            <span className="hp-overline mono">Capabilities</span>
            <h2>Everything you need to track emerging companies.</h2>
          </RevealSection>
          <div className="hp-features-grid">
            {FEATURES.map((f, i) => (
              <RevealSection key={f.title} delay={i * 60} className="hp-feature-card">
                <span className="hp-feature-label mono">{f.label}</span>
                <h3>{f.title}</h3>
                <p>{f.description}</p>
              </RevealSection>
            ))}
          </div>
        </section>

        {/* ═══ CTA ═══ */}
        <section className="hp-cta">
          <RevealSection className="hp-cta-inner">
            <h2>Start tracking emerging signals today.</h2>
            <p>
              Open the dashboard and explore real-time entity intelligence
              across the startup ecosystem.
            </p>
            <button className="hp-btn-primary hp-btn-lg" onClick={() => navigate('/dashboard')}>
              Open Dashboard <span className="hp-arrow">&rarr;</span>
            </button>
          </RevealSection>
        </section>

        {/* Footer */}
        <footer className="hp-footer">
          <div className="hp-footer-inner">
            <div className="hp-footer-brand">
              <span className="hp-brand-icon" />
              <span>Scout</span>
            </div>
            <span className="hp-footer-note mono">Real-time startup intelligence</span>
          </div>
        </footer>
      </main>
    </div>
  )
}
