import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Search, Sparkles, Square, Upload } from 'lucide-react'
import { get, post, stream, upload } from '../lib/api'
import { useConfig } from '../lib/config'
import { plainDashes, plural } from '../lib/format'
import { useSheet } from '../components/Sheet'
import { Empty, ErrorBox, Loading, Meter } from '../components/bits'
import { useToast } from '../components/Toasts'

const FOCUS = [['', 'Everything'], ['fridge', 'Fridge'], ['freezer', 'Freezer'], ['pantry', 'Pantry']]
const TIME = [['', 'Any time'], ['quick', 'Under 30 min'], ['medium', '30 to 75 min'], ['long', 'Longer']]
const PROTEIN = [['', 'Any protein'], ['chicken', 'Chicken'], ['beef', 'Beef'], ['pork', 'Pork'], ['seafood', 'Seafood'], ['vegetarian', 'Vegetarian'], ['other_meat', 'Other meat']]
const CUISINE = [['', 'Any cuisine'], ['asian', 'Asian'], ['european', 'European'], ['latin', 'Latin'], ['african_me', 'African & Middle Eastern'], ['american', 'American'], ['other', 'Other']]
const DISH = [['', 'Any dish'], ['main', 'Mains'], ['soup', 'Soups & stews'], ['salad', 'Salads & sides'], ['baked', 'Baking'], ['breakfast', 'Breakfast'], ['sauce', 'Sauces']]

function MealIdeas() {
  const { ai } = useConfig()
  const [focus, setFocus] = useState('')
  const [context, setContext] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const ctl = useRef(null)
  useEffect(() => () => ctl.current?.abort(), [])
  const ask = async () => {
    setText('')
    setErr(null)
    setBusy(true)
    ctl.current = new AbortController()
    try {
      await stream('/meals/suggest', { storage: focus || undefined, context }, (t) => setText((x) => x + t), ctl.current.signal)
    } catch (e) {
      if (e.name !== 'AbortError') setErr(e)
    } finally { setBusy(false) }
  }
  if (!ai.chat) {
    return (
      <section className="panel">
        <div className="section-head"><h2 className="section-title">Meal ideas</h2></div>
        <p className="note">Set up an agent (GT_AGENT, OpenClaw or Hermes) or a model (GT_MODEL_PROVIDER) and it will suggest dinners from what's in the house, leading with what expires first.</p>
      </section>
    )
  }
  return (
    <section className="panel">
      <div className="section-head"><h2 className="section-title">Ask {ai.chat} for dinner</h2><span className="section-sub">reads the pantry, leads with what expires first</span></div>
      <div className="chips" role="group" aria-label="Look in" style={{ marginBottom: 10 }}>
        {FOCUS.map(([k, v]) => <button key={k} className="chip" aria-pressed={focus === k} onClick={() => setFocus(k)}>{v}</button>)}
      </div>
      <label className="field" style={{ marginBottom: 12 }}>
        <span>Anything to know</span>
        <input className="input" value={context} onChange={(e) => setContext(e.target.value)} placeholder="Cooking for two, 30 minutes max, no rice…" />
      </label>
      <div className="btn-row">
        {busy
          ? <button className="btn" onClick={() => ctl.current?.abort()}><Square aria-hidden="true" />Stop</button>
          : <button className="btn btn--ink" onClick={ask}><Sparkles aria-hidden="true" />{text ? 'Ask again' : 'Suggest meals'}</button>}
      </div>
      <ErrorBox error={err} />
      {(text || busy) && (
        <div className="md" style={{ marginTop: 16 }} aria-live="polite">
          {text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{plainDashes(text)}</ReactMarkdown> : <p className="muted">{ai.chat} is looking in the fridge…</p>}
        </div>
      )}
    </section>
  )
}

function RecipeRow({ r, onOpen }) {
  const meta = [r.category, r.cuisine, r.total_time || r.prep_time].filter(Boolean)
  return (
    <button className="row" onClick={onOpen}>
      <span style={{ minWidth: 0 }}>
        <span className="row__title">{r.title}</span>
        <span className="row__meta" style={{ display: 'block' }}>
          {meta.join(', ')}
          {r.make?.need?.length ? `${meta.length ? '. ' : ''}Need ${r.make.need.slice(0, 3).join(', ')}${r.make.need.length > 3 ? ` and ${r.make.need.length - 3} more` : ''}` : ''}
        </span>
      </span>
      <span className="row__aside">
        {r.journal?.score ? <span className="pill">{r.journal.score}/10</span> : null}
        <Meter have={r.make?.have || 0} known={r.make?.known || 0} />
      </span>
    </button>
  )
}

function SaveRecipe() {
  const { ai } = useConfig()
  const [src, setSrc] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [draft, setDraft] = useState(null)
  const qc = useQueryClient()
  const toast = useToast()
  const isUrl = /^https?:\/\//.test(src.trim())
  const parse = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await post('/recipes/parse', isUrl ? { url: src.trim() } : { text: src })
      setDraft(r.draft)
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const save = async () => {
    try {
      const r = await post('/recipes', draft)
      qc.invalidateQueries({ queryKey: ['recipes'] })
      toast(`${r.title} saved to your recipes`)
      setDraft(null); setSrc('')
    } catch (e) { setErr(e) }
  }
  return (
    <details className="panel">
      <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Save a recipe</summary>
      <div className="stack" style={{ marginTop: 12 }}>
        {!draft && <>
          <textarea className="textarea" value={src} onChange={(e) => setSrc(e.target.value)} placeholder="Paste a link to a recipe, or the recipe itself" />
          <div className="btn-row">
            <button className="btn btn--ink" onClick={parse} disabled={busy || !src.trim()}>{busy ? 'Reading' : 'Read it'}</button>
            <button className="btn btn--ghost" onClick={() => setDraft({ title: '', ingredients: [], instructions: '', category: 'dinner', status: 'to-try' })}>Type it in</button>
          </div>
          {busy && <p className="note">{isUrl ? 'Reading the page…' : `${ai.extract || 'The model'} is writing it out…`}</p>}
          {!ai.extract && <p className="note">Most recipe sites publish their recipes in a form GrubTrack reads directly. Pasted text needs a model (GT_MODEL_PROVIDER).</p>}
        </>}
        {draft && (
          <div className="stack">
            <label className="field"><span>Title</span><input className="input" value={draft.title || ''} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
            <label className="field"><span>Ingredients, one per line</span>
              <textarea className="textarea" value={(draft.ingredients || []).join('\n')} onChange={(e) => setDraft({ ...draft, ingredients: e.target.value.split('\n').filter(Boolean) })} /></label>
            <label className="field"><span>Instructions</span>
              <textarea className="textarea" style={{ minHeight: 160 }} value={draft.instructions || ''} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} /></label>
            <div className="btn-row"><button className="btn btn--ink" onClick={save} disabled={!draft.title?.trim()}>Save to your recipes</button><button className="btn btn--ghost" onClick={() => setDraft(null)}>Start over</button></div>
          </div>
        )}
        <ErrorBox error={err} />
      </div>
    </details>
  )
}

const FORMATS = '.txt,.mmf,.mm,.mxp,.json,.jsonl,.jsonld,.html,.htm,.zip,.paprikarecipes,.paprikarecipe'

function ImportCollection({ onDone }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const pick = async (file) => {
    if (!file) return
    setBusy(true)
    try {
      const r = await upload('/recipes/import', 'file', file, file.name)
      qc.invalidateQueries({ queryKey: ['recipes'] })
      qc.invalidateQueries({ queryKey: ['library'] })
      toast(`${plural(r.added, 'recipe')} imported${r.duplicates ? `, ${r.duplicates.toLocaleString()} already in the library` : ''}`)
      if (r.added) onDone()
    } catch (e) { toast(e.message) } finally { setBusy(false) }
  }
  return (
    <label className="btn btn--sm" title="Meal-Master, MasterCook, Paprika or Mealie exports, recipe JSON or web pages, or a zip of them">
      <Upload aria-hidden="true" />{busy ? 'Importing' : 'Import recipes'}
      <input type="file" accept={FORMATS} hidden disabled={busy} onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }} />
    </label>
  )
}

export default function Cook() {
  const { open } = useSheet()
  const { recipes } = useConfig()
  const [tab, setTab] = useState('mine')
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [f, setF] = useState({ complexity: '', protein: '', cuisine: '', dish: '' })
  const [cookable, setCookable] = useState(true)
  const [offset, setOffset] = useState(0)
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t) }, [q])
  useEffect(() => setOffset(0), [debounced, f, cookable])

  const mine = useQuery({ queryKey: ['recipes'], queryFn: () => get('/recipes') })
  const params = new URLSearchParams({ offset: String(offset), ...(debounced.trim().length >= 2 ? { q: debounced.trim() } : {}), ...(cookable ? { cookable: 'true' } : {}) })
  for (const [k, v] of Object.entries(f)) if (v) params.set(k, v)
  const lib = useQuery({ queryKey: ['library', params.toString()], queryFn: () => get(`/recipes/bulk/search?${params}`), enabled: tab === 'library' })

  const personal = (mine.data?.personal || [])
    .filter((r) => !q.trim() || `${r.title} ${r.ingredients.join(' ')}`.toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => (b.make?.pct ?? -1) - (a.make?.pct ?? -1) || a.title.localeCompare(b.title))

  return (
    <div>
      <div className="page-head"><h1 className="page-title">Cook</h1></div>
      <p className="lede">Dinner from what's already here. Recipes are ranked by how much of each one is in the house.</p>
      <div className="cols cols--2">
        <MealIdeas />
        {recipes.write && <SaveRecipe />}
      </div>

      <section className="section">
        <div className="toolbar">
          <div className="seg" role="group" aria-label="Recipes">
            <button aria-pressed={tab === 'mine'} onClick={() => setTab('mine')}>Yours{mine.data ? ` (${mine.data.personal.length})` : ''}</button>
            {mine.data?.libraryTotal > 0 && <button aria-pressed={tab === 'library'} onClick={() => setTab('library')}>Library ({mine.data.libraryTotal.toLocaleString()})</button>}
          </div>
          <label className="search">
            <Search aria-hidden="true" /><span className="sr-only">Search recipes</span>
            <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search recipes or ingredients" />
          </label>
          <ImportCollection onDone={() => setTab('library')} />
        </div>
        {tab === 'library' && (
          <div className="toolbar">
            {[['complexity', TIME], ['protein', PROTEIN], ['cuisine', CUISINE], ['dish', DISH]].map(([k, opts]) => (
              <select key={k} className="select" style={{ width: 'auto' }} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} aria-label={k}>
                {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            ))}
            <label className="hstack" style={{ gap: 8, fontWeight: 600, fontSize: 'var(--t-sm)' }}>
              <input type="checkbox" checked={cookable} onChange={(e) => setCookable(e.target.checked)} /> What I can make first
            </label>
          </div>
        )}

        {tab === 'mine' && (mine.isLoading ? <Loading /> : personal.length ? (
          <div className="list list--panel">{personal.map((r) => <RecipeRow key={r.slug} r={r} onOpen={() => open('recipe', r.slug)} />)}</div>
        ) : <Empty title={q.trim() ? 'No recipes match' : 'No recipes yet'}>{q.trim() ? null : 'Save one from a link above, import a collection (Meal-Master, MasterCook, Paprika, Mealie or recipe JSON), or point GT_RECIPES_DIR at a folder of markdown recipes.'}</Empty>)}

        {tab === 'library' && (lib.isLoading ? <Loading label="Searching the library" /> : lib.error ? <ErrorBox error={lib.error} /> : (
          <>
            <p className="note" style={{ marginBottom: 10 }}>
              {plural(lib.data.total, 'recipe')}{cookable ? ' with at least three recognizable ingredients, best match first' : ''}.
            </p>
            <div className={`list list--panel ${lib.isFetching ? 'dim' : ''}`}>
              {lib.data.recipes.map((r) => <RecipeRow key={r.slug} r={r} onOpen={() => open('recipe', r.slug)} />)}
            </div>
            <div className="btn-row" style={{ marginTop: 12 }}>
              <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
              <button className="btn" disabled={offset + 50 >= lib.data.total} onClick={() => setOffset(offset + 50)}>Next 50</button>
            </div>
          </>
        ))}
      </section>
    </div>
  )
}
