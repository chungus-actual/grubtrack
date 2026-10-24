import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Camera, Keyboard, Plus, X } from 'lucide-react'
import { img, post, upload } from '../lib/api'
import { useConfig } from '../lib/config'
import { shrinkPhoto } from '../lib/photo'
import { dollars, plural } from '../lib/format'
import Sheet, { useSheet } from '../components/Sheet'
import { ErrorBox, Loading } from '../components/bits'
import { useToast } from '../components/Toasts'

const today = () => new Date().toLocaleDateString('en-CA') // YYYY-MM-DD, local
const num = (v) => (v === '' || v == null || Number.isNaN(Number(v)) ? null : Number(v))
const cents = (n) => Math.round(n * 100) / 100
const blankLine = () => ({ name: '', price: '' })
const blank = () => ({ store: '', date: today(), total: '', tax: '', savings: '', lines: [blankLine(), blankLine(), blankLine()] })

function toForm(d) {
  const s = (v) => (v == null ? '' : String(v))
  return { ...d, store: s(d.store), date: d.date || today(), total: s(d.total), tax: s(d.tax), savings: s(d.savings),
    lines: (d.lines || []).map((l) => ({ ...l, name: l.name || l.raw || '', price: s(l.price), qty: l.qty ? String(l.qty) : '' })) }
}

function toBody(f) {
  return { ...f, store: f.store.trim(), total: num(f.total), tax: num(f.tax), savings: num(f.savings),
    lines: f.lines.filter((l) => l.name.trim() && num(l.price) != null)
      .map(({ qty, ...l }) => ({ ...l, name: l.name.trim(), price: num(l.price), ...(num(qty) ? { qty: num(qty) } : {}) })) }
}

function Reconcile({ f }) {
  const priced = f.lines.filter((l) => num(l.price) != null)
  const sum = cents(priced.reduce((s, l) => s + num(l.price), 0))
  const tax = num(f.tax)
  const total = num(f.total)
  if (total == null) return <p className="note">Enter the printed total and the lines are checked against it as you go.</p>
  const diff = cents(total - sum - (tax || 0))
  const ok = tax != null && Math.abs(diff) <= 0.02
  return (
    <div className={`recon ${ok ? 'recon--ok' : ''}`} role="status">
      <span>{plural(priced.length, 'line')} {dollars(sum)}{tax != null ? ` plus ${dollars(tax)} tax` : ''}</span>
      <b>{ok ? 'Adds up to the printed total' : tax == null ? 'Enter the tax (0 if none)' : `${dollars(Math.abs(diff))} ${diff > 0 ? 'short of' : 'over'} the total`}</b>
    </div>
  )
}

export default function NewReceiptSheet({ onClose }) {
  const { ai } = useConfig()
  const qc = useQueryClient()
  const toast = useToast()
  const { open } = useSheet()
  const [stage, setStage] = useState('start') // start | reading | edit
  const [f, setF] = useState(null)
  const [warn, setWarn] = useState([])
  const [preview, setPreview] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef(null)
  useEffect(() => () => preview && URL.revokeObjectURL(preview), [preview])

  const read = async (file) => {
    if (!file) return
    setErr(null)
    setPreview(URL.createObjectURL(file))
    setStage('reading')
    try {
      const small = await shrinkPhoto(file)
      const out = await upload('/receipts/read', 'photo', small, small.name || 'receipt.jpg')
      setF(toForm(out.draft))
      setWarn(out.warnings || [])
      setStage('edit')
    } catch (e) {
      setErr(e)
      setStage('start')
    }
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const setLine = (i, k) => (e) => setF({ ...f, lines: f.lines.map((l, j) => (j === i ? { ...l, [k]: e.target.value } : l)) })
  const save = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await post('/receipts', toBody(f))
      qc.invalidateQueries()
      const rec = r.reconciliation
      toast(`Receipt saved: ${plural(r.lines, 'line')}, ${rec.ok ? 'adds up' : `${dollars(Math.abs(rec.diff || 0))} off the total`}`)
      open('receipt', r.receiptId)
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const body = f && toBody(f)
  const ready = body && body.store && body.date && body.total != null && body.lines.length > 0
  const photo = preview || (f?.image && img(f.image))

  return (
    <Sheet wide title="Add a receipt" onClose={onClose}
      footer={stage === 'edit' && (
        <div className="btn-row">
          <button className="btn btn--ink" onClick={save} disabled={busy || !ready}>{busy ? 'Saving' : 'Save receipt'}</button>
          <button className="btn btn--ghost" onClick={() => { setF(null); setWarn([]); setPreview(null); setStage('start') }}>Start over</button>
        </div>
      )}>
      {stage === 'start' && (
        <div className="stack" style={{ gap: 14 }}>
          {ai.vision ? (
            <>
              <button className="pickphoto" onClick={() => fileRef.current?.click()}>
                <Camera aria-hidden="true" />
                <b>Read a photo of the receipt</b>
                <span>{ai.model?.model || 'The model'} reads every line, then you check them before anything is saved.</span>
              </button>
              <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => read(e.target.files?.[0])} />
            </>
          ) : (
            <p className="note">Reading photos needs a vision model: set GT_MODEL_PROVIDER and GT_MODEL on the server. You can type a receipt in now.</p>
          )}
          <button className="btn" style={{ alignSelf: 'flex-start' }} onClick={() => { setF(blank()); setStage('edit') }}>
            <Keyboard aria-hidden="true" />Type it in
          </button>
          <ErrorBox error={err} />
        </div>
      )}

      {stage === 'reading' && (
        <div className="stack">
          {photo && <img className="receipt-photo" src={photo} alt="The receipt being read" />}
          <Loading label="Reading every line. A long receipt takes a minute." />
        </div>
      )}

      {stage === 'edit' && f && (
        <div className="stack" style={{ gap: 16 }}>
          {photo && (
            <details className="block">
              <summary style={{ cursor: 'pointer', fontWeight: 650 }}>Show the photo</summary>
              <img className="receipt-photo" style={{ marginTop: 10 }} src={photo} alt="The receipt" />
            </details>
          )}
          {warn.length > 0 && <div className="recon" role="note"><span>{warn.join(' ')}</span></div>}
          {photo && !warn.length && <p className="note">Read by {ai.model?.model || 'the model'}. Check the lines against the photo: a receipt can add up and still have a misread line.</p>}
          <div className="field-grid">
            <label className="field" style={{ gridColumn: '1 / -1' }}><span>Store</span>
              <input className="input" value={f.store} onChange={set('store')} placeholder="As printed, with the address if it's there" /></label>
            <label className="field"><span>Date</span><input className="input" type="date" value={f.date} onChange={set('date')} /></label>
            <label className="field"><span>Total</span><input className="input" inputMode="decimal" value={f.total} onChange={set('total')} placeholder="0.00" /></label>
            <label className="field"><span>Tax</span><input className="input" inputMode="decimal" value={f.tax} onChange={set('tax')} placeholder="0.00" /></label>
            <label className="field"><span>Saved</span><input className="input" inputMode="decimal" value={f.savings} onChange={set('savings')} placeholder="optional" /></label>
          </div>

          <div className="block">
            <div className="rline rline--head" aria-hidden="true"><span>Line</span><span>Qty</span><span>Paid</span><span /></div>
            <div className="rlines">
              {f.lines.map((l, i) => (
                <div className="rline" key={i}>
                  <input className="input" aria-label={`Line ${i + 1}`} value={l.name} onChange={setLine(i, 'name')} placeholder={l.raw || 'What it is'} />
                  <input className="input" aria-label={`Line ${i + 1} quantity`} inputMode="decimal" value={l.qty || ''} onChange={setLine(i, 'qty')} placeholder="1" />
                  <input className="input" aria-label={`Line ${i + 1} price`} inputMode="decimal" value={l.price} onChange={setLine(i, 'price')} placeholder="0.00" />
                  <button className="iconbtn" aria-label={`Remove line ${i + 1}`} onClick={() => setF({ ...f, lines: f.lines.filter((_, j) => j !== i) })}><X /></button>
                  {(l.regular > num(l.price) || l.weightLb) && (
                    <span className="rline__note">
                      {l.weightLb ? `${l.weightLb} lb${l.perLb ? ` at ${dollars(l.perLb)}/lb` : ''}` : ''}
                      {l.weightLb && l.regular > num(l.price) ? ', ' : ''}
                      {l.regular > num(l.price) ? `regular ${dollars(l.regular)}` : ''}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <button className="btn btn--sm btn--ghost" style={{ marginTop: 8 }} onClick={() => setF({ ...f, lines: [...f.lines, blankLine()] })}>
              <Plus aria-hidden="true" />Add a line
            </button>
          </div>
          <Reconcile f={f} />
          <p className="note">Lines that stocked something in the pantry get linked to it on save, and its price follows the receipt.</p>
          <ErrorBox error={err} />
        </div>
      )}
    </Sheet>
  )
}
