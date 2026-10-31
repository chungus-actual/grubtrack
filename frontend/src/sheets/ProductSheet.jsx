import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import { get, patch, post } from '../lib/api'
import { dollars, pct, perLabel, plural, shortDate, unitText } from '../lib/format'
import Sheet, { useSheet } from '../components/Sheet'
import PriceTag, { lastMeta } from '../components/PriceTag'
import { PriceHistory } from '../components/charts'
import { Delta, ErrorBox, Loading, StoreKey } from '../components/bits'
import { useToast } from '../components/Toasts'

export function money(v, label) {
  if (v === null || v === undefined) return ''
  return !label || label === 'pkg' || label === 'ea' ? dollars(v) : unitText(v)
}

export function CategorySelect({ value, onChange, taxonomy, id }) {
  return (
    <select id={id} className="select" value={value || ''} onChange={(e) => onChange(e.target.value)}>
      {(taxonomy?.departments || []).map((d) => (
        <optgroup key={d.key} label={d.label}>
          {d.cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </optgroup>
      ))}
    </select>
  )
}

function changeSentence(p) {
  const b = p.basis?.label
  const c = p.change
  if (!c) return null
  if (!c.pct) return <>Holding at <b>{money(c.to, b)}</b> {perLabel(b)} at {c.store} ({shortDate(c.fromDate)} and {shortDate(c.toDate)}).</>
  return (
    <>Shelf price at {c.store} went from <b>{money(c.from, b)}</b> to <b>{money(c.to, b)}</b> {perLabel(b)} between {shortDate(c.fromDate)} and {shortDate(c.toDate)} <Delta value={c.pct} digits={1} />.</>
  )
}

function MergePicker({ product, onMerged }) {
  const [q, setQ] = useState('')
  const { data = [] } = useQuery({ queryKey: ['products', q], queryFn: () => get(`/products?q=${encodeURIComponent(q)}`), enabled: q.length >= 2 })
  const toast = useToast()
  return (
    <div className="stack">
      <input className="input" placeholder="Find the product it's the same as" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="list list--panel">
        {data.filter((x) => x.id !== product.id).map((x) => (
          <button key={x.id} className="row" onClick={async () => {
            await post(`/products/${product.id}/merge`, { into: x.id })
            toast(`${product.name} folded into ${x.name}`)
            onMerged(x.id)
          }}>
            <span><span className="row__title">{x.name}</span><span className="row__meta" style={{ display: 'block' }}>{x.catLabel}{x.brand ? `, ${x.brand}` : ''}</span></span>
            <span className="soft">Same thing</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function EditProduct({ p, taxonomy, onDone }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ name: p.name, cat: p.cat, size: p.size || '', generic: p.generic || '' })
  const [err, setErr] = useState(null)
  const save = async (e) => {
    e.preventDefault()
    const body = {}
    if (f.name !== p.name) body.name = f.name
    if (f.cat !== p.cat) body.cat = f.cat
    if ((f.size || '') !== (p.size || '')) body.size = f.size
    if ((f.generic || '') !== (p.generic || '')) body.generic = f.generic
    try {
      if (Object.keys(body).length) await patch(`/products/${p.id}`, body)
      qc.invalidateQueries()
      onDone()
    } catch (x) { setErr(x) }
  }
  return (
    <form className="stack" style={{ gap: 14 }} onSubmit={save}>
      <label className="field"><span>Name</span><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field"><span>Category</span><CategorySelect value={f.cat} taxonomy={taxonomy} onChange={(cat) => setF({ ...f, cat })} /></label>
      <div className="field-grid">
        <label className="field"><span>Package size</span><input className="input" value={f.size} placeholder="32 oz, 1.5 L, dozen" onChange={(e) => setF({ ...f, size: e.target.value })} /></label>
        <label className="field"><span>Kind of thing</span><input className="input" value={f.generic} placeholder="kefir, eggs, olive oil" onChange={(e) => setF({ ...f, generic: e.target.value })} /></label>
      </div>
      <ErrorBox error={err} />
      <div className="btn-row"><button className="btn btn--ink" type="submit">Save changes</button><button type="button" className="btn btn--ghost" onClick={onDone}>Cancel</button></div>
    </form>
  )
}

export default function ProductSheet({ id, onClose }) {
  const { open } = useSheet()
  const [mode, setMode] = useState('view')
  const { data: p, error, isLoading } = useQuery({ queryKey: ['product', id], queryFn: () => get(`/products/${id}`) })
  const { data: tax } = useQuery({ queryKey: ['taxonomy'], queryFn: () => get('/taxonomy'), staleTime: Infinity })
  const b = p?.basis?.label
  return (
    <Sheet wide title={p?.name || 'Product'} onClose={onClose}
      actions={p && mode === 'view' && <button className="iconbtn" aria-label="Edit product" onClick={() => setMode('edit')}><Pencil /></button>}>
      {isLoading && <Loading />}
      <ErrorBox error={error} />
      {p && mode === 'edit' && (
        <>
          <EditProduct p={p} taxonomy={tax} onDone={() => setMode('view')} />
          <div className="divider" />
          <div className="block-title" style={{ fontWeight: 700, marginBottom: 8 }}>Same as another product?</div>
          <p className="note" style={{ marginBottom: 10 }}>Folding it in moves every purchase and pantry row over and remembers the name, so it never splits off again.</p>
          <MergePicker product={p} onMerged={(into) => open('product', into)} />
        </>
      )}
      {p && mode === 'view' && (
        <>
          {p.last ? (
            <PriceTag size="lg" name={p.name} meta={[p.catLabel, p.size, lastMeta(p.last)].filter(Boolean).join(', ')}
              price={p.last.price} unit={p.last.unit} unitLabel={p.last.unitLabel === 'pkg' && p.basis?.label === 'ea' ? 'ea' : p.last.unitLabel} saved={p.last.saved} regular={p.last.regular} />
          ) : (
            <p className="note">No purchases recorded for this product yet.</p>
          )}

          {p.n > 0 && (
            <div className="block">
              <p className="soft">
                Bought {plural(p.n, 'time')} since {shortDate(p.first.date, { year: true })}
                {p.verified < p.n ? `, ${p.verified} of them read off a receipt` : ', every one off a receipt'}.
                {' '}{changeSentence(p)}
                {p.deal ? <> The last one was <b>{p.deal}% off</b> the shelf price.</> : null}
              </p>
            </div>
          )}

          {p.spark?.length > 1 && (
            <div className="block"><PriceHistory points={p.spark} basis={p.basis} /></div>
          )}
          {p.n > 0 && !p.basis && (
            <p className="block note">Sold by weight, and the weights weren't printed, so these packages can't be compared. A package size on the product fixes that.</p>
          )}

          {p.stores?.length > 1 && (
            <div className="block">
              <div className="block-title">By store <small>{perLabel(b)}</small></div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr><th>Store</th><th className="r">Last</th><th className="r">Average</th><th className="r">Bought</th></tr></thead>
                  <tbody>
                    {p.stores.map((s) => (
                      <tr key={s.storeId || 'none'}>
                        <td><StoreKey id={s.storeId} name={s.store} /></td>
                        <td className="r nowrap">{money(s.last, b)}</td>
                        <td className="r nowrap">{money(s.avg, b)}</td>
                        <td className="r nowrap">{s.n}×</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {p.storeGap && <p className="note" style={{ marginTop: 8 }}>{p.storeGap.cheap} is {p.storeGap.pct}% cheaper than {p.storeGap.dear} on the latest shelf prices.</p>}
            </div>
          )}

          {p.siblings?.length > 0 && (
            <div className="block">
              <div className="block-title">Other {p.generic} you've bought</div>
              <div className="list list--panel">
                {p.siblings.map((s) => (
                  <button key={s.id} className="row" onClick={() => open('product', s.id)}>
                    <span><span className="row__title">{s.name}</span>
                      <span className="row__meta" style={{ display: 'block' }}>{s.last?.store}, {shortDate(s.last?.date)}{s.size ? `, ${s.size}` : ''}</span></span>
                    <span className="row__aside">
                      {s.last?.unit != null && s.last.unitLabel !== 'pkg' && <span className="num soft">{unitText(s.last.unit)} {perLabel(s.last.unitLabel)}</span>}
                      <span className="num lead">{dollars(s.last?.price)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {p.cadence && (
            <div className="block">
              <div className="block-title">Buying rhythm</div>
              <p className="soft">
                {p.cadence.status === 'learning'
                  ? <>Bought {p.cadence.purchases} times, {p.cadence.gaps.join(' and ')} days apart. One more purchase and it gets a due date.</>
                  : <>About every <b>{Math.round(p.cadence.every)} days</b>; next one due around <b>{shortDate(p.cadence.next)}</b>{p.cadence.daysLeft < 0 ? ', which has passed' : ''}.</>}
              </p>
            </div>
          )}

          {(p.stock?.length > 0 || (p.outcomes && p.outcomes.eaten + p.outcomes.tossed > 0)) && (
            <div className="block">
              <div className="block-title">In the house</div>
              {p.outcomes && p.outcomes.eaten + p.outcomes.tossed > 0 && (
                <p className="soft" style={{ marginBottom: 10 }}>Eaten {p.outcomes.eaten}×, tossed {p.outcomes.tossed}×.</p>
              )}
              <div className="list list--panel">
                {p.stock.map((s) => (
                  <button key={s.id} className="row" onClick={() => open('item', s.id)}>
                    <span><span className="row__title">{s.name}</span>
                      <span className="row__meta" style={{ display: 'block' }}>{s.deletedAt ? `gone ${shortDate(s.deletedAt)}` : s.storage}{s.expirationDate && !s.deletedAt ? `, use by ${shortDate(s.expirationDate)}` : ''}</span></span>
                    {s.price != null && <span className="num soft">{dollars(s.price)}</span>}
                  </button>
                ))}
              </div>
            </div>
          )}

          {p.observations?.length > 0 && (
            <div className="block">
              <div className="block-title">Every purchase</div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr><th>Date</th><th>Store</th><th className="r">Paid</th>{p.observations.some((o) => o.regular) && <th className="r">Shelf</th>}<th className="r hide-sm">Unit</th><th>Source</th></tr></thead>
                  <tbody>
                    {p.observations.map((o) => (
                      <tr key={o.id}>
                        <td className="nowrap">{shortDate(o.date, { year: true })}</td>
                        <td><StoreKey id={o.storeId} name={o.store} /></td>
                        <td className="r">{dollars(o.price)}{o.qty > 1 ? <span className="muted"> for {o.qty}</span> : ''}</td>
                        {p.observations.some((x) => x.regular) && <td className="r">{o.regular ? dollars(o.regular) : ''}</td>}
                        <td className="r hide-sm">{o.unit != null && o.unitLabel !== 'pkg' ? `${unitText(o.unit)}/${o.unitLabel}` : ''}</td>
                        <td>{o.receiptId
                          ? <button className="btn btn--sm btn--ghost" onClick={() => open('receipt', o.receiptId)}>{o.verified ? 'Receipt' : 'Logged'}</button>
                          : <span className="muted">Logged</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <p className="block note">Filed under {p.catLabel}{p.catSource === 'user' ? ' (set by hand)' : p.confidence === 'low' ? ', a guess: edit to place it' : ''}.</p>
        </>
      )}
    </Sheet>
  )
}
