import { useEffect, useMemo, useRef, useState } from 'react'
import './NodeGraph.css'

const SOURCE_COLORS = {
  hackernews: { fill: '#ff8b3d', bg: 'rgba(255,139,61,0.15)' },
  github: { fill: '#8b5cf6', bg: 'rgba(139,92,246,0.16)' },
  reddit: { fill: '#ff7340', bg: 'rgba(255,115,64,0.15)' },
  twitter: { fill: '#5bc7ff', bg: 'rgba(91,199,255,0.15)' },
  techcrunch: { fill: '#37d67a', bg: 'rgba(55,214,122,0.15)' },
  producthunt: { fill: '#ff9865', bg: 'rgba(255,152,101,0.15)' },
}

function engagementScore(node) {
  return node.interactions + node.views * 0.18
}

function hashString(value) {
  let h = 0
  for (let i = 0; i < value.length; i += 1) {
    h = (h << 5) - h + value.charCodeAt(i)
    h |= 0
  }
  return Math.abs(h)
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v))
}

function shortSource(sourceId) {
  const map = { hackernews: 'HN', github: 'GH', reddit: 'RD', twitter: 'X', techcrunch: 'TC', producthunt: 'PH' }
  return map[sourceId] ?? 'SRC'
}

function buildLayout(nodes, width, height) {
  if (!width || !height) return []
  const cx = width / 2
  const cy = height / 2
  const scale = Math.min(width, height)
  const innerCount = Math.min(8, nodes.length)
  const innerR = scale * 0.22
  const outerR = scale * 0.38
  const maxScore = Math.max(...nodes.map(engagementScore), 1)

  return nodes.map((node, i) => {
    const isInner = i < innerCount
    const ringI = isInner ? i : i - innerCount
    const ringN = isInner ? innerCount : Math.max(1, nodes.length - innerCount)
    const angle = (ringI / ringN) * Math.PI * 2 - Math.PI / 2

    const seed = hashString(node.id)
    const aJit = ((seed % 27) - 13) * 0.012
    const rJit = ((Math.floor(seed / 29) % 21) - 10) * 1.5
    const radius = (isInner ? innerR : outerR) + rJit

    const ratio = engagementScore(node) / maxScore
    const r = 14 + ratio * 20
    const colors = SOURCE_COLORS[node.source_id] ?? { fill: '#60a5fa', bg: 'rgba(96,165,250,0.15)' }

    return {
      ...node,
      x: cx + Math.cos(angle + aJit) * radius,
      y: cy + Math.sin(angle + aJit) * radius,
      r,
      score: engagementScore(node),
      colors,
      short: shortSource(node.source_id),
    }
  })
}

/* Extract meaningful keywords from a headline */
const STOP_WORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
  'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'has',
  'have', 'had', 'this', 'that', 'from', 'its', 'it', 'as', 'how', 'no',
  'not', 'all', 'can', 'will', 'do', 'does', 'did', 'about', 'into',
  'than', 'more', 'over', 'also', 'just', 'new', 'other', 'their',
])

function extractKeywords(text) {
  return (text || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter((w) => w.length > 3 && !STOP_WORDS.has(w))
}

function keywordOverlap(a, b) {
  const setA = new Set(extractKeywords(a))
  const setB = new Set(extractKeywords(b))
  let count = 0
  setA.forEach((w) => { if (setB.has(w)) count++ })
  return count
}

/* Build edges: same source type OR shared keywords/topics */
function buildEdges(layout) {
  const edges = []
  const seen = new Set()

  for (let i = 0; i < layout.length; i++) {
    for (let j = i + 1; j < layout.length; j++) {
      const a = layout[i]
      const b = layout[j]
      const key = `${a.id}|${b.id}`
      if (seen.has(key)) continue

      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy)

      // Same source type — always connect if nearby
      const sameSource = a.source_id === b.source_id
      // Shared keywords — topic similarity
      const overlap = keywordOverlap(a.headline, b.headline)

      let strength = 0
      if (sameSource && dist < 350) strength += 0.5
      if (overlap >= 2) strength += 0.3 * Math.min(overlap, 4)
      if (overlap >= 1 && dist < 250) strength += 0.2

      if (strength > 0.2) {
        seen.add(key)
        edges.push({
          a, b, dist, strength,
          // Use topic color if keyword match, source color if same source
          color: overlap >= 2 ? 'rgba(148,163,184,0.5)' : a.colors.fill,
          type: overlap >= 2 ? 'topic' : 'source',
        })
      }
    }
  }
  return edges
}

function DetailsModal({ node, onClose }) {
  if (!node) return null
  return (
    <div className="web-overlay" onClick={onClose}>
      <div className="web-modal" onClick={(e) => e.stopPropagation()}>
        <header className="web-modal-head">
          <div>
            <span className="section-label">Source Detail</span>
            <h3 className="web-modal-title">{node.headline}</h3>
          </div>
          <button type="button" className="web-modal-close" onClick={onClose}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="web-modal-meta">
          <span className="web-modal-badge" style={{ color: node.colors.fill, borderColor: `${node.colors.fill}40`, background: node.colors.bg }}>
            {node.source_name}
          </span>
          <a href={node.url} target="_blank" rel="noreferrer" className="web-modal-link">{node.url}</a>
        </div>

        <p className="web-modal-summary">{node.summary}</p>

        <div className="web-modal-stats">
          <article>
            <span className="section-label">Interactions</span>
            <strong>{node.interactions.toLocaleString()}</strong>
          </article>
          <article>
            <span className="section-label">Views</span>
            <strong>{node.views.toLocaleString()}</strong>
          </article>
          <article>
            <span className="section-label">Engagement</span>
            <strong>{Math.round(node.score).toLocaleString()}</strong>
          </article>
        </div>
      </div>
    </div>
  )
}

export default function NodeGraph({ company = '', nodes = [], loading = false }) {
  const containerRef = useRef(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [hoveredId, setHoveredId] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)

  const stableSize = useRef({ width: 0, height: 0 })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return undefined

    function measure() {
      const rect = el.getBoundingClientRect()
      const w = Math.round(rect.width)
      const h = Math.round(rect.height)
      if (w > 100 && h > 100) {
        const prev = stableSize.current
        if (prev.width === 0 || Math.abs(prev.width - w) > 20 || Math.abs(prev.height - h) > 20) {
          stableSize.current = { width: w, height: h }
          setSize({ width: w, height: h })
        }
      }
    }

    measure()
    const t1 = setTimeout(measure, 50)
    const t2 = setTimeout(measure, 200)

    const ro = new ResizeObserver(() => measure())
    ro.observe(el)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
      ro.disconnect()
    }
  }, [])

  useEffect(() => {
    setHoveredId(null)
    setSelectedNode(null)
  }, [company])

  const topNodes = useMemo(
    () => [...nodes].sort((a, b) => engagementScore(b) - engagementScore(a)).slice(0, 20),
    [nodes],
  )

  const layout = useMemo(
    () => buildLayout(topNodes, size.width, size.height),
    [topNodes, size.width, size.height],
  )

  const edges = useMemo(() => buildEdges(layout), [layout])

  const hoveredNode = layout.find((n) => n.id === hoveredId)

  const tipStyle = useMemo(() => {
    if (!hoveredNode) return null
    return {
      left: clamp(hoveredNode.x + hoveredNode.r + 14, 10, size.width - 330),
      top: clamp(hoveredNode.y - 50, 10, size.height - 130),
    }
  }, [hoveredNode, size])

  if (loading || !company || topNodes.length === 0) {
    return (
      <div className="web-root" ref={containerRef}>
        <div className="web-empty">
          <span className="section-label">
            {loading ? 'Loading...' : !company ? 'Select a company.' : 'No sources.'}
          </span>
        </div>
      </div>
    )
  }

  const cx = size.width / 2
  const cy = size.height / 2

  return (
    <div className="web-root" ref={containerRef}>
      <svg className="web-svg" width={size.width} height={size.height}>
        <defs>
          <radialGradient id="web-center-g">
            <stop offset="0%" stopColor="rgba(59,130,246,0.22)" />
            <stop offset="100%" stopColor="rgba(59,130,246,0.04)" />
          </radialGradient>
          {Object.entries(SOURCE_COLORS).map(([id, c]) => (
            <radialGradient key={id} id={`wn-${id}`}>
              <stop offset="0%" stopColor={c.fill} stopOpacity="0.85" />
              <stop offset="100%" stopColor={c.fill} stopOpacity="0.35" />
            </radialGradient>
          ))}
        </defs>

        {/* Hub lines */}
        {layout.map((n) => (
          <line
            key={`l-${n.id}`}
            x1={cx} y1={cy} x2={n.x} y2={n.y}
            stroke={hoveredId === n.id ? 'rgba(148,163,184,0.35)' : 'rgba(148,163,184,0.08)'}
            strokeWidth={hoveredId === n.id ? 1.2 : 0.6}
            strokeDasharray={hoveredId === n.id ? 'none' : '2 4'}
          />
        ))}

        {/* Inter-node connections (source + topic based) */}
        {edges.map(({ a, b, dist, strength, color, type }) => {
          const baseOpacity = Math.min(0.25, strength * 0.2)
          const isHighlight = hoveredId === a.id || hoveredId === b.id
          return (
            <line
              key={`e-${a.id}-${b.id}`}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={isHighlight ? (type === 'topic' ? '#94a3b8' : a.colors.fill) : color}
              strokeWidth={isHighlight ? 1.5 : type === 'topic' ? 0.8 : 0.6}
              strokeOpacity={isHighlight ? 0.5 : baseOpacity}
              strokeDasharray={type === 'topic' ? '3 3' : 'none'}
            />
          )
        })}

        {/* Center hub */}
        <circle cx={cx} cy={cy} r="50" fill="url(#web-center-g)" stroke="rgba(96,165,250,0.4)" strokeWidth="1.2" />
        <text x={cx} y={cy - 3} textAnchor="middle" fill="var(--text-primary)" fontFamily="var(--font-display)" fontSize="16" fontWeight="700">
          {company}
        </text>
        <text x={cx} y={cy + 13} textAnchor="middle" fill="var(--text-muted)" fontFamily="var(--font-mono)" fontSize="8" letterSpacing="0.1em">
          SOURCE WEB
        </text>

        {/* Nodes */}
        {layout.map((n, i) => {
          const isH = hoveredId === n.id
          const gradId = `wn-${n.source_id}`
          const hasDef = !!SOURCE_COLORS[n.source_id]
          return (
            <g
              key={n.id}
              className="web-node"
              style={{ animationDelay: `${i * 28}ms` }}
              onMouseEnter={() => setHoveredId(n.id)}
              onMouseLeave={() => setHoveredId(null)}
              onClick={() => setSelectedNode(n)}
            >
              {isH && (
                <circle cx={n.x} cy={n.y} r={n.r + 5} fill="none" stroke={n.colors.fill} strokeWidth="1.2" strokeOpacity="0.45" />
              )}
              <circle
                cx={n.x} cy={n.y} r={n.r}
                fill={hasDef ? `url(#${gradId})` : n.colors.fill}
                stroke={isH ? n.colors.fill : 'rgba(255,255,255,0.04)'}
                strokeWidth={isH ? 1.5 : 0.6}
              />
              <text
                x={n.x} y={n.y + 3.5}
                textAnchor="middle" fill="#fff"
                fontFamily="var(--font-mono)" fontSize={n.r > 26 ? 11 : 9}
                fontWeight="700" letterSpacing="0.06em"
                style={{ pointerEvents: 'none' }}
              >
                {n.short}
              </text>
            </g>
          )
        })}
      </svg>

      {hoveredNode && tipStyle && (
        <div className="web-tip" style={tipStyle}>
          <p className="web-tip-title">{hoveredNode.headline}</p>
          <div className="web-tip-meta">
            <span style={{ color: hoveredNode.colors.fill }}>{hoveredNode.source_name}</span>
            <span className="mono">{hoveredNode.interactions.toLocaleString()} interactions</span>
          </div>
          <span className="web-tip-url">{hoveredNode.url}</span>
        </div>
      )}

      <div className="web-legend">
        {Object.entries(SOURCE_COLORS).map(([id, c]) => (
          <span key={id} className="web-legend-item">
            <span className="web-legend-dot" style={{ background: c.fill }} />
            {shortSource(id)}
          </span>
        ))}
        <span className="web-legend-count mono">{topNodes.length} sources</span>
      </div>

      <DetailsModal node={selectedNode} onClose={() => setSelectedNode(null)} />
    </div>
  )
}
