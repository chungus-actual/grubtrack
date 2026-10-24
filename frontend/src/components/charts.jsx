// Hand-built SVG charts on the dataviz specs: 2px lines, >=8px dots with a 2px surface ring,
// hairline solid grid, values in text ink (never the series colour), a crosshair or per-mark
// tooltip, and every value also reachable without hovering (direct labels or a table).
import { useCallback, useMemo, useRef, useState } from 'react'
import { dollars, parseDay, perLabel, shortDate, unitText } from '../lib/format'
import { useStores } from '../lib/stores'

function niceTicks(lo, hi, count = 4) {
  if (!(hi > lo)) {
    const pad = Math.abs(lo) * 0.2 || 1
    lo -= pad
    hi += pad
  }
  const span = hi - lo
  const step0 = span / count
  const mag = 10 ** Math.floor(Math.log10(step0))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || mag * 10
  const start = Math.floor(lo / step) * step
  const ticks = []
  for (let v = start; v <= hi + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)))
  return ticks
}

const lin = (d0, d1, r0, r1) => (v) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0))

function useWidth(initial = 640) {
  const [w, setW] = useState(initial)
  const ro = useRef(null)
  const set = useCallback((el) => {
    ro.current?.disconnect()
    if (!el) return
    const now = el.getBoundingClientRect().width
    if (now) setW(Math.max(260, Math.round(now)))
    ro.current = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))))
    ro.current.observe(el)
  }, [])
  return [set, w]
}

function fmtUnit(v, label) {
  if (v === null || v === undefined) return ''
  return label === 'pkg' || label === 'ea' ? dollars(v) : unitText(v)
}

// ------------------------------------------------------------------ price history

export function PriceHistory({ points, basis }) {
  // points: [{date, v (paid, on basis), shelf (pre-discount, on basis) | null, storeId, store, verified}]
  const { color } = useStores()
  const [setRef, width] = useWidth()
  const [hover, setHover] = useState(null)
  const H = 220
  const m = { t: 16, r: 18, b: 30, l: 52 }
  const label = basis?.label

  const geo = useMemo(() => {
    if (!points.length) return null
    const days = points.map((p) => parseDay(p.date).getTime())
    let x0 = Math.min(...days)
    let x1 = Math.max(...days)
    if (x1 - x0 < 14 * 86400000) {
      const mid = (x0 + x1) / 2
      x0 = mid - 10 * 86400000
      x1 = mid + 10 * 86400000
    }
    const vals = points.flatMap((p) => [p.v, p.shelf].filter((v) => v != null))
    const ticks = niceTicks(Math.min(...vals) * 0.9, Math.max(...vals) * 1.05)
    const x = lin(x0, x1, m.l, width - m.r)
    const y = lin(ticks[0], ticks[ticks.length - 1], H - m.b, m.t)
    // x ticks: up to 5 dates spread across the range
    const xt = []
    const n = Math.min(5, Math.max(2, Math.floor((width - 80) / 110)))
    for (let i = 0; i < n; i++) xt.push(x0 + ((x1 - x0) * i) / (n - 1))
    return { x, y, ticks, xt }
  }, [points, width])

  if (!geo) return null
  const { x, y, ticks, xt } = geo
  const byStore = {}
  for (const p of points) (byStore[p.storeId || 'none'] ||= []).push(p)
  const dates = [...new Set(points.map((p) => p.date))].sort()
  const nearest = (px) => {
    let best = null
    for (const d of dates) {
      const dx = Math.abs(x(parseDay(d).getTime()) - px)
      if (!best || dx < best.dx) best = { d, dx }
    }
    return best?.d
  }

  return (
    <div className="chart" ref={setRef}>
      <svg viewBox={`0 0 ${width} ${H}`} role="img" aria-label="Price history by store">
        <g className="grid">
          {ticks.map((t) => <line key={t} x1={m.l} x2={width - m.r} y1={y(t)} y2={y(t)} />)}
        </g>
        {ticks.map((t) => (
          <text key={t} x={m.l - 8} y={y(t) + 4} textAnchor="end">{fmtUnit(t, label)}</text>
        ))}
        {xt.map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor={i === 0 ? 'start' : i === xt.length - 1 ? 'end' : 'middle'}>
            {shortDate(new Date(t).toISOString().slice(0, 10))}
          </text>
        ))}
        {Object.entries(byStore).map(([sid, ps]) => (
          ps.length > 1 && (
            <polyline key={sid} fill="none" stroke={color(sid)} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
              points={ps.map((p) => `${x(parseDay(p.date).getTime())},${y(p.v)}`).join(' ')} />
          )
        ))}
        {hover && <line x1={x(parseDay(hover).getTime())} x2={x(parseDay(hover).getTime())} y1={m.t} y2={H - m.b} stroke="var(--line-2)" />}
        {points.map((p, i) => {
          const cx = x(parseDay(p.date).getTime())
          return (
            <g key={i}>
              {p.shelf != null && p.shelf > p.v && (
                <>
                  <line x1={cx} x2={cx} y1={y(p.shelf)} y2={y(p.v)} stroke="var(--ink-3)" strokeWidth="1.5" />
                  <circle cx={cx} cy={y(p.shelf)} r="4.5" fill="var(--surface)" stroke="var(--ink-3)" strokeWidth="2" />
                </>
              )}
              <circle cx={cx} cy={y(p.v)} r="5.5" fill={color(p.storeId)} stroke="var(--surface)" strokeWidth="2" />
            </g>
          )
        })}
        <rect className="hit" x={m.l} y={0} width={Math.max(0, width - m.l - m.r)} height={H - m.b}
          onPointerMove={(e) => {
            const r = e.currentTarget.ownerSVGElement.getBoundingClientRect()
            setHover(nearest(((e.clientX - r.left) / r.width) * width))
          }}
          onPointerLeave={() => setHover(null)} />
      </svg>
      {hover && (() => {
        const at = points.filter((p) => p.date === hover)
        const left = Math.min(Math.max(x(parseDay(hover).getTime()) / width * 100, 8), 70)
        return (
          <div className="tip" style={{ left: `${left}%`, top: 8 }}>
            <div className="tip-head">{shortDate(hover, { year: true })}</div>
            {at.map((p, i) => (
              <div className="tip-row" key={i}>
                <i className="key" style={{ background: color(p.storeId) }} />
                <b>{fmtUnit(p.v, label)}</b>
                <span>{p.store || 'No receipt'}{p.shelf > p.v ? `, shelf ${fmtUnit(p.shelf, label)}` : ''}</span>
              </div>
            ))}
          </div>
        )
      })()}
      <div className="legend" style={{ marginTop: 6, marginBottom: 0 }}>
        {Object.entries(byStore).map(([sid, ps]) => (
          <span key={sid}><i className="sw" style={{ background: color(sid) }} />{ps[0].store || 'No receipt'}</span>
        ))}
        {points.some((p) => p.shelf > p.v) && <span><i className="sw sw--ring" />shelf price before the sale</span>}
        <span className="muted">{perLabel(label)}</span>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------------- spark

export function Spark({ values, width = 72, height = 22 }) {
  if (!values || values.length < 2) return null
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const x = lin(0, values.length - 1, 2, width - 4)
  const y = hi === lo ? () => height / 2 : lin(lo, hi, height - 3, 3)
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  const last = values[values.length - 1]
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke="var(--ink-3)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(values.length - 1)} cy={y(last)} r="2.6" fill="var(--ink)" />
    </svg>
  )
}

// --------------------------------------------------------------------- columns

export function Columns({ rows, height = 200, format = (v) => dollars(v, { cents: false }), onPick, picked }) {
  // rows: [{key, label, value, sub}]
  const [setRef, width] = useWidth()
  const [hover, setHover] = useState(null)
  const m = { t: 22, r: 6, b: 26, l: 6 }
  const max = Math.max(1, ...rows.map((r) => r.value || 0))
  const band = (width - m.l - m.r) / Math.max(rows.length, 1)
  const bw = Math.min(24 * 1.6, band * 0.55)
  const y = lin(0, max, height - m.b, m.t)
  return (
    <div className="chart" ref={setRef}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Spend by month">
        <line x1={m.l} x2={width - m.r} y1={height - m.b} y2={height - m.b} stroke="var(--axis)" />
        {rows.map((r, i) => {
          const cx = m.l + band * i + band / 2
          const top = y(r.value || 0)
          const h = Math.max(0, height - m.b - top)
          const rad = Math.min(4, h)
          const active = picked ? picked === r.key : true
          return (
            <g key={r.key} opacity={active ? 1 : 0.45}>
              <path d={`M${cx - bw / 2},${height - m.b} V${top + rad} Q${cx - bw / 2},${top} ${cx - bw / 2 + rad},${top} H${cx + bw / 2 - rad} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + rad} V${height - m.b} Z`}
                fill="var(--seq)" />
              <text x={cx} y={top - 7} textAnchor="middle" className="label">{format(r.value)}</text>
              <text x={cx} y={height - 8} textAnchor="middle">{r.label}</text>
              <rect className="hit" x={cx - band / 2} y={0} width={band} height={height}
                style={{ cursor: onPick ? 'pointer' : 'default' }}
                onPointerEnter={() => setHover(r)} onPointerLeave={() => setHover(null)}
                onClick={() => onPick?.(r.key)} />
            </g>
          )
        })}
      </svg>
      {hover && hover.sub && (
        <div className="tip" style={{ left: `${Math.min(70, ((rows.indexOf(hover) + 0.5) / rows.length) * 100)}%`, top: 0 }}>
          <div className="tip-head">{hover.label}</div>
          <div className="tip-row"><b>{dollars(hover.value)}</b><span>{hover.sub}</span></div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- horizontal bars

export function HBars({ rows, format = (v) => dollars(v, { cents: false }), onPick }) {
  // rows: [{key, label, value, sub}] – one hue; value at the tip
  const max = Math.max(1, ...rows.map((r) => r.value || 0))
  return (
    <div className="stack" style={{ gap: 9 }}>
      {rows.map((r) => (
        <button key={r.key} type="button" onClick={() => onPick?.(r.key)} title={r.sub || undefined}
          style={{ all: 'unset', cursor: onPick ? 'pointer' : 'default', display: 'grid', gridTemplateColumns: 'minmax(96px, 30%) 1fr', gap: 12, alignItems: 'center' }}>
          <span style={{ fontSize: 'var(--t-sm)', fontWeight: 600, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ height: 14, width: `${Math.max(1.5, (r.value / max) * 82)}%`, background: 'var(--seq)', borderRadius: '0 4px 4px 0', flex: 'none' }} />
            <span className="num" style={{ fontSize: 'var(--t-sm)', fontWeight: 700, whiteSpace: 'nowrap' }}>{format(r.value)}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

// -------------------------------------------------------------------- dumbbell

export function GapList({ rows, onPick }) {
  // rows: [{key, name, basis, prices: [{storeId, store, avg}] (cheapest first), spreadPct}]
  const { color } = useStores()
  const maxPct = Math.max(10, ...rows.map((r) => r.spreadPct || 0))
  return (
    <div className="list list--panel">
      {rows.map((r) => {
        const cheap = r.prices[0]
        const dear = r.prices[r.prices.length - 1]
        const w = (r.spreadPct / maxPct) * 100
        return (
          <button key={r.key} type="button" className="row" onClick={() => onPick?.(r.key)} style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
            <div className="spread" style={{ gap: 8 }}>
              <span className="row__title">{r.name}</span>
              <span className="row__meta num">{r.spreadPct}% apart</span>
            </div>
            <div style={{ position: 'relative', height: 18, margin: '6px 4px 2px' }} aria-hidden="true">
              <span style={{ position: 'absolute', left: 0, width: `${w}%`, top: 8, height: 2, background: 'var(--line-2)' }} />
              <span style={{ position: 'absolute', left: 0, top: 3, width: 12, height: 12, borderRadius: 6, background: color(cheap.storeId), boxShadow: '0 0 0 2px var(--surface)', transform: 'translateX(-2px)' }} />
              <span style={{ position: 'absolute', left: `calc(${w}% - 10px)`, top: 3, width: 12, height: 12, borderRadius: 6, background: color(dear.storeId), boxShadow: '0 0 0 2px var(--surface)' }} />
            </div>
            <div className="row__meta" style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
              <span><b className="num" style={{ color: 'var(--ink)' }}>{fmtUnit(cheap.avg, r.basis)}</b> {cheap.store}</span>
              <span><b className="num" style={{ color: 'var(--ink)' }}>{fmtUnit(dear.avg, r.basis)}</b> {dear.store}</span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
