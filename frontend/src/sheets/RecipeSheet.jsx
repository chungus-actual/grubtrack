import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Plus } from 'lucide-react'
import { get, post, put } from '../lib/api'
import Sheet from '../components/Sheet'
import { ErrorBox, Loading, Meter } from '../components/bits'
import { useConfig } from '../lib/config'
import { useToast } from '../components/Toasts'

function Journal({ slug, type }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({ queryKey: ['journal', slug], queryFn: () => get(`/recipes/${slug}/journal`) })
  const [f, setF] = useState({ score: '', tags: '', review: '', mods: '' })
  useEffect(() => {
    const e = data?.entry
    if (e) setF({ score: e.score ?? '', tags: (e.tags || []).join(', '), review: e.review || '', mods: e.mods || '' })
  }, [data])
  const save = async (e) => {
    e.preventDefault()
    await put(`/recipes/${slug}/journal`, { score: f.score === '' ? null : Number(f.score), tags: f.tags.split(',').map((t) => t.trim()).filter(Boolean), review: f.review, mods: f.mods, recipe_type: type })
    qc.invalidateQueries({ queryKey: ['recipes'] })
    toast('Journal saved')
  }
  return (
    <form className="stack" onSubmit={save}>
      <div className="field-grid">
        <label className="field"><span>Score out of 10</span><input className="input" type="number" min="1" max="10" value={f.score} onChange={(e) => setF({ ...f, score: e.target.value })} /></label>
        <label className="field"><span>Tags</span><input className="input" value={f.tags} placeholder="weeknight, freezer" onChange={(e) => setF({ ...f, tags: e.target.value })} /></label>
      </div>
      <label className="field"><span>How it went</span><textarea className="textarea" value={f.review} onChange={(e) => setF({ ...f, review: e.target.value })} /></label>
      <label className="field"><span>What we changed</span><textarea className="textarea" value={f.mods} onChange={(e) => setF({ ...f, mods: e.target.value })} /></label>
      <div className="btn-row"><button className="btn btn--ink" type="submit">Save journal</button></div>
    </form>
  )
}

export default function RecipeSheet({ id, onClose }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: r, error, isLoading } = useQuery({ queryKey: ['recipe', id], queryFn: () => get(`/recipes/${id}`) })
  const { ai } = useConfig()
  const [enriching, setEnriching] = useState(false)
  const [enrichErr, setEnrichErr] = useState(null)
  const need = (r?.lines || []).filter((l) => l.have === false)
  const addMissing = async () => {
    let n = 0
    for (const g of [...new Set(need.map((l) => l.generic))]) {
      const x = await post('/shopping', { name: g, notes: `for ${r.title}` })
      if (x.added) n++
    }
    qc.invalidateQueries({ queryKey: ['shopping'] })
    toast(n ? `${n} added to the list` : 'Already on the list')
  }
  const enrich = async () => {
    setEnriching(true)
    setEnrichErr(null)
    try { await post('/recipes/bulk/enrich', { slug: id }); qc.invalidateQueries({ queryKey: ['recipe', id] }) } catch (e) { setEnrichErr(e) } finally { setEnriching(false) }
  }
  // the markdown below the title, without the Ingredients list (shown above, line by line) or a heading of its
  // own for the method: the block is already titled Method
  const body = r?.markdown ? r.markdown.replace(/^---[\s\S]*?\n---\s*\n/, '').replace(/^# .*\n/, '')
    .replace(/##\s*Ingredients[\s\S]*?(?=\n##|$)/i, '')
    .replace(/^\s*#{2,3}\s*(instructions|method|directions|steps|preparation)\s*\n/i, '').trim() : null
  return (
    <Sheet wide title={r?.title || 'Recipe'} onClose={onClose}
      footer={r && need.length > 0 && <button className="btn btn--ink" onClick={addMissing}><Plus aria-hidden="true" />Add {need.length} missing to the list</button>}>
      {isLoading && <Loading />}
      <ErrorBox error={error} />
      {r && (
        <>
          <div className="spread">
            <p className="soft">{[r.category, r.cuisine, r.servings && `serves ${r.servings}`, r.total_time || [r.prep_time, r.cook_time].filter(Boolean).join(' + ')].filter(Boolean).join(', ')}</p>
            <Meter have={r.make.have} known={r.make.known} />
          </div>
          <ErrorBox error={enrichErr} />
          {r.type === 'imported' && (
            <div className="btn-row block">
              {!r.total_time && ai.extract && <button className="btn btn--sm" onClick={enrich} disabled={enriching}>{enriching ? `Asking ${ai.extract}` : 'Estimate the times'}</button>}
              {r.source && <a className="btn btn--sm btn--ghost" href={r.source} target="_blank" rel="noreferrer">Original</a>}
            </div>
          )}
          <div className="block">
            <div className="block-title">Ingredients <small>{r.make.known ? `${r.make.have} of ${r.make.known} in the house` : ''}</small></div>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }} className="stack">
              {r.lines.map((l, i) => (
                <li key={i} className="hstack" style={{ flexWrap: 'nowrap', alignItems: 'flex-start', gap: 10 }}>
                  <span aria-label={l.have === true ? 'in the house' : l.have === false ? 'need it' : ''}
                    style={{ flex: 'none', width: 20, height: 20, marginTop: 1, borderRadius: 4, display: 'grid', placeItems: 'center',
                      background: l.have === true ? 'var(--down-soft)' : l.have === false ? 'var(--yellow)' : 'var(--surface-3)',
                      color: l.have === true ? 'var(--down)' : 'var(--yellow-ink)' }}>
                    {l.have === true ? <Check style={{ width: 14 }} /> : l.have === false ? <Plus style={{ width: 14 }} /> : null}
                  </span>
                  <span>{l.text}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="block">
            <div className="block-title">Method</div>
            {body
              ? <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown></div>
              : <div className="md" style={{ whiteSpace: 'pre-wrap' }}>{r.instructions || 'No method recorded.'}</div>}
          </div>
          <div className="block">
            <div className="block-title">Journal</div>
            <Journal slug={id} type={r.type} />
          </div>
        </>
      )}
    </Sheet>
  )
}
