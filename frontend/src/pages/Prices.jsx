import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { get, patch } from '../lib/api'
import { monthName, pct, perLabel, plural, shortDate } from '../lib/format'
import { useSheet } from '../components/Sheet'
import { Delta, Empty, ErrorBox, Loading, Money, UnitBox } from '../components/bits'
import { GapList, Spark } from '../components/charts'
import { CategorySelect, money } from '../sheets/ProductSheet'
import { useToast } from '../components/Toasts'

const SORTS = { recent: 'Recently bought', name: 'A to Z', bought: 'Most bought', moved: 'Biggest moves' }

function headline(data) {
  const idx = data.storeIndex
  const ids = Object.keys(idx.stores)
  const parts = []
  if (ids.length >= 2 && idx.products.length) {
    const names = {}
    for (const p of idx.products) for (const s of p.prices) names[s.storeId] = s.store
    const sorted = ids.map((id) => ({ id, ...idx.stores[id] })).sort((a, b) => a.index - b.index)
    const lo = sorted[0]
    const hi = sorted[sorted.length - 1]
    const gap = Math.round((1 - lo.index / hi.index) * 100)
    parts.push(
      <span key="g"><b>{names[lo.id]}</b> runs <b>{gap}% cheaper</b> than {names[hi.id]} on the {plural(Math.min(lo.shared, hi.shared), 'thing')} you've bought at both. </span>,
    )
  }
  const inf = data.inflation
  if (inf.pairs.length) {
    const since = inf.pairs.reduce((m, p) => (p.from.date < m ? p.from.date : m), inf.pairs[0].from.date)
    const moved = inf.pairs.filter((p) => p.pct !== 0).length
    parts.push(
      <span key="i">Shelf prices on {plural(inf.pairs.length, 'repeat buy')} moved <b>{pct(inf.basketPct, { digits: 1 })}</b> since {monthName(since.slice(0, 7), true)}{moved ? `; ${moved} changed, the rest held.` : ', none changed.'}</span>,
    )
  }
  return parts.length ? parts : null
}

function ReviewQueue() {
  const { data } = useQuery({ queryKey: ['review'], queryFn: () => get('/products/review') })
  const qc = useQueryClient()
  const toast = useToast()
  const { open } = useSheet()
  const [picks, setPicks] = useState({})
  if (!data?.products?.length) return null
  return (
    <section className="panel">
      <div className="section-head"><h2 className="section-title">Needs a category</h2><span className="section-sub">{data.products.length} unsure</span></div>
      <div className="stack" style={{ gap: 12 }}>
        {data.products.map((p) => (
          <div key={p.id} className="stack" style={{ gap: 6 }}>
            <div className="spread" style={{ gap: 6 }}>
              <button className="btn btn--ghost btn--sm" style={{ paddingLeft: 0 }} onClick={() => open('product', p.id)}><b>{p.name}</b></button>
              {p.raw && <span className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{p.raw}</span>}
            </div>
            <div className="hstack" style={{ flexWrap: 'nowrap' }}>
              <CategorySelect value={picks[p.id] || p.cat} taxonomy={data.taxonomy} onChange={(v) => setPicks({ ...picks, [p.id]: v })} />
              <button className="btn btn--sm btn--ink" onClick={async () => {
                await patch(`/products/${p.id}`, { cat: picks[p.id] || p.cat })
                qc.invalidateQueries()
                toast(`${p.name} filed`)
              }}>Save</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function BookRow({ p, onOpen }) {
  const b = p.basis?.label
  const last = p.last
  return (
    <button className="row" onClick={onOpen} style={{ gridTemplateColumns: 'minmax(0,1fr) auto', paddingBlock: 12 }}>
      <span style={{ minWidth: 0 }}>
        <span className="row__title">{p.name}</span>
        <span className="row__meta" style={{ display: 'block' }}>
          {last.store || 'No receipt'}, {shortDate(last.date)}{p.trips > 1 ? `, bought ${p.trips}×` : ''}
          {p.change && p.change.pct ? <> <Delta value={p.change.pct} title={`Shelf price at ${p.change.store}`} /></> : null}
          {p.deal ? <> <span className="pill pill--yellow">{p.deal}% off</span></> : null}
        </span>
      </span>
      <span className="row__aside" style={{ gap: 12 }}>
        <span className="hide-sm"><Spark values={(p.spark || []).map((s) => s.v)} /></span>
        {!p.basis ? <UnitBox none="weight?" />
          : last.unitLabel && last.unitLabel !== 'pkg' ? <UnitBox value={last.unit} label={last.unitLabel} />
          : <span className="unitbox-spacer" aria-hidden="true" />}
        <Money value={last.price} size="sm" />
      </span>
    </button>
  )
}

export default function Prices() {
  const { open } = useSheet()
  const { data, error, isLoading } = useQuery({ queryKey: ['prices'], queryFn: () => get('/prices') })
  const [q, setQ] = useState('')
  const [dept, setDept] = useState('all')
  const [sort, setSort] = useState('recent')
  const [allGaps, setAllGaps] = useState(false)

  const deps = data?.taxonomy?.departments || []
  const book = useMemo(() => {
    let xs = (data?.book || []).filter((p) => (dept === 'all' || p.dept === dept))
    const qn = q.trim().toLowerCase()
    if (qn) xs = xs.filter((p) => `${p.name} ${p.brand || ''} ${p.generic || ''} ${p.catLabel}`.toLowerCase().includes(qn))
    const by = {
      recent: (a, b) => b.last.date.localeCompare(a.last.date) || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
      bought: (a, b) => b.n - a.n || a.name.localeCompare(b.name),
      moved: (a, b) => Math.abs(b.change?.pct || 0) - Math.abs(a.change?.pct || 0) || (b.storeGap?.pct || 0) - (a.storeGap?.pct || 0),
    }[sort]
    return [...xs].sort(by)
  }, [data, q, dept, sort])

  if (isLoading) return <Loading label="Reading the price book" />
  if (error) return <ErrorBox error={error} />
  const ov = data.overview.prices
  const moved = data.movers.moved
  const gaps = data.storeIndex.products
  const deptCounts = {}
  for (const p of data.book) deptCounts[p.dept] = (deptCounts[p.dept] || 0) + 1
  const lead = headline(data)

  return (
    <div>
      <div className="page-head"><h1 className="page-title">Prices</h1></div>
      {lead ? <p className="lede">{lead}</p> : <p className="lede">Prices appear here as receipts are read.</p>}

      <div className="cols cols--2 section">
        <div className="stack" style={{ gap: 28 }}>
          {gaps.length > 0 && (
            <section>
              <div className="section-head">
                <h2 className="section-title">Where it's cheaper</h2>
              </div>
              <GapList rows={(allGaps ? gaps : gaps.slice(0, 8)).map((g) => ({ key: g.productId, name: g.name, basis: g.basis, prices: g.prices, spreadPct: g.spreadPct }))}
                onPick={(id) => open('product', id)} />
              {gaps.length > 8 && (
                <button className="btn btn--sm btn--ghost" style={{ marginTop: 8 }} onClick={() => setAllGaps(!allGaps)}>
                  {allGaps ? 'Show the widest 8' : `Show all ${gaps.length}`}
                </button>
              )}
            </section>
          )}
          <section>
            <div className="section-head">
              <h2 className="section-title">What moved</h2>
            </div>
            {moved.length ? (
              <div className="list list--panel">
                {moved.map((p) => (
                  <button key={p.id} className="row" onClick={() => open('product', p.id)}>
                    <span><span className="row__title">{p.name}</span>
                      <span className="row__meta" style={{ display: 'block' }}>{p.change.store}, {shortDate(p.change.fromDate)} to {shortDate(p.change.toDate)}</span></span>
                    <span className="row__aside">
                      <span className="num soft">{money(p.change.from, p.basis?.label)} to <b style={{ color: 'var(--ink)' }}>{money(p.change.to, p.basis?.label)}</b> {perLabel(p.basis?.label)}</span>
                      <Delta value={p.change.pct} digits={1} />
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <Empty title="Nothing has moved yet">A product needs two visits to the same store before a change can show.</Empty>
            )}
            {data.movers.steady > 0 && <p className="note" style={{ marginTop: 8 }}>{plural(data.movers.steady, 'other repeat buy')} held the same shelf price.</p>}
          </section>
        </div>
        <div className="stack" style={{ gap: 28 }}>
          <ReviewQueue />
          {data.cadence.length > 0 && (
            <section className="panel">
              <div className="section-head"><h2 className="section-title">Buying rhythm</h2></div>
              <div className="list" style={{ margin: '0 -18px -18px' }}>
                {data.cadence.slice(0, 8).map((c) => (
                  <button key={c.productId} className="row" onClick={() => open('product', c.productId)} style={{ minHeight: 48 }}>
                    <span className="row__title">{c.name}</span>
                    <span className="muted num" style={{ fontSize: 'var(--t-sm)', textAlign: 'right' }}>
                      {c.status === 'learning' ? `${c.gaps[0]} days apart` : `every ~${Math.round(c.every)} days, due ${shortDate(c.next)}`}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      <section className="section" aria-labelledby="book">
        <div className="section-head"><h2 className="section-title" id="book">Price book</h2><span className="section-sub">{book.length} of {data.book.length}</span></div>
        <div className="toolbar">
          <label className="search">
            <Search aria-hidden="true" />
            <span className="sr-only">Search prices</span>
            <input className="input" type="search" placeholder="Search the price book" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <label className="hstack" style={{ gap: 8 }}>
            <span className="muted" style={{ fontSize: 'var(--t-sm)' }}>Sort</span>
            <select className="select" style={{ width: 'auto' }} value={sort} onChange={(e) => setSort(e.target.value)}>
              {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
        <div className="chips chips--scroll" role="group" aria-label="Department" style={{ marginBottom: 12 }}>
          <button className="chip" aria-pressed={dept === 'all'} onClick={() => setDept('all')}>All</button>
          {deps.filter((d) => deptCounts[d.key]).map((d) => (
            <button key={d.key} className="chip" aria-pressed={dept === d.key} onClick={() => setDept(d.key)}>{d.label} <span className="n">{deptCounts[d.key]}</span></button>
          ))}
        </div>
        {book.length ? (
          <div className="list list--panel">
            {book.map((p) => <BookRow key={p.id} p={p} onOpen={() => open('product', p.id)} />)}
          </div>
        ) : <Empty title="No prices match">Clear the search or pick another department.</Empty>}
      </section>
    </div>
  )
}
