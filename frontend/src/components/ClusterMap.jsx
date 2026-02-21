import { useEffect, useMemo, useRef, useState } from 'react'
import './ClusterMap.css'

const CATEGORY = {
  OpenAI: 'ai', Anthropic: 'ai', 'Mistral AI': 'ai', Groq: 'ai',
  Cursor: 'devtools', Perplexity: 'ai', 'Scale AI': 'ai',
  Databricks: 'ai', Runway: 'media', Cohere: 'ai',
  Stripe: 'fintech', Vercel: 'devtools', Supabase: 'devtools',
  Linear: 'devtools', Replit: 'devtools', Warp: 'devtools',
  Anduril: 'defense', SpaceX: 'defense', Figma: 'devtools',
  Notion: 'devtools', Rippling: 'fintech', Plaid: 'fintech',
  Ramp: 'fintech', Cloudflare: 'devtools', ElevenLabs: 'ai',
  Neon: 'devtools', Resend: 'devtools', Midjourney: 'media',
  'Hugging Face': 'ai', 'Together AI': 'ai',
}

const CAT_COLOR = {
  ai: '#3b82f6',
  devtools: '#38bdf8',
  fintech: '#fbbf24',
  defense: '#4ade80',
  media: '#f472b6',
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

function getWorldBounds(circles) {
  if (!circles.length) {
    return { minX: 0, maxX: 0, minY: 0, maxY: 0 }
  }

  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity

  circles.forEach((circle) => {
    minX = Math.min(minX, circle.x - circle.r)
    maxX = Math.max(maxX, circle.x + circle.r)
    minY = Math.min(minY, circle.y - circle.r)
    maxY = Math.max(maxY, circle.y + circle.r)
  })

  return { minX, maxX, minY, maxY }
}

function fitAndClampCircles(circles, width, height) {
  if (!circles.length || !width || !height) return circles

  const padding = 22
  const cx = width / 2
  const cy = height / 2

  const bounds = getWorldBounds(circles)
  const clusterW = bounds.maxX - bounds.minX
  const clusterH = bounds.maxY - bounds.minY
  const fitW = Math.max(1, width - padding * 2)
  const fitH = Math.max(1, height - padding * 2)

  if (clusterW > fitW || clusterH > fitH) {
    const scale = Math.min(fitW / clusterW, fitH / clusterH, 1)
    circles.forEach((circle) => {
      circle.x = cx + (circle.x - cx) * scale
      circle.y = cy + (circle.y - cy) * scale
      circle.r *= scale
    })
  }

  circles.forEach((circle) => {
    circle.x = clamp(circle.x, circle.r + padding, width - circle.r - padding)
    circle.y = clamp(circle.y, circle.r + padding, height - circle.r - padding)
  })

  return circles
}

/* Place biggest circles first and find nearest open spot. */
function packCircles(items, width, height) {
  if (!items.length || !width || !height) return []

  const cx = width / 2
  const cy = height / 2
  const maxDim = Math.min(width, height)

  const sorted = [...items].sort((a, b) => b.trend_score - a.trend_score)
  const topScore = sorted[0].trend_score
  const minScore = sorted[sorted.length - 1].trend_score

  const circles = sorted.map((item) => {
    const t = topScore === minScore ? 0.5 : (item.trend_score - minScore) / (topScore - minScore)
    const r = maxDim * (0.035 + t * 0.085)
    const cat = CATEGORY[item.entity] ?? 'ai'
    const color = CAT_COLOR[cat] ?? CAT_COLOR.ai
    return { ...item, r, cat, color, x: cx, y: cy }
  })

  circles[0].x = cx
  circles[0].y = cy

  for (let i = 1; i < circles.length; i += 1) {
    const current = circles[i]
    let bestX = cx
    let bestY = cy
    let bestDist = Infinity

    for (let j = 0; j < i; j += 1) {
      const placed = circles[j]
      const touchDist = placed.r + current.r + 4

      for (let a = 0; a < 36; a += 1) {
        const angle = (a / 36) * Math.PI * 2
        const tx = placed.x + Math.cos(angle) * touchDist
        const ty = placed.y + Math.sin(angle) * touchDist

        let overlaps = false
        for (let k = 0; k < i; k += 1) {
          const other = circles[k]
          const dx = tx - other.x
          const dy = ty - other.y
          if (Math.sqrt(dx * dx + dy * dy) < other.r + current.r + 3) {
            overlaps = true
            break
          }
        }

        if (!overlaps) {
          const distToCenter = Math.sqrt((tx - cx) ** 2 + (ty - cy) ** 2)
          if (distToCenter < bestDist) {
            bestDist = distToCenter
            bestX = tx
            bestY = ty
          }
        }
      }
    }

    current.x = bestX
    current.y = bestY
  }

  const bounds = getWorldBounds(circles)
  const clusterCx = (bounds.minX + bounds.maxX) / 2
  const clusterCy = (bounds.minY + bounds.maxY) / 2
  const offsetX = cx - clusterCx
  const offsetY = cy - clusterCy

  circles.forEach((circle) => {
    circle.x += offsetX
    circle.y += offsetY
  })

  return fitAndClampCircles(circles, width, height)
}

function clampCamera(camera, circles, renderSize) {
  const width = renderSize.width
  const height = renderSize.height
  if (!width || !height || !circles.length) return { x: 0, y: 0, zoom: camera.zoom }

  const margin = 14
  const bounds = getWorldBounds(circles)
  const worldW = bounds.maxX - bounds.minX
  const worldH = bounds.maxY - bounds.minY
  const zoom = camera.zoom

  let x = camera.x
  let y = camera.y

  const scaledW = worldW * zoom
  const scaledH = worldH * zoom

  if (scaledW <= width - margin * 2) {
    const centerX = (bounds.minX + bounds.maxX) / 2
    x = width / 2 - centerX * zoom
  } else {
    const minX = (width - margin) - bounds.maxX * zoom
    const maxX = margin - bounds.minX * zoom
    x = clamp(x, minX, maxX)
  }

  if (scaledH <= height - margin * 2) {
    const centerY = (bounds.minY + bounds.maxY) / 2
    y = height / 2 - centerY * zoom
  } else {
    const minY = (height - margin) - bounds.maxY * zoom
    const maxY = margin - bounds.minY * zoom
    y = clamp(y, minY, maxY)
  }

  return { x, y, zoom }
}

export default function ClusterMap({
  trends = [],
  loading = false,
  onSelectEntity,
  selectedEntity,
}) {
  const containerRef = useRef(null)
  const [renderSize, setRenderSize] = useState({ width: 0, height: 0 })
  const [hovered, setHovered] = useState(null)
  const [camera, setCamera] = useState({ x: 0, y: 0, zoom: 1 })
  const [isPanning, setIsPanning] = useState(false)
  const layoutRef = useRef({ key: '', circles: [] })
  const [layoutVersion, setLayoutVersion] = useState(0)
  const cameraInitRef = useRef(false)
  const cameraRef = useRef(camera)
  const panRef = useRef(null)
  const movedRef = useRef(false)
  cameraRef.current = camera

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined

    const measure = () => {
      const rect = element.getBoundingClientRect()
      const width = Math.round(rect.width)
      const height = Math.round(rect.height)
      if (width > 120 && height > 120) {
        setRenderSize({ width, height })
      }
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)

    return () => observer.disconnect()
  }, [])

  const entityKey = useMemo(
    () => trends.map((trend) => trend.entity).sort().join('|'),
    [trends]
  )

  useEffect(() => {
    if (!trends.length || !renderSize.width || !renderSize.height) return

    const nextLayoutKey = `${entityKey}|${renderSize.width}x${renderSize.height}`
    if (layoutRef.current.key === nextLayoutKey) return

    const packed = packCircles(trends, renderSize.width, renderSize.height)
    layoutRef.current = {
      key: nextLayoutKey,
      circles: packed.map((circle) => ({
        entity: circle.entity,
        x: circle.x,
        y: circle.y,
        r: circle.r,
        cat: circle.cat,
        color: circle.color,
      })),
    }
    cameraInitRef.current = false
    setLayoutVersion((value) => value + 1)
  }, [entityKey, trends, renderSize.width, renderSize.height])

  const circles = useMemo(() => {
    if (!layoutRef.current.circles.length) return []
    const trendLookup = new Map(trends.map((trend) => [trend.entity, trend]))
    return layoutRef.current.circles
      .map((circle) => {
        const trend = trendLookup.get(circle.entity)
        if (!trend) return null
        return {
          ...trend,
          x: circle.x,
          y: circle.y,
          r: circle.r,
          cat: circle.cat,
          color: circle.color,
        }
      })
      .filter(Boolean)
  }, [trends, layoutVersion])

  useEffect(() => {
    if (!circles.length) return
    setCamera((previous) => {
      const base = cameraInitRef.current ? previous : { x: 0, y: 0, zoom: 1 }
      const next = clampCamera(base, circles, renderSize)
      cameraInitRef.current = true
      if (
        Math.abs(next.x - previous.x) < 0.01 &&
        Math.abs(next.y - previous.y) < 0.01 &&
        Math.abs(next.zoom - previous.zoom) < 0.0001
      ) {
        return previous
      }
      return next
    })
  }, [circles, renderSize])

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined

    const onWheel = (event) => {
      event.preventDefault()

      if (!circles.length) return

      const factor = event.deltaY > 0 ? 0.92 : 1.08
      const rect = element.getBoundingClientRect()
      const mouseX = event.clientX - rect.left
      const mouseY = event.clientY - rect.top

      setCamera((previous) => {
        const zoom = clamp(previous.zoom * factor, 0.8, 2.2)
        const worldX = (mouseX - previous.x) / previous.zoom
        const worldY = (mouseY - previous.y) / previous.zoom

        const next = {
          zoom,
          x: mouseX - worldX * zoom,
          y: mouseY - worldY * zoom,
        }

        return clampCamera(next, circles, renderSize)
      })
    }

    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [circles, renderSize])

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined

    const getPoint = (event) => {
      const rect = element.getBoundingClientRect()
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      }
    }

    const onPointerDown = (event) => {
      if (event.button !== 0) return
      if (cameraRef.current.zoom <= 1.01) return

      const point = getPoint(event)
      panRef.current = {
        startX: point.x,
        startY: point.y,
        camX: cameraRef.current.x,
        camY: cameraRef.current.y,
        pointerId: event.pointerId,
      }
      movedRef.current = false
      setIsPanning(true)
      element.setPointerCapture(event.pointerId)
      event.preventDefault()
    }

    const onPointerMove = (event) => {
      const pan = panRef.current
      if (!pan) return
      const point = getPoint(event)
      const dx = point.x - pan.startX
      const dy = point.y - pan.startY
      if (Math.abs(dx) + Math.abs(dy) > 2) movedRef.current = true

      setCamera((previous) =>
        clampCamera(
          {
            x: pan.camX + dx,
            y: pan.camY + dy,
            zoom: previous.zoom,
          },
          circles,
          renderSize
        )
      )
    }

    const endPan = () => {
      setIsPanning(false)
      panRef.current = null
    }

    element.addEventListener('pointerdown', onPointerDown)
    element.addEventListener('pointermove', onPointerMove)
    element.addEventListener('pointerup', endPan)
    element.addEventListener('pointercancel', endPan)
    element.addEventListener('pointerleave', endPan)

    return () => {
      element.removeEventListener('pointerdown', onPointerDown)
      element.removeEventListener('pointermove', onPointerMove)
      element.removeEventListener('pointerup', endPan)
      element.removeEventListener('pointercancel', endPan)
      element.removeEventListener('pointerleave', endPan)
    }
  }, [circles, renderSize])

  const tooltipStyle = useMemo(() => {
    if (!hovered) return null
    const circle = circles.find((item) => item.entity === hovered)
    if (!circle) return null

    const sx = circle.x * camera.zoom + camera.x
    const sy = circle.y * camera.zoom + camera.y
    const radius = circle.r * camera.zoom

    return {
      left: Math.min(sx + radius + 14, Math.max(12, renderSize.width - 210)),
      top: Math.max(8, sy - 44),
    }
  }, [hovered, circles, camera, renderSize.width])

  if (loading) {
    return (
      <div className="cm-root" ref={containerRef}>
        <div className="cm-empty"><span className="section-label">Loading clusters...</span></div>
      </div>
    )
  }

  const transform = `translate(${camera.x}, ${camera.y}) scale(${camera.zoom})`

  return (
    <div
      className={`cm-root ${camera.zoom > 1.01 ? 'cm-root--pan-enabled' : ''} ${isPanning ? 'cm-root--panning' : ''}`}
      ref={containerRef}
    >
      <svg
        className="cm-svg"
        width={renderSize.width}
        height={renderSize.height}
        viewBox={`0 0 ${renderSize.width} ${renderSize.height}`}
      >
        <g transform={transform}>
          <defs>
            {circles.map((circle) => (
              <radialGradient key={`g-${circle.entity}`} id={`cg-${circle.entity.replace(/\s/g, '-')}`}>
                <stop offset="0%" stopColor={circle.color} stopOpacity="0.9" />
                <stop offset="100%" stopColor={circle.color} stopOpacity="0.4" />
              </radialGradient>
            ))}
          </defs>

          {circles.map((a, i) => circles.slice(i + 1).map((b) => {
            if (a.cat !== b.cat) return null
            const dx = b.x - a.x
            const dy = b.y - a.y
            const dist = Math.sqrt(dx * dx + dy * dy)
            const threshold = a.r + b.r + 120
            if (dist > threshold) return null
            const opacity = Math.max(0, 0.12 - (dist / threshold) * 0.12)

            return (
              <line
                key={`l-${a.entity}-${b.entity}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={a.color}
                strokeWidth="0.8"
                strokeOpacity={opacity}
              />
            )
          }))}

          {circles.map((circle, index) => {
            const isHovered = hovered === circle.entity
            const dimmed = hovered && !isHovered
            const isSelected = selectedEntity === circle.entity

            return (
              <g
                key={circle.entity}
                className="cm-bubble"
                style={{
                  animationDelay: `${index * 30}ms`,
                  opacity: dimmed ? 0.25 : 1,
                }}
                onMouseEnter={() => setHovered(circle.entity)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => {
                  if (movedRef.current) return
                  onSelectEntity?.(circle.entity)
                }}
              >
                {(isHovered || isSelected) && (
                  <circle
                    cx={circle.x}
                    cy={circle.y}
                    r={circle.r + 6}
                    fill="none"
                    stroke={circle.color}
                    strokeWidth="1.5"
                    strokeOpacity="0.45"
                  />
                )}

                <circle
                  cx={circle.x}
                  cy={circle.y}
                  r={circle.r}
                  fill={`url(#cg-${circle.entity.replace(/\s/g, '-')})`}
                  stroke={isHovered || isSelected ? circle.color : 'rgba(255,255,255,0.06)'}
                  strokeWidth={isHovered || isSelected ? 2 : 0.8}
                />

                <circle
                  cx={circle.x}
                  cy={circle.y - circle.r * 0.25}
                  r={circle.r * 0.55}
                  fill="rgba(255,255,255,0.04)"
                  style={{ pointerEvents: 'none' }}
                />

                {circle.r >= 26 && (
                  <>
                    <text
                      x={circle.x}
                      y={circle.y - (circle.r > 40 ? 6 : 1)}
                      textAnchor="middle"
                      fill="#fff"
                      fontFamily="var(--font-display)"
                      fontSize={Math.min(14, Math.max(9, circle.r * 0.28))}
                      fontWeight="600"
                      letterSpacing="-0.01em"
                      style={{ pointerEvents: 'none' }}
                    >
                      {circle.r > 40 ? circle.entity : (circle.entity.length > 10 ? circle.entity.split(' ')[0] : circle.entity)}
                    </text>
                    {circle.r > 40 && (
                      <text
                        x={circle.x}
                        y={circle.y + 11}
                        textAnchor="middle"
                        fill="rgba(255,255,255,0.45)"
                        fontFamily="var(--font-mono)"
                        fontSize="9"
                        letterSpacing="0.04em"
                        style={{ pointerEvents: 'none' }}
                      >
                        {circle.trend_score.toFixed(1)}
                      </text>
                    )}
                  </>
                )}

                {circle.r < 26 && circle.r >= 16 && (
                  <text
                    x={circle.x}
                    y={circle.y + 3}
                    textAnchor="middle"
                    fill="#fff"
                    fontFamily="var(--font-display)"
                    fontSize="8"
                    fontWeight="600"
                    style={{ pointerEvents: 'none' }}
                  >
                    {circle.entity.slice(0, 4)}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>

      {hovered && tooltipStyle && (() => {
        const circle = circles.find((item) => item.entity === hovered)
        if (!circle) return null
        const trend = trends.find((item) => item.entity === hovered)

        return (
          <div className="cm-tip" style={tooltipStyle}>
            <div className="cm-tip-head">
              <span className="cm-tip-dot" style={{ background: circle.color }} />
              <strong>{circle.entity}</strong>
            </div>
            <div className="cm-tip-row">
              <span>Score</span>
              <span>{circle.trend_score.toFixed(1)}</span>
            </div>
            {trend && (
              <>
                <div className="cm-tip-row">
                  <span>Mentions/h</span>
                  <span>{trend.mention_count_1h}</span>
                </div>
                <div className="cm-tip-row">
                  <span>Sources</span>
                  <span>{trend.sources?.length ?? 0}</span>
                </div>
              </>
            )}
            <div className="cm-tip-hint">Click a bubble to open source web</div>
          </div>
        )
      })()}

      <div className="cm-legend">
        {Object.entries(CAT_COLOR).map(([key, color]) => (
          <span key={key} className="cm-legend-item">
            <span className="cm-legend-dot" style={{ background: color }} />
            {key}
          </span>
        ))}
      </div>

      <div className="cm-controls">
        <button
          type="button"
          className="cm-ctrl-btn"
          onClick={() => setCamera((previous) => clampCamera({ ...previous, zoom: clamp(previous.zoom * 1.25, 0.8, 2.2) }, circles, renderSize))}
          title="Zoom in"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 3v8M3 7h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        </button>
        <button
          type="button"
          className="cm-ctrl-btn"
          onClick={() => setCamera((previous) => clampCamera({ ...previous, zoom: clamp(previous.zoom * 0.8, 0.8, 2.2) }, circles, renderSize))}
          title="Zoom out"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 7h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        </button>
        <button
          type="button"
          className="cm-ctrl-btn"
          onClick={() => setCamera(clampCamera({ x: 0, y: 0, zoom: 1 }, circles, renderSize))}
          title="Reset view"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="2" y="2" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2" fill="none" /></svg>
        </button>
      </div>
    </div>
  )
}
