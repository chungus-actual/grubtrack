import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Image as ImageIcon, Link2 } from 'lucide-react'
import { get, img, post } from '../lib/api'
import { dollars, shortDate, weekday } from '../lib/format'
import Sheet, { useSheet } from '../components/Sheet'
import { ErrorBox, Loading } from '../components/bits'
import { useToast } from '../components/Toasts'

function LinkPicker({ rid, line, onDone }) {
  const { data = [] } = useQuery({ queryKey: ['cands', rid], queryFn: () => get(`/receipts/${rid}/candidates`) })
  const qc = useQueryClient()
  const toast = useToast()
  const link = async (itemId) => {
    await post(`/receipts/${rid}/lines/${line.id}/link`, { itemId })
    qc.invalidateQueries()
    toast(itemId ? 'Linked: the pantry row now carries this price' : 'Unlinked')
    onDone()
  }
  return (
    <div className="panel" style={{ margin: '8px 0 12px', fontFamily: 'var(--font)' }}>
      <div className="spread" style={{ marginBottom: 8 }}>
        <b style={{ fontSize: 'var(--t-sm)' }}>Which pantry row did {line.name || line.raw} stock?</b>
        <button className="btn btn--sm btn--ghost" onClick={onDone}>Cancel</button>
      </div>
      <div className="chips">
        {data.filter((c) => !c.gone).map((c) => (
          <button key={c.id} className="chip" aria-pressed={line.itemId === c.id} onClick={() => link(c.id)}>{c.name}</button>
        ))}
        {line.itemId && <button className="chip" onClick={() => link(null)}>Not in the pantry</button>}
      </div>
      {!data.length && <p className="note">Nothing was logged to the pantry around that date.</p>}
    </div>
  )
}

export default function ReceiptSheet({ id, onClose }) {
  const { open } = useSheet()
  const { data: r, error, isLoading } = useQuery({ queryKey: ['receipt', id], queryFn: () => get(`/receipts/${id}`) })
  const [photo, setPhoto] = useState(false)
  const [linking, setLinking] = useState(null)

  const lines = r?.lines || []
  const fromReceipt = lines.filter((l) => l.source === 'receipt')
  const voiced = lines.filter((l) => l.source !== 'receipt')
  const sections = []
  for (const l of fromReceipt) {
    const s = l.section || ''
    if (!sections.length || sections[sections.length - 1].name !== s) sections.push({ name: s, lines: [] })
    sections[sections.length - 1].lines.push(l)
  }
  const lineSum = fromReceipt.reduce((s, l) => s + l.price, 0)
  const saved = fromReceipt.reduce((s, l) => s + (l.regular && l.regular > l.price ? l.regular - l.price : 0), 0)
  const store = r?.storeInfo

  return (
    <Sheet title={store ? `${store.name}, ${shortDate(r.date)}` : 'Receipt'} onClose={onClose}
      actions={r?.imagePath && <button className="iconbtn" aria-pressed={photo} aria-label={photo ? 'Show the transcription' : 'Show the photo'} onClick={() => setPhoto(!photo)}><ImageIcon /></button>}>
      {isLoading && <Loading />}
      <ErrorBox error={error} />
      {r && photo && <img src={img(r.imagePath)} alt={`Photo of the ${store?.name || ''} receipt`} style={{ width: '100%', borderRadius: 6 }} />}
      {r && !photo && (
        <div className="receipt">
          <div className="r-head">
            <b>{store?.chain || r.store}</b>
            {store?.address && <div>{store.address}</div>}
            {store?.city && <div>{store.city}{store.region ? `, ${store.region}` : ''} {store.postal || ''}</div>}
            <div>{weekday(r.date)} {shortDate(r.date, { year: true })}</div>
          </div>
          {sections.map((s, si) => (
            <div key={si}>
              {s.name && <div className="r-sec">{s.name}</div>}
              {s.lines.map((l) => (
                <div key={l.id}>
                  <button className="r-line" onClick={() => l.product && open('product', l.product.id)} title={l.product ? `Open ${l.product.name}` : undefined}>
                    <span>{l.raw || l.name}</span>
                    <span>{l.price.toFixed(2)}{l.taxed ? ' T' : ''}</span>
                    {l.qty > 1 && !l.weightLb && <span className="r-sub">{l.qty} @ {(l.price / l.qty).toFixed(2)} ea</span>}
                    {l.weightLb && <span className="r-sub">{l.weightLb} lb @ ${(l.perLb || l.price / l.weightLb).toFixed(2)}/lb</span>}
                    {l.regular > l.price && <span className="r-save">Sale savings −{(l.regular - l.price).toFixed(2)}</span>}
                  </button>
                  <div className="r-link">
                    {l.product && <span>{l.product.name}</span>}
                    {l.product && <span className="pill">{l.product.catLabel}</span>}
                    {l.item && (
                      <button className="pill" style={{ border: 0, cursor: 'pointer' }} onClick={() => open('item', l.item.id)}>
                        <Link2 aria-hidden="true" />{l.item.name}{l.item.gone ? ' (gone)' : ''}</button>
                    )}
                    <button className="r-relink" onClick={() => setLinking(linking === l.id ? null : l.id)}
                      aria-label={l.item ? `Change which pantry row ${l.name} stocked` : `Link ${l.name} to a pantry row`}
                      title={l.item ? 'Change the pantry link' : 'Link to a pantry row'}>
                      <Link2 aria-hidden="true" />
                    </button>
                  </div>
                  {linking === l.id && <LinkPicker rid={r.id} line={l} onDone={() => setLinking(null)} />}
                </div>
              ))}
            </div>
          ))}
          {voiced.length > 0 && (
            <>
              <div className="r-sec">Prices logged without lines</div>
              {voiced.map((l) => (
                <button key={l.id} className="r-line" onClick={() => l.product && open('product', l.product.id)}>
                  <span>{l.name}</span><span>{l.price.toFixed(2)}</span>
                </button>
              ))}
            </>
          )}
          <hr className="r-rule" />
          {fromReceipt.length > 0 && <div className="r-total"><span>Lines</span><span>{lineSum.toFixed(2)}</span></div>}
          {r.tax != null && <div className="r-total"><span>Tax</span><span>{r.tax.toFixed(2)}</span></div>}
          <div className="r-total big"><span>Total</span><span>{r.total != null ? r.total.toFixed(2) : '?'}</span></div>
          {(r.savings || saved > 0) ? <div className="r-total"><span>You saved</span><span>{(r.savings || saved).toFixed(2)}</span></div> : null}
          {r.paymentNote && <div style={{ marginTop: 6 }}>{r.paymentNote}</div>}
          {r.reconciled ? (
            <div className="r-check ok">Every line is here: lines plus tax come to the printed total, to the cent.</div>
          ) : fromReceipt.length ? (
            <div className="r-check bad">Off by {dollars(Math.abs(r.diff || 0))}: a line is missing or misread. Check the lines against the photo.</div>
          ) : (
            <div className="r-check bad">Not itemized: {dollars(r.unitemized || r.total)} of this trip isn't broken into lines{r.imagePath ? '' : ', and no photo is on file'}.</div>
          )}
        </div>
      )}
    </Sheet>
  )
}
