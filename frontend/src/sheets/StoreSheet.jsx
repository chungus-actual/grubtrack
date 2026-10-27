import { useQuery } from '@tanstack/react-query'
import { get } from '../lib/api'
import { dollars, perLabel, plural, shortDate, unitText } from '../lib/format'
import Sheet, { useSheet } from '../components/Sheet'
import { ErrorBox, Loading, StoreKey } from '../components/bits'
import { HBars } from '../components/charts'

const fmt = (v, label) => (v == null ? '' : !label || label === 'pkg' || label === 'ea' ? dollars(v) : unitText(v))

export default function StoreSheet({ id, onClose }) {
  const { open } = useSheet()
  const { data: s, error, isLoading } = useQuery({ queryKey: ['store', id], queryFn: () => get(`/stores/${id}`) })
  const { data: tax } = useQuery({ queryKey: ['taxonomy'], queryFn: () => get('/taxonomy'), staleTime: Infinity })
  const deptLabel = Object.fromEntries((tax?.departments || []).map((d) => [d.key, d.label]))
  const idx = s?.index
  const shared = (s?.products || []).filter((p) => p.other)
  return (
    <Sheet wide title={s?.name || 'Store'} onClose={onClose}>
      {isLoading && <Loading />}
      <ErrorBox error={error} />
      {s && (
        <>
          <p className="soft">
            <StoreKey id={s.id} name={s.chain} />
            {s.address ? `, ${s.address}` : ''}{s.city ? `, ${s.city}` : ''}{s.storeNumber ? `, store #${s.storeNumber}` : ''}.
          </p>
          <div className="figures block">
            <div className="figure"><span>Trips</span><b>{s.tripCount ?? s.trips.length}</b><small>{s.firstVisit ? `since ${shortDate(s.firstVisit)}` : ''}</small></div>
            <div className="figure"><span>Spent</span><b>{dollars(s.spend, { cents: false })}</b><small>{s.avgBasket ? `${dollars(s.avgBasket, { cents: false })} a trip` : ''}</small></div>
            <div className="figure"><span>Saved</span><b>{dollars(s.saved || 0, { cents: false })}</b><small>sales and coupons</small></div>
            {idx && (
              <div className="figure">
                <span>Price level</span>
                <b>{idx.index < 1 ? `${Math.round((1 - idx.index) * 100)}% under` : idx.index > 1 ? `${Math.round((idx.index - 1) * 100)}% over` : 'Average'}</b>
                <small>average, on {plural(idx.shared, 'shared product')}</small>
              </div>
            )}
          </div>
          {s.depts?.length > 0 && (
            <div className="block">
              <div className="block-title">What gets bought here</div>
              <HBars rows={s.depts.slice(0, 8).map((d) => ({ key: d.dept, label: deptLabel[d.dept] || d.dept, value: d.spend }))} />
            </div>
          )}
          {shared.length > 0 && (
            <div className="block">
              <div className="block-title">Against your other stores</div>
              <table className="tbl">
                <thead><tr><th>Product</th><th className="r">Here</th><th className="r">Best elsewhere</th></tr></thead>
                <tbody>
                  {shared.map((p) => (
                    <tr key={p.id} onClick={() => open('product', p.id)} style={{ cursor: 'pointer' }}>
                      <td>{p.name}<div className="muted">{perLabel(p.basis?.label)}</div></td>
                      <td className="r">{fmt(p.avg, p.basis?.label)}</td>
                      <td className="r">{fmt(p.other.avg, p.basis?.label)}<div className="muted">{p.other.store}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="block">
            <div className="block-title">Trips</div>
            <div className="list list--panel">
              {s.trips.map((t) => (
                <button key={t.id} className="row" onClick={() => open('receipt', t.id)}>
                  <span><span className="row__title">{shortDate(t.date, { year: true })}</span>
                    <span className="row__meta" style={{ display: 'block' }}>{t.lines ? `${t.lines} lines` : 'not itemized'}{t.saved ? `, saved ${dollars(t.saved)}` : ''}</span></span>
                  <span className="num lead">{dollars(t.total)}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="block">
            <div className="block-title">Everything bought here <small>{s.products.length}</small></div>
            <div className="list list--panel">
              {s.products.map((p) => (
                <button key={p.id} className="row" onClick={() => open('product', p.id)}>
                  <span><span className="row__title">{p.name}</span><span className="row__meta" style={{ display: 'block' }}>{p.catLabel}, {p.n}×, last {shortDate(p.lastDate)}</span></span>
                  <span className="num soft">{fmt(p.last, p.basis?.label)} {p.basis && p.basis.label !== 'pkg' ? perLabel(p.basis.label) : ''}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </Sheet>
  )
}
