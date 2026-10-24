import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, Send, Share2, Undo2 } from 'lucide-react'
import { get, post } from '../lib/api'
import { useConfig } from '../lib/config'
import { ago, dollars, plural } from '../lib/format'
import { useSheet } from '../components/Sheet'
import { Empty, ErrorBox, Loading, Money, StoreKey } from '../components/bits'
import { useToast } from '../components/Toasts'

const clean = (n) => n.replace(/\s*\([^)]*\)/g, '').replace(/\s+/g, ' ').trim()

function Line({ row, onDone }) {
  const { open } = useSheet()
  const e = row.estimate
  return (
    <div className="row row--check">
      <button className="iconbtn" onClick={onDone} aria-label={`Cross off ${row.name}`}
        style={{ border: '1.5px solid var(--line-2)', width: 30, height: 30, borderRadius: 6 }}><span className="sr-only">Cross off</span></button>
      <span style={{ minWidth: 0 }}>
        <span className="row__title">{row.name}{row.quantity ? <span className="muted"> ×{row.quantity}{row.unit ? ` ${row.unit}` : ''}</span> : null}</span>
        <span className="row__meta" style={{ display: 'block' }}>
          {e?.match ? (
            <>
              <button className="btn btn--ghost btn--sm" style={{ padding: 0, minHeight: 0, fontWeight: 500, color: 'inherit' }} onClick={() => open('product', e.productId)}>
                {e.match === 'exact' || clean(e.product || '').toLowerCase() === clean(row.name).toLowerCase() ? 'Last time' : `Like ${e.product}`}
              </button>
              {e.range ? `, ${dollars(e.range[0])} to ${dollars(e.range[1])} across ${e.kinds} kinds` : ''}
              {e.best && Object.keys(e.stores).length > 1 ? <>, cheapest at <StoreKey id={Object.keys(e.stores).find((k) => e.stores[k].store === e.best.store)} name={e.best.store} /> {dollars(e.best.price)}</> : null}
            </>
          ) : e ? `${e.catLabel}, no price on record` : null}
          {row.notes ? ` ${row.notes}` : ''}
        </span>
      </span>
      {e?.estimate != null ? <Money value={e.estimate} size="xs" /> : <span />}
    </div>
  )
}

export default function List() {
  const qc = useQueryClient()
  const toast = useToast()
  const { ai } = useConfig()
  const { data, error, isLoading } = useQuery({ queryKey: ['shopping'], queryFn: () => get('/shopping') })
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const refresh = () => qc.invalidateQueries({ queryKey: ['shopping'] })

  const add = async (name) => {
    const n = (name ?? text).trim()
    if (!n) return
    const r = await post('/shopping', { name: n })
    if (name === undefined) setText('')
    refresh()
    toast(r.added ? `${r.name} added` : `${r.name} is already on the list`)
  }
  const done = async (row) => {
    await post(`/shopping/${row.id}/done`)
    refresh()
    toast(`${row.name} crossed off`, { label: 'Undo', run: async () => { await post(`/shopping/${row.id}/undo`); refresh() } })
  }
  const send = async () => {
    setSending(true)
    try { await post('/shopping/send'); toast(`Sent to ${ai.send}`) } catch (e) { toast(e.message) } finally { setSending(false) }
  }
  // the phone's own share sheet (Messages, Notes, any chat app); copy to the clipboard elsewhere
  const share = async () => {
    const text = data.open.map((r) => `- ${r.name}${r.quantity ? ` ×${r.quantity}${r.unit ? ` ${r.unit}` : ''}` : ''}`).join('\n')
    try {
      if (navigator.share) await navigator.share({ title: 'Shopping list', text })
      else { await navigator.clipboard.writeText(text); toast('List copied') }
    } catch (e) { if (e.name !== 'AbortError') toast("Couldn't share the list") }
  }

  if (isLoading) return <Loading />
  if (error) return <ErrorBox error={error} />
  const est = data.estimate
  const cmp = est.compare

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">List</h1>
        {data.open.length > 0 && (
          <div className="btn-row">
            {ai.send && <button className="btn" onClick={send} disabled={sending}><Send aria-hidden="true" />{sending ? 'Sending' : `Send to ${ai.send}`}</button>}
            <button className="btn" onClick={share}><Share2 aria-hidden="true" />Share</button>
          </div>
        )}
      </div>
      {data.open.length > 0 ? (
        <p className="lede">
          <b>{plural(data.open.length, 'thing')}</b> to get.
          {est.priced > 0 && <> About <b>{dollars(est.total, { cents: false })}</b> at the prices last paid{est.priced < est.count ? ` (${est.priced} of ${est.count} have a price)` : ''}.</>}
          {cmp && <> The {plural(cmp.lines, 'item')} bought at both stores: <b>{dollars(cmp.stores[0].total)}</b> at {cmp.stores[0].store}, <b>{dollars(cmp.stores[1].total)}</b> at {cmp.stores[1].store}.</>}
        </p>
      ) : <p className="lede">The list is empty. What you add here, from the command line or through your agent, all lands on this one list.</p>}

      <form className="hstack" style={{ flexWrap: 'nowrap', marginBottom: 18 }} onSubmit={(e) => { e.preventDefault(); add() }}>
        <label className="sr-only" htmlFor="add">Add to the list</label>
        <input id="add" className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add something: oat milk, cilantro…" autoComplete="off" />
        <button className="btn btn--ink" type="submit" disabled={!text.trim()}><Plus aria-hidden="true" />Add</button>
      </form>

      {data.open.length > 0 && (
        <div className="list list--panel">
          {data.open.map((r) => <Line key={r.id} row={r} onDone={() => done(r)} />)}
        </div>
      )}

      {[['rhythm', 'Due on their usual rhythm'], ['eaten', 'Eaten lately, none in the house']].map(([kind, title]) => {
        const xs = data.suggestions.filter((s) => s.kind === kind)
        if (!xs.length) return null
        return (
          <section className="section" key={kind}>
            <div className="section-head"><h2 className="section-title">{title}</h2><span className="section-sub">tap to add</span></div>
            <div className="chips">
              {xs.map((s) => {
                const name = clean(s.name)
                return (
                  <button key={s.name} className="chip chip--add" onClick={() => add(name)} title={s.why}>
                    <Plus aria-hidden="true" style={{ width: 14 }} />{name}
                  </button>
                )
              })}
            </div>
          </section>
        )
      })}

      {data.done.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2 className="section-title">Crossed off this week</h2>
            <button className="btn btn--sm btn--ghost" onClick={async () => { const r = await post('/shopping/clear'); refresh(); toast(`${plural(r.removed, 'row')} cleared`) }}>Clear crossed off</button>
          </div>
          <div className="list list--panel">
            {data.done.map((r) => (
              <div key={r.id} className="row row--check">
                <span className="iconbtn" style={{ width: 30, height: 30, background: 'var(--ink)', color: 'var(--surface)', borderRadius: 6 }}><Check aria-hidden="true" style={{ width: 16 }} /></span>
                <span><span className="row__title" style={{ textDecoration: 'line-through', color: 'var(--ink-3)' }}>{r.name}</span>
                  <span className="row__meta" style={{ display: 'block' }}>{ago(r.doneAt)}{r.addedBy ? `, added by ${r.addedBy}` : ''}</span></span>
                <button className="btn btn--sm btn--ghost" onClick={async () => { await post(`/shopping/${r.id}/undo`); refresh() }}><Undo2 aria-hidden="true" />Put back</button>
              </div>
            ))}
          </div>
        </section>
      )}
      {!data.open.length && !data.done.length && !data.suggestions.length && <Empty title="Nothing to buy" />}
    </div>
  )
}
