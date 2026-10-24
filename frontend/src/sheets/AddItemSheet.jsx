import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { post } from '../lib/api'
import { cap } from '../lib/format'
import Sheet, { useSheet } from '../components/Sheet'
import { ErrorBox } from '../components/bits'
import { useToast } from '../components/Toasts'

const STORAGES = ['fridge', 'freezer', 'pantry', 'cupboard', 'counter']

export default function AddItemSheet({ onClose }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { open } = useSheet()
  const [f, setF] = useState({ name: '', storage: 'fridge', expirationDate: '', quantity: '', unit: '', size: '', brand: '', price: '' })
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const body = { ...f, quantity: f.quantity ? Number(f.quantity) : undefined, price: f.price ? Number(f.price) : undefined }
      const it = await post('/items', body)
      qc.invalidateQueries({ queryKey: ['pantry'] })
      toast(`${it.name} added to the ${it.storage}`)
      open('item', it.id)
    } catch (x) { setErr(x) } finally { setBusy(false) }
  }
  return (
    <Sheet title="Add to the pantry" onClose={onClose}>
      <form onSubmit={submit} className="stack" style={{ gap: 16 }}>
        <label className="field"><span>What is it</span>
          <input className="input" autoFocus required value={f.name} onChange={set('name')} placeholder="Greek yogurt, Fairlife 2% milk…" />
        </label>
        <div className="field">
          <span>Where does it go</span>
          <div className="chips" role="group" aria-label="Storage">
            {STORAGES.map((s) => (
              <button type="button" key={s} className="chip" aria-pressed={f.storage === s} onClick={() => setF({ ...f, storage: s })}>{cap(s)}</button>
            ))}
          </div>
        </div>
        <div className="field-grid">
          <label className="field"><span>Use by</span><input className="input" type="date" value={f.expirationDate} onChange={set('expirationDate')} /></label>
          <label className="field"><span>Quantity</span><input className="input" type="number" step="any" min="0" value={f.quantity} onChange={set('quantity')} /></label>
          <label className="field"><span>Unit</span><input className="input" value={f.unit} onChange={set('unit')} placeholder="bag, can, lb" /></label>
          <label className="field"><span>Package size</span><input className="input" value={f.size} onChange={set('size')} placeholder="32 oz, 1.5 L" /></label>
          <label className="field"><span>Brand</span><input className="input" value={f.brand} onChange={set('brand')} /></label>
          <label className="field"><span>Price paid</span><input className="input" type="number" step="0.01" min="0" value={f.price} onChange={set('price')} placeholder="0.00" /></label>
        </div>
        <p className="note">The category is worked out from the name. A package size gives it a unit price, so it can be compared across stores and brands.</p>
        <ErrorBox error={err} />
        <div className="btn-row"><button className="btn btn--ink" type="submit" disabled={busy || !f.name.trim()}>Add to the pantry</button></div>
      </form>
    </Sheet>
  )
}
