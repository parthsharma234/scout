import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './VelocityChart.css'

const TIME_WINDOWS = ['1h', '6h', '24h']
const WINDOW_POINTS = { '1h': 12, '6h': 36, '24h': 72 }

const ENTITY_COLORS = [
  '#60A5FA',
  '#3B82F6',
  '#2563EB',
  '#38BDF8',
  '#0EA5E9',
  '#1D4ED8',
]

function EmptyChart() {
  return (
    <div className="vc-empty">
      <p>No velocity history yet.</p>
      <span>Chart data appears once the first intervals are collected.</span>
    </div>
  )
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null

  return (
    <div className="vc-tooltip">
      <p className="vc-tooltip-time mono">{label}</p>
      {payload.map((point) => (
        <div key={point.dataKey} className="vc-tooltip-row">
          <span className="vc-tooltip-dot" style={{ backgroundColor: point.color }} />
          <span className="vc-tooltip-name">{point.dataKey}</span>
          <span className="vc-tooltip-value mono">{Number(point.value ?? 0).toFixed(1)}</span>
        </div>
      ))}
    </div>
  )
}

export default function VelocityChart({
  data = [],
  entities = [],
  loading = false,
  window = '1h',
  onWindowChange,
}) {
  const hasData = data.length > 0 && entities.length > 0
  const visiblePoints = WINDOW_POINTS[window] ?? WINDOW_POINTS['1h']
  const chartData = data.slice(-visiblePoints)

  return (
    <section className="vc-root card">
      <header className="vc-header">
        <p className="section-label">Velocity Over Time</p>
        <div className="vc-controls">
          {TIME_WINDOWS.map((item) => (
            <button
              key={item}
              type="button"
              className={`vc-time-btn mono ${window === item ? 'vc-time-btn--active' : ''}`}
              onClick={() => onWindowChange?.(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </header>

      <div className="vc-body">
        {loading && <div className="vc-loading" />}

        {!loading && !hasData && <EmptyChart />}

        {!loading && hasData && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 6, right: 16, left: -16, bottom: 4 }}>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.12)" vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: '#6f7f98', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                axisLine={{ stroke: 'rgba(148, 163, 184, 0.12)' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#6f7f98', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                axisLine={false}
                tickLine={false}
                width={34}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{
                  paddingTop: '10px',
                  fontFamily: 'IBM Plex Mono',
                  fontSize: '10px',
                  color: '#a7b4c8',
                }}
              />
              {entities.map((entity, index) => (
                <Line
                  key={entity}
                  dataKey={entity}
                  type="monotone"
                  stroke={ENTITY_COLORS[index % ENTITY_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 3 }}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  )
}
