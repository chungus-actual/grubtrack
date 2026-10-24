import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { get } from '../lib/api'
import { useConfig } from '../lib/config'
import { dollars, monthName, plural, shortDate, weekday } from '../lib/format'
import { useSheet } from '../components/Sheet'
import { Empty, ErrorBox, Loading, StoreKey } from '../components/bits'
import { Columns, HBars } from '../components/charts'

export default function Spend() {
  const { open } = useSheet()
  const { ai } = useConfig()
  const { data, error, isLoading } = useQuery({ queryKey: ['spend'], queryFn: () => get('/spend') })
  const { data: tax } = useQuery({ queryKey: ['taxonomy'], queryFn: () => get('/taxonomy'), staleTime: Infinity })
  const [month, setMonth] = useState(null)
  if (isLoading) return <Loading label="Adding up the receipts" />
  if (error) return <ErrorBox error={error} />

  const sp = data.spend
  const months = sp.months
  const picked = month || months[months.length - 1]?.month
  const m = months.find((x) => x.month === picked)
  const deptLabel = Object.fromEntries((tax?.departments || []).map((d) => [d.key, d.label]))
  const ov = data.overview.spend
  const w = data.waste
  const trips = sp.trips
  const deptRows = m ? Object.entries(m.depts).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ key: k, label: deptLabel[k] || k, value: v })) : []
  if (m?.unitemized > 0.5) deptRows.push({ key: '_un', label: 'Not itemized', value: m.unitemized })
  if (m?.tax > 0) deptRows.push({ key: '_tax', label: 'Tax', value: m.tax })

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">Spend</h1>
        <button className="btn" onClick={() => open('newreceipt')}><Plus aria-hidden="true" />Add a receipt</button>
      </div>
      {m ? (
        <p className="lede">
          <b>{dollars(m.total, { cents: false })}</b> on food and household in {monthName(m.month, true)} across {plural(m.trips, 'trip')}
          {m.saved > 0 ? <>, with <b>{dollars(m.saved)}</b> knocked off by sales</> : null}.
          {' '}The last 30 days come to <b>{dollars(ov.last30, { cents: false })}</b>.
        </p>
      ) : <p className="lede">Spending shows up here once receipts are recorded.</p>}

      {months.length > 0 && (
        <section className="section panel">
          <div className="section-head">
            <h2 className="section-title">By month</h2>
            <span className="section-sub">pick a month to break it down</span>
          </div>
          <Columns rows={months.map((x) => ({ key: x.month, label: monthName(x.month), value: x.total, sub: `${plural(x.trips, 'trip')}` }))}
            picked={picked} onPick={setMonth} height={190} />
        </section>
      )}

      <div className="cols cols--even section">
        <section>
          <div className="section-head"><h2 className="section-title">Where it went{m ? ` in ${monthName(m.month, true)}` : ''}</h2></div>
          {deptRows.length ? (
            <div className="panel"><HBars rows={deptRows} /></div>
          ) : <Empty title="Nothing itemized yet" />}
          {m?.unitemized > 0.5 && <p className="note" style={{ marginTop: 8 }}>Not itemized is money on a receipt that has no lines yet, like a trip logged by voice without the photo.</p>}
        </section>
        <section>
          <div className="section-head"><h2 className="section-title">Stores</h2></div>
          <div className="list list--panel">
            {data.stores.filter((s) => s.trips).map((s) => (
              <button key={s.id} className="row" onClick={() => open('store', s.id)}>
                <span>
                  <span className="row__title"><StoreKey id={s.id} name={s.name} /></span>
                  <span className="row__meta" style={{ display: 'block' }}>
                    {plural(s.trips, 'trip')}, last {shortDate(s.lastVisit)}
                    {s.index ? `, prices ${s.index.index < 1 ? `${Math.round((1 - s.index.index) * 100)}% under` : `${Math.round((s.index.index - 1) * 100)}% over`} average on ${plural(s.index.shared, 'shared product')}` : ''}
                  </span>
                </span>
                <span className="num lead">{dollars(s.spend, { cents: false })}</span>
              </button>
            ))}
          </div>
        </section>
      </div>

      <section className="section">
        <div className="section-head"><h2 className="section-title">Trips</h2><span className="section-sub">{trips.length} receipts</span></div>
        {trips.length ? (
          <div className="list list--panel">
            {trips.map((t) => (
              <button key={t.id} className="row" onClick={() => open('receipt', t.id)}>
                <span>
                  <span className="row__title">{weekday(t.date)} {shortDate(t.date, { year: true })}, <StoreKey id={t.storeId} name={t.store} /></span>
                  <span className="row__meta" style={{ display: 'block' }}>
                    {t.lines ? plural(t.lines, 'line') : t.voiced ? `${t.voiced} priced by voice` : 'no lines'}
                    {t.saved ? `, saved ${dollars(t.saved)}` : ''}
                  </span>
                </span>
                <span className="row__aside">
                  {t.reconciled
                    ? <span className="pill pill--down" title="Lines plus tax equal the printed total">Adds up</span>
                    : <span className="pill pill--warn" title="Part of this trip isn't broken into lines">{dollars(t.unitemized, { cents: false })} unitemized</span>}
                  <span className="num lead">{dollars(t.total)}</span>
                </span>
              </button>
            ))}
          </div>
        ) : <Empty title="No receipts yet">{ai.vision ? 'Add one from a photo' : 'Add one by hand'}, or have your agent record it with the food-inventory skill. Every line lands here, checked against the printed total.</Empty>}
      </section>

      {sp.loose.length > 0 && (
        <section className="section">
          <div className="section-head"><h2 className="section-title">Priced without a receipt</h2><span className="section-sub">voice-logged prices with no store attached</span></div>
          <div className="list list--panel">
            {sp.loose.map((o) => (
              <div key={o.id} className="row">
                <span><span className="row__title">{o.name}</span><span className="row__meta" style={{ display: 'block' }}>{shortDate(o.date)}</span></span>
                <span className="num lead">{dollars(o.price)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head"><h2 className="section-title">What left the house</h2><span className="section-sub">last {w.days} days</span></div>
        <p className="soft" style={{ maxWidth: '70ch' }}>
          <b>{w.eaten}</b> eaten and <b>{w.tossed}</b> tossed
          {w.tossedPriced ? <>, the tossed ones worth <b>{dollars(w.tossedUsd)}</b>{w.tossedPriced < w.tossed ? ` (${w.tossedPriced} had a price)` : ''}</> : null}.
          {w.unknown > 0 && <> Another {w.unknown} were cleared without saying which, so they count as neither.</>}
        </p>
        {w.byCat.length > 0 && (
          <div className="panel panel--flush" style={{ marginTop: 12 }}>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead><tr><th>Category</th><th className="r">Eaten</th><th className="r">Tossed</th><th className="r">Tossed share</th></tr></thead>
                <tbody>
                  {w.byCat.slice(0, 12).map((c) => (
                    <tr key={c.cat}><td>{c.catLabel}</td><td className="r">{c.eaten}</td><td className="r">{c.tossed}</td><td className="r">{c.tossRate}%</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
